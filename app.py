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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("app")

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

MIN_INDIRIM = 10   # yuzde
ARALIK      = 900  # saniye (15 dakika)

if not TOKEN or not CHAT_ID:
    log.critical("TOKEN veya CHAT_ID eksik!")
    sys.exit(1)


# Kitap tespiti icin anahtar kelimeler
KITAP_KELIMELERI = [
    # Turkce
    "ciltli kapak", "ciltli", "ciltsiz", "kitap",
    "kagit kapak", "kagıt kapak", "kağıt kapak", "karton kapak",
    "tam metin", "roman (", "seri)", "kutu set",
    # Yayinevleri ve format belirteçleri
    "penguin classics", "oxford world", "wordsworth",
    "paperback", "hardcover", "hardback",
    # Amazon kategorisi belirteci
    "(kitaplar)", "| kitap",
]

def kitap_mi(isim, link=""):
    # Yontem 1: ASIN kontrolu (en guvenilir)
    # Kitaplarin ASIN'i ISBN-10 formatinda olup rakamla baslar (0 veya 1)
    # Diger urunlerin ASIN'i "B" harfiyle baslar (ornek: B08N5WRWNW)
    if "/dp/" in link:
        asin = link.split("/dp/")[1].split("?")[0].split("/")[0]
        if asin and asin[0].isdigit():
            return True

    # Yontem 2: Baslik anahtar kelimesi (yedek)
    isim_lower = isim.lower()
    return any(k in isim_lower for k in KITAP_KELIMELERI)


bot = telebot.TeleBot(TOKEN, threaded=False)
ilk_tarama_bitti = False


def kapat(signum, frame):
    log.info("Kapanıyor...")
    try:
        bot.stop_polling()
    except Exception:
        pass
    sys.exit(0)


signal.signal(signal.SIGTERM, kapat)
signal.signal(signal.SIGINT, kapat)


def tara(manuel=False, chat=None):
    global ilk_tarama_bitti
    hedef = chat or CHAT_ID

    if manuel:
        bot.send_message(hedef, "Amazon Depo taranıyor...")

    log.info("Tarama başlıyor (manuel=%s)", manuel)

    try:
        urunler = scraper.tum_sayfalari_tara()
    except Exception as exc:
        log.error("Tarama hatası: %s", exc)
        if manuel:
            bot.send_message(hedef, "Hata: " + str(exc))
        return

    bildirim = 0

    for u in urunler:
        isim  = u["isim"]
        fiyat = u["fiyat"]
        mevcut = db.urun_getir(isim)

        if mevcut is None:
            db.urun_kaydet(isim, fiyat, u["gorsel_url"], u["link"], u["stok_adet"])
            if ilk_tarama_bitti or manuel:
                if not kitap_mi(isim, u.get("link", "")):
                    notifier.yeni_urun_bildir(bot, hedef, u)
                    bildirim += 1
        else:
            eski = mevcut["fiyat"]
            if fiyat < eski:
                indirim = int(((eski - fiyat) / eski) * 100)
                db.urun_kaydet(isim, fiyat, u["gorsel_url"], u["link"], u["stok_adet"])
                db.fiyat_gecmisi_kaydet(isim, eski, fiyat)
                if indirim >= MIN_INDIRIM and not kitap_mi(isim, u.get("link", "")):
                    notifier.fiyat_dustu_bildir(bot, hedef, u, eski, indirim)
                    bildirim += 1
            elif fiyat != eski:
                db.urun_kaydet(isim, fiyat, u["gorsel_url"], u["link"], u["stok_adet"])

    db.ayar_yaz("son_tarama", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if not manuel and not ilk_tarama_bitti:
        ilk_tarama_bitti = True
        bot.send_message(CHAT_ID,
            "Bot aktif! " + str(db.toplam_urun()) + " urun hafizaya alindi. "
            "15 dakikada bir otomatik taranacak."
        )
        return

    if manuel and bildirim == 0:
        bot.send_message(hedef, "Tarama bitti. Yeni urun veya fiyat dususu yok.")


def otomatik():
    while True:
        try:
            tara(manuel=False)
        except Exception as exc:
            log.error("Otomatik hata: %s", exc)
        time.sleep(ARALIK)


@bot.message_handler(commands=["kontrol"])
def cmd_kontrol(message):
    Thread(target=tara, kwargs={"manuel": True, "chat": message.chat.id}).start()


@bot.message_handler(commands=["durum"])
def cmd_durum(message):
    son = db.ayar_oku("son_tarama") or "Henuz yok"
    bot.send_message(message.chat.id,
        "Son tarama: " + son + "\n"
        "Takip edilen: " + str(db.toplam_urun()) + " urun\n"
        "Indirim esigi: %" + str(MIN_INDIRIM) + "\n"
        "Aralik: 15 dakika"
    )


@bot.message_handler(commands=["sifirla"])
def cmd_sifirla(message):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("Evet Sifirla", callback_data="sifirla_evet"),
        telebot.types.InlineKeyboardButton("Iptal", callback_data="sifirla_iptal")
    )
    bot.send_message(message.chat.id,
        "Tum kayitlar silinecek. Bir sonraki taramada bulunan her urun "
        "yeni sayilacak ve bildirim gonderilecek. Emin misin?",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("sifirla_"))
def cb_sifirla(call):
    if call.data == "sifirla_iptal":
        bot.answer_callback_query(call.id, "Iptal.")
        bot.edit_message_text("Iptal edildi.", call.message.chat.id, call.message.message_id)
        return
    global ilk_tarama_bitti
    db.urunleri_sifirla()
    ilk_tarama_bitti = True
    bot.answer_callback_query(call.id, "Sifirlandi!")
    bot.edit_message_text(
        "Sifirlandi. /kontrol ile hemen tarama baslat.",
        call.message.chat.id, call.message.message_id
    )


@bot.message_handler(commands=["yardim", "start"])
def cmd_yardim(message):
    bot.send_message(message.chat.id,
        "Komutlar:\n"
        "/kontrol — Anlik tarama\n"
        "/durum   — Bot durumu\n"
        "/sifirla — Veritabanini sifirla\n"
        "/yardim  — Bu menu"
    )


if __name__ == "__main__":
    log.info("Bot baslatiliyor...")
    db.tablolari_olustur()

    if db.toplam_urun() > 0:
        ilk_tarama_bitti = True
        log.info("DB'de %d urun var, aktif mod.", db.toplam_urun())

    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception:
        pass

    try:
        bot.get_updates(offset=-1)
    except Exception:
        pass

    log.info("20 saniye bekleniyor...")
    time.sleep(20)

    Thread(target=otomatik, daemon=True).start()
    log.info("Tarama dongusu baslatildi.")

    log.info("Polling basliyor...")
    while True:
        try:
            bot.polling(non_stop=False, timeout=20, long_polling_timeout=5,
                        allowed_updates=["message", "callback_query"])
        except telebot.apihelper.ApiTelegramException as exc:
            if "409" in str(exc):
                log.warning("409 — 30s bekleniyor")
                time.sleep(30)
            elif "401" in str(exc):
                log.critical("Gecersiz token!")
                sys.exit(1)
            else:
                time.sleep(5)
        except Exception:
            time.sleep(5)
