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

TOKEN    = os.getenv("TELEGRAM_TOKEN")
CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")

MIN_INDIRIM_ORANI = 10
TARAMA_ARALIGI    = 900

if not TOKEN or not CHAT_ID:
    log.critical("TELEGRAM_TOKEN veya TELEGRAM_CHAT_ID eksik!")
    sys.exit(1)

if not os.getenv("SCRAPERAPI_KEY"):
    log.critical("SCRAPERAPI_KEY eksik!")
    sys.exit(1)

bot = telebot.TeleBot(TOKEN, threaded=False)
ilk_tarama_bitti = False


def temizce_kapat(signum, frame):
    log.info("Kapatma sinyali alindi (%s)", signum)
    try:
        bot.stop_polling()
    except Exception:
        pass
    sys.exit(0)


signal.signal(signal.SIGTERM, temizce_kapat)
signal.signal(signal.SIGINT,  temizce_kapat)


def magazayi_tara(manuel=False, hedef_chat=None):
    global ilk_tarama_bitti
    chat = hedef_chat or CHAT_ID

    if manuel:
        bot.send_message(chat, "Amazon Depo taranıyor, lütfen bekleyin...")

    log.info("Tarama basliyor (manuel=%s)...", manuel)
    basla = datetime.now()

    try:
        urunler = scraper.tum_sayfalari_tara()
    except Exception as exc:
        log.error("Tarama hatasi: %s", exc)
        if manuel:
            bot.send_message(chat, "Tarama sirasinda hata olustu: " + str(exc))
        return

    bildirim_sayisi = 0

    for urun in urunler:
        isim  = urun["isim"]
        fiyat = urun["fiyat"]
        mevcut = db.urun_getir(isim)

        if mevcut is None:
            db.urun_kaydet(isim, fiyat, urun["gorsel_url"], urun["link"], urun["stok_adet"])
            if ilk_tarama_bitti or manuel:
                if db.urun_kategoriye_uyuyor_mu(isim):
                    log.info("YENİ URUN: %.60s @ %.2f TL", isim, fiyat)
                    notifier.yeni_urun_bildir(bot, chat, urun)
                    bildirim_sayisi += 1
        else:
            eski_fiyat = mevcut["fiyat"]
            if fiyat < eski_fiyat:
                indirim = int(((eski_fiyat - fiyat) / eski_fiyat) * 100)
                if indirim >= MIN_INDIRIM_ORANI:
                    log.info("FIYAT DUSTU %%(%d): %.50s  %.2f → %.2f TL", indirim, isim, eski_fiyat, fiyat)
                    db.urun_kaydet(isim, fiyat, urun["gorsel_url"], urun["link"], urun["stok_adet"])
                    db.fiyat_gecmisi_kaydet(isim, eski_fiyat, fiyat)
                    if db.urun_kategoriye_uyuyor_mu(isim):
                        notifier.fiyat_dustu_bildir(bot, chat, urun, eski_fiyat, indirim)
                        bildirim_sayisi += 1
                else:
                    db.urun_kaydet(isim, fiyat, urun["gorsel_url"], urun["link"], urun["stok_adet"])
            elif fiyat > eski_fiyat:
                db.urun_kaydet(isim, fiyat, urun["gorsel_url"], urun["link"], urun["stok_adet"])

    sure = (datetime.now() - basla).seconds
    db.durum_yaz("son_tarama", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    db.durum_yaz("son_sure_sn", str(sure))

    if not manuel and not ilk_tarama_bitti:
        ilk_tarama_bitti = True
        bot.send_message(
            CHAT_ID,
            (
                "Bot aktif!\n\n"
                "Hafizaya alinan urun: " + str(db.toplam_urun_sayisi()) + "\n"
                "Tarama suresi: " + str(sure) + " saniye\n\n"
                "Yeni urunler ve >= %" + str(MIN_INDIRIM_ORANI) + " fiyat dususleri 15 dakikada bir bildirilecek."
            )
        )
        return

    if manuel and bildirim_sayisi == 0:
        bot.send_message(chat, "Tarama tamamlandi. Yeni urun veya fiyat dususu bulunamadi.")


def otomatik_dongu():
    while True:
        try:
            magazayi_tara(manuel=False)
        except Exception as exc:
            log.error("Otomatik tarama hatasi: %s", exc)
        time.sleep(TARAMA_ARALIGI)


# ── Yardimci: kategori menusunu gonder / guncelle ─────────────────────────────

def _kategori_markup():
    aktifler = db.aktif_kategorileri_getir()
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    butonlar = []
    for kategori in db.KATEGORI_LISTESI.keys():
        isaret = "\u2705" if kategori in aktifler else "\u2b1c"
        butonlar.append(
            telebot.types.InlineKeyboardButton(
                isaret + " " + kategori,
                callback_data="kat_" + kategori
            )
        )
    markup.add(*butonlar)
    markup.add(
        telebot.types.InlineKeyboardButton(
            "Filtreyi Temizle (Hepsini Goster)",
            callback_data="kat_temizle"
        )
    )
    return markup


def _kategori_metin():
    aktifler = db.aktif_kategorileri_getir()
    aktif_str = ", ".join(aktifler) if aktifler else "Yok (tum urunler bildiriliyor)"
    return (
        "<b>Kategori Filtresi</b>\n\n"
        "Aktif kategoriler: <b>" + aktif_str + "</b>\n\n"
        "Secili kategorilerdeki urunler bildirilir.\n"
        "Hicbiri secili degilse tum urunler bildirilir."
    )


# ── Komutlar ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["kontrol"])
def cmd_kontrol(message):
    Thread(target=magazayi_tara, kwargs={"manuel": True, "hedef_chat": message.chat.id}).start()


@bot.message_handler(commands=["durum"])
def cmd_durum(message):
    son_tarama = db.durum_oku("son_tarama") or "Henuz tarama yapilmadi"
    son_sure   = db.durum_oku("son_sure_sn")
    sure_metni = son_sure + " saniye" if son_sure else "-"
    aktifler   = db.aktif_kategorileri_getir()
    kat_metni  = ", ".join(aktifler) if aktifler else "Tumu"

    metin = (
        "<b>Bot Durumu</b>\n\n"
        "Son tarama: <b>" + son_tarama + "</b>\n"
        "Tarama suresi: <b>" + sure_metni + "</b>\n"
        "Takip edilen urun: <b>" + str(db.toplam_urun_sayisi()) + "</b>\n"
        "Min. indirim esigi: <b>%" + str(MIN_INDIRIM_ORANI) + "</b>\n"
        "Kategori filtresi: <b>" + kat_metni + "</b>\n"
        "Tarama araligi: <b>15 dakika</b>"
    )
    bot.send_message(message.chat.id, metin, parse_mode="HTML")


@bot.message_handler(commands=["kategori"])
def cmd_kategori(message):
    bot.send_message(
        message.chat.id,
        _kategori_metin(),
        parse_mode="HTML",
        reply_markup=_kategori_markup()
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("kat_"))
def callback_kategori(call):
    secim = call.data[4:]
    aktifler = db.aktif_kategorileri_getir()

    if secim == "temizle":
        db.aktif_kategorileri_kaydet([])
        bot.answer_callback_query(call.id, "Filtre temizlendi.")
    elif secim in aktifler:
        aktifler.remove(secim)
        db.aktif_kategorileri_kaydet(aktifler)
        bot.answer_callback_query(call.id, secim + " kaldirildi.")
    else:
        aktifler.append(secim)
        db.aktif_kategorileri_kaydet(aktifler)
        bot.answer_callback_query(call.id, secim + " eklendi.")

    try:
        bot.edit_message_text(
            _kategori_metin(),
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=_kategori_markup()
        )
    except Exception:
        pass


@bot.message_handler(commands=["istatistik"])
def cmd_istatistik(message):
    son = db.son_guncelleme_zamani() or "-"
    metin = (
        "<b>Istatistikler</b>\n\n"
        "Toplam urun: <b>" + str(db.toplam_urun_sayisi()) + "</b>\n"
        "Son guncelleme: <b>" + son + "</b>"
    )
    bot.send_message(message.chat.id, metin, parse_mode="HTML")


@bot.message_handler(commands=["start", "yardim"])
def cmd_yardim(message):
    metin = (
        "<b>Amazon Depo Bot</b>\n\n"
        "Komutlar:\n"
        "/kontrol — Anlik tarama baslat\n"
        "/kategori — Kategori filtresi\n"
        "/durum — Bot durumunu gor\n"
        "/istatistik — Urun istatistikleri\n"
        "/yardim — Bu menu"
    )
    bot.send_message(message.chat.id, metin, parse_mode="HTML")


# ── Baslangic ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Amazon Depo Botu baslatiliyor...")

    db.tablolari_olustur()

    if db.toplam_urun_sayisi() > 0:
        ilk_tarama_bitti = True
        log.info("Veritabaninda %d urun mevcut — aktif mod.", db.toplam_urun_sayisi())

    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception:
        pass

    try:
        bot.get_updates(offset=-1)
    except Exception:
        pass

    log.info("Telegram session stabilizasyonu icin 20 saniye bekleniyor...")
    time.sleep(20)

    tarama_thread = Thread(target=otomatik_dongu, daemon=True)
    tarama_thread.start()
    log.info("Arka plan tarama dongusu baslatildi.")

    log.info("Telegram polling basliyor...")
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
                log.warning("409 cakismasi — 30 saniye bekleniyor...")
                time.sleep(30)
            elif "401" in str(exc):
                log.critical("Gecersiz TELEGRAM_TOKEN!")
                sys.exit(1)
            else:
                log.warning("Telegram API hatasi: %s", exc)
                time.sleep(5)
        except Exception as exc:
            log.error("Polling hatasi: %s", exc)
            time.sleep(5)
