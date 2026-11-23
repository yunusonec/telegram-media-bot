import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, List, Set

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command
from aiogram.types import (
    FSInputFile,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from dotenv import load_dotenv

from downloader import MediaItem, download_media, extract_supported_urls

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("telegram_media_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
COOKIES_FILE = os.getenv("COOKIES_FILE", "cookies.txt").strip()
COOKIES_CONTENT = os.getenv("COOKIES_CONTENT", "").strip()
DEFAULT_SEND_DOC = os.getenv("SEND_ORIG_AS_DOCUMENT", "false").lower() == "true"
PORT = int(os.getenv("PORT", "8080"))

# Bulut sunucularda (Render/Koyeb) cookies.txt dosyasını ortam değişkeninden otomatik oluşturma
if COOKIES_CONTENT and not Path(COOKIES_FILE).exists():
    try:
        Path(COOKIES_FILE).write_text(COOKIES_CONTENT, encoding="utf-8")
        logger.info(f"COOKIES_CONTENT ortam değişkeninden {COOKIES_FILE} oluşturuldu.")
    except Exception as e:
        logger.warning(f"Çerez dosyası yazılamadı: {e}")

# İsteğe bağlı kullanıcı kısıtlaması
ALLOWED_USER_IDS: Set[int] = set()
raw_allowed = os.getenv("ALLOWED_USER_IDS", "").strip()
if raw_allowed:
    for uid in raw_allowed.split(","):
        uid = uid.strip()
        if uid.isdigit():
            ALLOWED_USER_IDS.add(int(uid))

# Sohbet bazlı "Fotoğrafları sıkıştırmasız dosya (belge) olarak da gönder" ayarı
chat_doc_mode: Dict[int, bool] = {}

MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024  # 50 MB (Standart Bot API sınırı)

dp = Dispatcher()


def is_user_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def chunk_list(items: List[MediaItem], size: int = 10) -> List[List[MediaItem]]:
    """Telegram albümleri (MediaGroup) en fazla 10 medya alabildiği için listeyi böler."""
    return [items[i : i + size] for i in range(0, len(items), size)]


@dp.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    if not message.from_user or not is_user_allowed(message.from_user.id):
        return

    doc_enabled = chat_doc_mode.get(message.chat.id, DEFAULT_SEND_DOC)
    durum = (
        "Açık (Fotoğraflar ayrıca ham dosya olarak da atılır)"
        if doc_enabled
        else "Kapalı (Direkt medya olarak atılır)"
    )

    text = (
        "👋 **Merhaba! Sosyal Medya İndirici Botuna Hoş Geldin.**\n\n"
        "Bana **Instagram**, **Twitter (X)** veya **TikTok** linki gönderdiğinde "
        "içindeki tüm video ve fotoğrafları **en yüksek kalitede** direkt sohbete atarım.\n\n"
        "✅ **Desteklenenler:**\n"
        "• Instagram Reels, Gönderi ve Çoklu Kaydırmalı (Carousel) Fotoğraf/Videolar\n"
        "• Twitter / X Videoları, GIF'ler ve Çoklu Fotoğraflar (`orig` tam çözünürlük)\n"
        "• TikTok Filigransız HD Videolar ve Fotoğraf Slaytları\n\n"
        f"⚙️ **Ham Dosya (Sıkıştırmasız Fotoğraf) Modu:** `{durum}`\n"
        "Değiştirmek için /kalite yazabilirsin."
    )
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)


@dp.message(Command("kalite"))
async def cmd_toggle_quality(message: Message) -> None:
    if not message.from_user or not is_user_allowed(message.from_user.id):
        return

    current = chat_doc_mode.get(message.chat.id, DEFAULT_SEND_DOC)
    new_val = not current
    chat_doc_mode[message.chat.id] = new_val

    if new_val:
        await message.answer(
            "✅ **Ham Dosya Modu Açıldı!**\n"
            "Videolar zaten Telegram tarafından sıkıştırılmadan orijinal MP4 olarak oynatılır. "
            "Fotoğraflar ise hem sohbete normal görsel olarak hem de Telegram sıkıştırması olmadan "
            "**orijinal ham dosya (belge)** olarak gönderilecek.",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await message.answer(
            "🖼️ **Standart Sohbet Modu Açıldı!**\n"
            "Tüm video ve fotoğraflar doğrudan sohbette görüntülenecek şekilde gönderilecek.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def send_downloaded_media(message: Message, items: List[MediaItem], send_as_doc: bool) -> None:
    """İndirilen fotoğraf ve videoları tekli veya albüm (MediaGroup) halinde gönderir."""
    valid_items: List[MediaItem] = []
    for item in items:
        if not item.path.exists():
            continue
        size = item.path.stat().st_size
        if size > MAX_TELEGRAM_FILE_SIZE:
            await message.reply(
                f"⚠️ `{item.path.name}` dosyası 50 MB'dan büyük ({size / (1024*1024):.1f} MB) "
                "olduğu için standart Telegram Bot API sınırını aşıyor.",
                parse_mode=ParseMode.MARKDOWN,
            )
            continue
        valid_items.append(item)

    if not valid_items:
        return

    # 1. Direkt sohbette oynatılabilir/görüntülenebilir olarak gönder
    if len(valid_items) == 1:
        item = valid_items[0]
        input_file = FSInputFile(item.path)
        if item.media_type == "video":
            await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_VIDEO)
            await message.reply_video(
                video=input_file,
                supports_streaming=True,
                width=item.width,
                height=item.height,
            )
        else:
            await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
            await message.reply_photo(photo=input_file)
    else:
        # Çoklu medya (Carousel / Albüm) -> 10'arlı gruplar halinde gönder
        for batch in chunk_list(valid_items, 10):
            media_group = []
            for item in batch:
                input_file = FSInputFile(item.path)
                if item.media_type == "video":
                    media_group.append(
                        InputMediaVideo(
                            media=input_file,
                            supports_streaming=True,
                            width=item.width,
                            height=item.height,
                        )
                    )
                else:
                    media_group.append(InputMediaPhoto(media=input_file))

            await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
            await message.reply_media_group(media=media_group)

    # 2. Eğer kullanıcı /kalite ile ham fotoğraf belge modunu açtıysa fotoğrafları sıkıştırmasız dosya olarak da at
    if send_as_doc:
        photo_items = [i for i in valid_items if i.media_type == "photo"]
        if len(photo_items) == 1:
            await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_DOCUMENT)
            await message.reply_document(
                document=FSInputFile(photo_items[0].path),
                caption="📄 Orijinal sıkıştırmasız fotoğraf dosyası",
            )
        elif len(photo_items) > 1:
            for batch in chunk_list(photo_items, 10):
                doc_group = [InputMediaDocument(media=FSInputFile(i.path)) for i in batch]
                await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_DOCUMENT)
                await message.reply_media_group(media=doc_group)


