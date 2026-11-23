# 📥 Telegram Sosyal Medya İndirici Bot (7/24 Bulut & Yerel Çalışmaya Hazır)

Sohbete attığınız **Instagram**, **Twitter (X)** ve **TikTok** linklerindeki video ve fotoğrafları **en yüksek kalitede (son kalite)** indirip doğrudan sohbete gönderen Telegram botu.

---

## ☁️ Bilgisayar Kapalıyken 7/24 Ücretsiz Çalıştırma (Render.com + UptimeRobot)

Proje içinde **FFmpeg kurulu `Dockerfile`** ve **7/24 Keep-Alive Web Sunucusu (`:8080/health`)** hazır olarak ayarlanmıştır. Bilgisayarınız kapalıyken bile ücretsiz çalıştırmak için şu 3 adımı uygulayın:

### Adım 1: Kodu GitHub'a Yükleyin
1. [GitHub](https://github.com/new) üzerinden yeni bir **Private (Gizli)** depo (repository) oluşturun.
2. Bu klasördeki dosyaları GitHub deponuza yükleyin (`.gitignore` sayesinde `.env` şifreniz gizli kalır).

### Adım 2: Render.com'a Bağlayın (Ücretsiz)
1. [Render.com](https://render.com/) adresine GitHub hesabınızla giriş yapın.
2. Sağ üstten **New +** -> **Web Service** seçeneğine tıklayın ve GitHub deponuzu seçin.
3. Ayarları şu şekilde seçin:
   - **Runtime / Environment:** `Docker`
   - **Instance Type:** `Free` (Ücretsiz)
4. **Environment Variables (Ortam Değişkenleri)** kısmına tıklayıp şunları ekleyin:
   - `BOT_TOKEN` = `@BotFather`'dan aldığınız Telegram Bot Token'ı
   - *(İsteğe bağlı)* `COOKIES_CONTENT` = Instagram çerezi kullanmak isterseniz `cookies.txt` dosyasının içindeki metni buraya yapıştırabilirsiniz.
5. **Create Web Service** butonuna basın. Birkaç dakika içinde botunuz bulutta çalışmaya başlayacaktır!

### Adım 3: UptimeRobot ile Uyku Modunu Engelleyin (7/24 Kesintisiz)
Render'ın ücretsiz paketi 15 dakika istek gelmezse uyku moduna geçer. Bunu engellemek için:
1. Render ekranının üst kısmında size verilen site adresini kopyalayın (ör. `https://telegram-media-bot-xxxx.onrender.com`).
2. [UptimeRobot.com](https://uptimerobot.com/) adresinden ücretsiz hesap açın.
3. **+ Add New Monitor** butonuna tıklayın:
   - **Monitor Type:** `HTTP(s)`
   - **Friendly Name:** `Telegram Bot`
   - **URL:** `https://telegram-media-bot-xxxx.onrender.com/health`
   - **Monitoring Interval:** `5 minutes` (5 dakikada bir)
4. Kaydedin. Artık UptimeRobot her 5 dakikada bir botunuzu dürteceği için **bilgisayarınız tamamen kapalı olsa bile botunuz 7/24 hiç uyumadan çalışacaktır!**

---

## 💻 Kendi Bilgisayarınızda Çalıştırmak İsterseniz

1. **FFmpeg Kurun:**
   ```powershell
   winget install Gyan.FFmpeg
   ```
2. **Paketleri Kurun:**
   ```powershell
   cd C:\Users\wakae\.gemini\antigravity\scratch\telegram-media-bot
   pip install -r requirements.txt
   ```
3. `.env.example` dosyasını `.env` olarak kopyalayıp içine `BOT_TOKEN` değerinizi yazın ve başlatın:
   ```powershell
   python bot.py
   ```
