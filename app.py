"""
app.py — Amazon Depo Telegram Botu — Ana Giriş Noktası

Mimari:
  • database.py  → SQLite kalıcı depolama
  • scraper.py   → ScraperAPI üzerinden Amazon çekme (URL encode + render=true)
  • notifier.py  → Telegram mesaj gönderme

Komutlar:
  /kontrol    → Anlık manuel tarama başlatır
  /durum      → Bot sağlık durumunu ve son tarama zamanını gösterir
  /istatistik → Veritabanındaki ürün sayısı ve fiyat düşüşü istatistikleri

Uyarı:
  MIN_INDIRIM_ORANI = 10  → Sadece %10 ve üzeri düşüşler bildirilir
"""

import os
import sys
import time
import signal
import logging
from datetime import datetime
from threading import Thread

import telebot

import database as db
import scraper
import notifier

# ── Loglama ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("app")

# ── Ortam değişkenleri ────────────────────────────────────────────────────────

TOKEN       = os.getenv("TELEGRAM_TOKEN")
CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID")

MIN_INDIRIM_ORANI = 10   # % — bu oranın altındaki düşüşler sessizce geçilir
TARAMA_ARALIGI    = 900  # saniye — 15 dakika

if not TOKEN or not CHAT_ID:
    log.critical("TELEGRAM_TOKEN veya TELEGRAM_CHAT_ID eksik!")
    sys.exit(1)

if not os.getenv("SCRAPERAPI_KEY"):
    log.critical("SCRAPERAPI_KEY eksik!")
    sys.exit(1)

# ── Bot nesnesi ───────────────────────────────────────────────────────────────

bot = telebot.TeleBot(TOKEN, threaded=False)

# ── Durum bayrağı ─────────────────────────────────────────────────────────────
# İlk taramada hafıza sessizce doldurulur; mesaj gönderilmez.
# Bu, Railway yeniden başlatmalarında SQLite'dan yüklenen mevcut
# ürünlerin "yeni" sayılmasını önler.

ilk_tarama_bitti = False


# ── Kapatma sinyalleri ────────────────────────────────────────────────────────

def temizce_kapat(signum, frame):
    log.info("Kapatma sinyali alındı (%s), bot durduruluyor...", signum)
    try:
        bot.stop_polling()
    except Exception:
        pass
    sys.exit(0)


signal.signal(signal.SIGTERM, temizce_kapat)
signal.signal(signal.SIGINT,  temizce_kapat)


# ── Ana tarama fonksiyonu ─────────────────────────────────────────────────────

