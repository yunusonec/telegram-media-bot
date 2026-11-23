import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import aiohttp
import yt_dlp

logger = logging.getLogger(__name__)

# Desteklenen platformlar için URL yakalama regex'i
URL_REGEX = re.compile(
    r"(https?://(?:www\.|vm\.|vt\.|m\.)?"
    r"(?:instagram\.com|ddinstagram\.com|kkinstagram\.com|"
    r"twitter\.com|x\.com|fxtwitter\.com|vxtwitter\.com|"
    r"tiktok\.com)"
    r"/[^\s]+)",
    re.IGNORECASE,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


@dataclass
class MediaItem:
    path: Path
    media_type: str  # "photo" veya "video"
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class DownloadResult:
    temp_dir: Path
    items: List[MediaItem] = field(default_factory=list)
    caption: str = ""
    error: Optional[str] = None

    def cleanup(self) -> None:
        """İndirilen geçici klasörü temizler."""
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)


def extract_supported_urls(text: str) -> List[str]:
    """Mesaj içindeki Instagram, Twitter/X ve TikTok linklerini ayıklar."""
    if not text:
        return []
    urls = URL_REGEX.findall(text)
    normalized = []
    for url in urls:
        # Özel yönlendirme domainlerini orijinale çevir
        url = re.sub(r"(?:dd|kk)instagram\.com", "instagram.com", url, flags=re.I)
        url = re.sub(r"(?:fx|vx)twitter\.com", "x.com", url, flags=re.I)
        normalized.append(url)
    return normalized


def detect_platform(url: str) -> str:
    url_lower = url.lower()
    if "instagram.com" in url_lower:
        return "instagram"
    if "twitter.com" in url_lower or "x.com" in url_lower:
        return "twitter"
    if "tiktok.com" in url_lower:
        return "tiktok"
    return "unknown"


def _collect_files_from_dir(directory: Path) -> List[MediaItem]:
    """Klasördeki indirilen fotoğraf ve videoları sıralı şekilde toplar."""
    items: List[MediaItem] = []
    files = sorted(
        (p for p in directory.rglob("*") if p.is_file()),
        key=lambda x: x.name,
    )
    for file_path in files:
        ext = file_path.suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            items.append(MediaItem(path=file_path, media_type="photo"))
        elif ext in VIDEO_EXTENSIONS:
            items.append(MediaItem(path=file_path, media_type="video"))
    return items