@dp.message(F.text)
async def handle_text_links(message: Message) -> None:
    if not message.from_user or not is_user_allowed(message.from_user.id):
        return

    urls = extract_supported_urls(message.text or "")
    if not urls:
        return

    send_as_doc = chat_doc_mode.get(message.chat.id, DEFAULT_SEND_DOC)

    for url in urls:
        status_msg = await message.reply("⏳ Medya en yüksek kalitede indiriliyor, lütfen bekleyin...")
        result = await download_media(url, cookies_file=COOKIES_FILE)
        try:
            if not result.items:
                err_text = result.error or "Medya bulunamadı veya indirilemedi."
                await status_msg.edit_text(f"❌ {err_text}")
                continue

            await status_msg.edit_text(f"📤 {len(result.items)} medya sohbete yükleniyor...")
            await send_downloaded_media(message, result.items, send_as_doc=send_as_doc)
            await status_msg.delete()
        except Exception as e:
            logger.exception(f"Medya gönderilirken hata oluştu ({url}): {e}")
            await status_msg.edit_text(f"❌ Gönderim sırasında bir hata oluştu: {e}")
        finally:
            result.cleanup()


async def handle_healthcheck(request: web.Request) -> web.Response:
    """Render, Koyeb ve UptimeRobot için 7/24 aktiflik kontrol endpoint'i."""
    return web.Response(text="Bot 7/24 Aktif ve Calisiyor!", status=200)


async def start_keep_alive_server() -> web.AppRunner:
    """Bulut sunucunun uyku moduna geçmemesi için hafif HTTP sunucusu başlatır."""
    app = web.Application()
    app.router.add_get("/", handle_healthcheck)
    app.router.add_get("/health", handle_healthcheck)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info(f"7/24 Keep-Alive Web Sunucusu 0.0.0.0:{PORT} portunda başlatıldı.")
    return runner


async def main() -> None:
    if not BOT_TOKEN or "ABCdef" in BOT_TOKEN:
        logger.error("Lütfen .env dosyasına veya sunucu ortam değişkenlerine geçerli bir BOT_TOKEN girin!")
        return

    runner = await start_keep_alive_server()
    bot = Bot(token=BOT_TOKEN)
    try:
        # Önceki webhook kalıntılarını temizle ve polling başlat
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Telegram Bot başlatıldı ve 7/24 mesajları dinliyor...")
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot durduruldu.")