def magazayi_tara(manuel: bool = False, hedef_chat=None):
    """
    Tüm Amazon Depo sayfalarını tarar, yeni ürün ve fiyat düşüşlerini bildirir.

    manuel=True  → /kontrol komutuyla kullanıcı tetikledi; mesajlar hedef_chat'e gider.
    manuel=False → Arka plan zamanlayıcısı tetikledi; mesajlar CHAT_ID'ye gider.
    """
    global ilk_tarama_bitti
    chat = hedef_chat or CHAT_ID

    if manuel:
        bot.send_message(chat, "🔍 Amazon Depo taranıyor, lütfen bekleyin...")

    log.info("Tarama başlıyor (manuel=%s)...", manuel)
    basla = datetime.now()

    try:
        urunler = scraper.tum_sayfalari_tara()
    except Exception as exc:
        log.error("Tarama sırasında kritik hata: %s", exc)
        if manuel:
            bot.send_message(chat, f"❌ Tarama sırasında hata oluştu:\n<code>{exc}</code>", parse_mode="HTML")
        return

    bildirim_sayisi = 0

    for urun in urunler:
        isim  = urun["isim"]
        fiyat = urun["fiyat"]

        mevcut = db.urun_getir(isim)

        if mevcut is None:
            # ── Veritabanında hiç yok → yeni ürün ───────────────────────────
            db.urun_kaydet(isim, fiyat, urun["gorsel_url"], urun["link"], urun["stok_adet"])

            if ilk_tarama_bitti or manuel:
                log.info("YENİ ÜRÜN: %.60s @ %.2f TL", isim, fiyat)
                notifier.yeni_urun_bildir(bot, chat, urun)
                bildirim_sayisi += 1

        else:
            # ── Zaten var → fiyat kontrolü ──────────────────────────────────
            eski_fiyat = mevcut["fiyat"]

            if fiyat < eski_fiyat:
                indirim = int(((eski_fiyat - fiyat) / eski_fiyat) * 100)

                if indirim >= MIN_INDIRIM_ORANI:
                    log.info(
                        "FİYAT DÜŞTÜ %%(%d): %.50s  %.2f → %.2f TL",
                        indirim, isim, eski_fiyat, fiyat
                    )
                    db.urun_kaydet(isim, fiyat, urun["gorsel_url"], urun["link"], urun["stok_adet"])
                    db.fiyat_gecmisi_kaydet(isim, eski_fiyat, fiyat)
                    notifier.fiyat_dustu_bildir(bot, chat, urun, eski_fiyat, indirim)
                    bildirim_sayisi += 1
                else:
                    # Eşiğin altında düşüş — sessizce fiyatı güncelle
                    db.urun_kaydet(isim, fiyat, urun["gorsel_url"], urun["link"], urun["stok_adet"])
            elif fiyat > eski_fiyat:
                # Fiyat yükseldi — sadece kayıt güncelle, bildirim yok
                db.urun_kaydet(isim, fiyat, urun["gorsel_url"], urun["link"], urun["stok_adet"])

    sure = (datetime.now() - basla).seconds
    db.durum_yaz("son_tarama", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    db.durum_yaz("son_sure_sn", str(sure))

    # İlk otomatik tarama tamamlandı
    if not manuel and not ilk_tarama_bitti:
        ilk_tarama_bitti = True
        bot.send_message(
            CHAT_ID,
            f"✅ <b>Bot aktif!</b>\n\n"
            f"📦 Hafızaya alınan ürün sayısı: <b>{db.toplam_urun_sayisi()}</b>\n"
            f"⏱ Tarama süresi: <b>{sure} saniye</b>\n\n"
            f"Yeni ürünler ve ≥%{MIN_INDIRIM_ORANI} fiyat düşüşleri 15 dakikada bir bildirilecek.",
            parse_mode="HTML"
        )
        return

    if manuel and bildirim_sayisi == 0:
        bot.send_message(
            chat,
            "✅ Tarama tamamlandı. Son kontrole göre yeni ürün veya "
            f"≥%{MIN_INDIRIM_ORANI} fiyat düşüşü bulunamadı."
        )


# ── Arka plan zamanlayıcısı ───────────────────────────────────────────────────

def otomatik_dongu():
    while True:
        try:
            magazayi_tara(manuel=False)
        except Exception as exc:
            log.error("Otomatik taramada beklenmeyen hata: %s", exc)
        time.sleep(TARAMA_ARALIGI)


# ── Telegram komutları ────────────────────────────────────────────────────────

@bot.message_handler(commands=["kontrol"])
def cmd_kontrol(message):
    """Manuel tarama başlatır."""
    Thread(target=magazayi_tara, kwargs={"manuel": True, "hedef_chat": message.chat.id}).start()


@bot.message_handler(commands=["durum"])
def cmd_durum(message):
    """Bot sağlık durumunu gösterir."""
    son_tarama = db.durum_oku("son_tarama") or "Henüz tarama yapılmadı"
    son_sure   = db.durum_oku("son_sure_sn")
    sure_metni = f"{son_sure} saniye" if son_sure else "—"

    metin = (
        "🤖 <b>Bot Durumu</b>\n\n"
        f"📅 Son tarama: <b>{son_tarama}</b>\n"
        f"⏱ Tarama süresi: <b>{sure_metni}</b>\n"
        f"📦 Takip edilen ürün: <b>{db.toplam_urun_sayisi()}</b>\n"
        f"🔔 Min. indirim eşiği: <b>%{MIN_INDIRIM_ORANI}</b>\n"
        f"⏰ Tarama aralığı: <b>15 dakika</b>"
    )
    bot.send_message(message.chat.id, metin, parse_mode="HTML")


@bot.message_handler(commands=["istatistik"])
def cmd_istatistik(message):
    """Fiyat düşüşü istatistiklerini gösterir."""
    toplam = db.toplam_urun_sayisi()
    son    = db.son_guncelleme_zamani() or "—"

    metin = (
        "📊 <b>İstatistikler</b>\n\n"
        f"📦 Toplam ürün: <b>{toplam}</b>\n"
        f"🕐 Son güncelleme: <b>{son}</b>"
    )
    bot.send_message(message.chat.id, metin, parse_mode="HTML")


@bot.message_handler(commands=["start", "yardim"])
def cmd_yardim(message):
    metin = (
        "🛒 <b>Amazon Depo Bot</b>\n\n"
        "Komutlar:\n"
        "/kontrol — Anlık tarama başlat\n"
        "/durum — Bot durumunu gör\n"
        "/istatistik — Ürün istatistikleri\n"
        "/yardim — Bu menü"
    )
    bot.send_message(message.chat.id, metin, parse_mode="HTML")


# ── Başlangıç ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Amazon Depo Botu başlatılıyor...")

    # 1. Veritabanı tablolarını hazırla
    db.tablolari_olustur()

    # Veritabanında zaten ürün varsa → daha önce çalışmış demektir.
    # Sessiz ilk tarama modunu atlayıp doğrudan aktif moda geç.
    if db.toplam_urun_sayisi() > 0:
        ilk_tarama_bitti = True
        log.info("Veritabanında %d ürün mevcut — aktif mod.", db.toplam_urun_sayisi())

    # 2. Varsa eski webhook'u temizle
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception:
        pass

    # 3. Telegram kuyruğunu temizle (eski instance'ı kapat)
    try:
        bot.get_updates(offset=-1)
    except Exception:
        pass

    # 4. Eski container'ın Telegram bağlantısını kesmesi için bekle
    log.info("Telegram session stabilizasyonu için 20 saniye bekleniyor...")
    time.sleep(20)

    # 5. Arka plan tarama döngüsünü başlat
    tarama_thread = Thread(target=otomatik_dongu, daemon=True)
    tarama_thread.start()
    log.info("Arka plan tarama döngüsü başlatıldı.")

    # 6. Telegram polling döngüsü
    log.info("Telegram polling başlıyor...")
    while True:
        try:
            bot.polling(
                non_stop=False,
                timeout=20,
                long_polling_timeout=5,
                allowed_updates=["message", "callback_query"],
            )
        except telebot.apihelper.ApiTelegramException as exc:
            if "409" in str(exc):
                log.warning("409 çakışması — 30 saniye bekleniyor...")
                time.sleep(30)
            elif "401" in str(exc):
                log.critical("Geçersiz TELEGRAM_TOKEN! Bot durduruluyor.")
                sys.exit(1)
            else:
                log.warning("Telegram API hatası: %s", exc)
                time.sleep(5)
        except Exception as exc:
            log.error("Polling hatası: %s", exc)
            time.sleep(5)