async def _download_file(session: aiohttp.ClientSession, url: str, dest: Path) -> bool:
    """Verilen URL'yi diske indirir."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/129.0.0.0 Safari/537.36"
            )
        }
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status == 200:
                data = await resp.read()
                dest.write_bytes(data)
                return True
    except Exception as e:
        logger.warning(f"Dosya indirilemedi ({url}): {e}")
    return False


async def _try_twitter_fx_api(url: str, temp_dir: Path) -> Optional[DownloadResult]:
    """
    Twitter/X gönderileri için FxTwitter API kullanarak çerez gerektirmeden
    orijinal kalitede (name=orig) fotoğraf ve en yüksek bitrate videoları indirir.
    """
    match = re.search(r"(?:twitter\.com|x\.com)/([^/]+)/status/(\d+)", url, re.I)
    if not match:
        return None

    user, status_id = match.group(1), match.group(2)
    api_url = f"https://api.fxtwitter.com/{user}/status/{status_id}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            tweet = data.get("tweet") or {}
            media_obj = tweet.get("media") or {}
            all_media = media_obj.get("all") or []
            if not all_media:
                return None

            items: List[MediaItem] = []
            for idx, m in enumerate(all_media, start=1):
                m_type = m.get("type")
                m_url = m.get("url")
                if not m_url:
                    continue

                if m_type == "photo":
                    # Twitter fotoğrafını orijinal sıkıştırmasız boyutta (name=orig) al
                    orig_url = m_url
                    if "pbs.twimg.com" in orig_url and "name=" not in orig_url:
                        sep = "&" if "?" in orig_url else "?"
                        orig_url = f"{orig_url}{sep}name=orig"
                    dest = temp_dir / f"tw_{idx:02d}.jpg"
                    if await _download_file(session, orig_url, dest):
                        items.append(MediaItem(path=dest, media_type="photo"))
                elif m_type in ("video", "gif"):
                    dest = temp_dir / f"tw_{idx:02d}.mp4"
                    if await _download_file(session, m_url, dest):
                        items.append(
                            MediaItem(
                                path=dest,
                                media_type="video",
                                width=m.get("width"),
                                height=m.get("height"),
                            )
                        )

            if items:
                caption = tweet.get("text", "")[:800]
                return DownloadResult(temp_dir=temp_dir, items=items, caption=caption)
    except Exception as e:
        logger.warning(f"FxTwitter API hatası: {e}")

    return None


async def _try_tiktok_tikwm_api(url: str, temp_dir: Path) -> Optional[DownloadResult]:
    """
    TikTok fotoğraf slayt gösterileri (Photo Mode) ve filigransız HD videolar için yedek API.
    """
    api_url = "https://www.tikwm.com/api/"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                api_url,
                data={"url": url, "hd": 1},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json()

            if payload.get("code") != 0 or not payload.get("data"):
                return None

            data = payload["data"]
            items: List[MediaItem] = []
            images = data.get("images")

            # TikTok Fotoğraf Slaytı ise tüm fotoğrafları en yüksek kalitede indir
            if images and isinstance(images, list):
                for idx, img_url in enumerate(images, start=1):
                    dest = temp_dir / f"tt_photo_{idx:02d}.jpg"
                    if await _download_file(session, img_url, dest):
                        items.append(MediaItem(path=dest, media_type="photo"))
            else:
                # Filigransız HD video
                video_url = data.get("hdplay") or data.get("play")
                if video_url:
                    if video_url.startswith("/"):
                        video_url = f"https://www.tikwm.com{video_url}"
                    dest = temp_dir / "tt_video.mp4"
                    if await _download_file(session, video_url, dest):
                        items.append(MediaItem(path=dest, media_type="video"))

            if items:
                caption = data.get("title", "")[:800]
                return DownloadResult(temp_dir=temp_dir, items=items, caption=caption)
    except Exception as e:
        logger.warning(f"TikWM API hatası: {e}")

    return None


def _run_yt_dlp(url: str, temp_dir: Path, cookies_file: Optional[str]) -> Optional[str]:
    """yt-dlp ile en yüksek kalitede video indirir."""
    outtmpl = str(temp_dir / "ytdlp_%(autonumber)02d.%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        # En yüksek video + en yüksek ses kalitesini MP4 olarak birleştir
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "concurrent_fragment_downloads": 4,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/129.0.0.0 Safari/537.36"
            )
        },
    }

    if cookies_file and os.path.exists(cookies_file):
        ydl_opts["cookiefile"] = cookies_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = ""
            if isinstance(info, dict):
                title = info.get("description") or info.get("title") or ""
            return title[:800]
    except Exception as e:
        logger.warning(f"yt-dlp indirme hatası ({url}): {e}")
        return None


def _run_gallery_dl(url: str, temp_dir: Path, cookies_file: Optional[str]) -> bool:
    """gallery-dl ile çoklu fotoğraf/video (carousel) gönderilerini orijinal kalitede indirir."""
    cmd = [
        sys.executable,
        "-m",
        "gallery_dl",
        "--directory",
        str(temp_dir),
        "--filename",
        "gdl_{num:>02}.{extension}",
        "--no-mtime",
    ]
    if cookies_file and os.path.exists(cookies_file):
        cmd.extend(["--cookies", cookies_file])

    cmd.append(url)
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90,
        )
        return res.returncode == 0
    except Exception as e:
        logger.warning(f"gallery-dl hatası ({url}): {e}")
        return False


async def download_media(url: str, cookies_file: Optional[str] = None) -> DownloadResult:
    """
    Verilen URL'deki video veya fotoğrafları en yüksek kalitede indirir.
    Platforma göre en uygun indirme motorunu (yt-dlp, gallery-dl, özel API'ler) seçer.
    """
    temp_dir = Path(tempfile.gettempdir()) / f"tg_media_{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    platform = detect_platform(url)

    # 1. TWITTER / X: Önce FxTwitter API (orijinal kalite fotoğraf & video, çerezsiz çalışır)
    if platform == "twitter":
        fx_res = await _try_twitter_fx_api(url, temp_dir)
        if fx_res and fx_res.items:
            return fx_res

        # Yedek: gallery-dl ve yt-dlp
        await asyncio.to_thread(_run_gallery_dl, url, temp_dir, cookies_file)
        items = _collect_files_from_dir(temp_dir)
        if items:
            return DownloadResult(temp_dir=temp_dir, items=items)

        caption = await asyncio.to_thread(_run_yt_dlp, url, temp_dir, cookies_file)
        items = _collect_files_from_dir(temp_dir)
        if items:
            return DownloadResult(temp_dir=temp_dir, items=items, caption=caption or "")

    # 2. TIKTOK: Fotoğraf slaytı mı yoksa video mu kontrol et
    elif platform == "tiktok":
        if "/photo/" in url.lower():
            # TikTok fotoğraf slaytı için önce TikWM veya gallery-dl
            tt_res = await _try_tiktok_tikwm_api(url, temp_dir)
            if tt_res and tt_res.items:
                return tt_res
            await asyncio.to_thread(_run_gallery_dl, url, temp_dir, cookies_file)
            items = _collect_files_from_dir(temp_dir)
            if items:
                return DownloadResult(temp_dir=temp_dir, items=items)

        # TikTok Video için önce yt-dlp, başarısız olursa veya fotoğraf çıkarsa TikWM API
        caption = await asyncio.to_thread(_run_yt_dlp, url, temp_dir, cookies_file)
        items = _collect_files_from_dir(temp_dir)
        if items:
            return DownloadResult(temp_dir=temp_dir, items=items, caption=caption or "")

        tt_res = await _try_tiktok_tikwm_api(url, temp_dir)
        if tt_res and tt_res.items:
            return tt_res

    # 3. INSTAGRAM (Reels, Tekli Fotoğraf, Çoklu Kaydırmalı Carousel)
    elif platform == "instagram":
        is_reel = any(k in url.lower() for k in ("/reel/", "/reels/", "/tv/"))

        if is_reel:
            # Reels için önce yt-dlp (en yüksek video+ses), olmazsa gallery-dl
            caption = await asyncio.to_thread(_run_yt_dlp, url, temp_dir, cookies_file)
            items = _collect_files_from_dir(temp_dir)
            if items:
                return DownloadResult(temp_dir=temp_dir, items=items, caption=caption or "")

            await asyncio.to_thread(_run_gallery_dl, url, temp_dir, cookies_file)
            items = _collect_files_from_dir(temp_dir)
            if items:
                return DownloadResult(temp_dir=temp_dir, items=items)
        else:
            # Normal post (/p/) fotoğraf veya çoklu kaydırmalı (carousel) olabileceği için önce gallery-dl
            await asyncio.to_thread(_run_gallery_dl, url, temp_dir, cookies_file)
            items = _collect_files_from_dir(temp_dir)
            if items:
                return DownloadResult(temp_dir=temp_dir, items=items)

            # Eğer gallery-dl çerez istediği için indiremediyse yt-dlp dene
            caption = await asyncio.to_thread(_run_yt_dlp, url, temp_dir, cookies_file)
            items = _collect_files_from_dir(temp_dir)
            if items:
                return DownloadResult(temp_dir=temp_dir, items=items, caption=caption or "")

    return DownloadResult(
        temp_dir=temp_dir,
        items=[],
        error=(
            "Medya indirilemedi. Gönderi gizli olabilir veya Instagram/Twitter "
            "giriş çerezi (cookies.txt) gerektiriyor olabilir."
        ),
    )
