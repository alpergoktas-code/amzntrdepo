"""
notifier.py — Telegram bildirim katmanı

Yeni ürün ve fiyat düşüşü bildirimlerini gönderir.
Görselli ve görselsiz mesaj formatlarını yönetir.
"""

import logging
import time

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

log = logging.getLogger(__name__)


def _markup_olustur(link: str) -> InlineKeyboardMarkup:
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("🔗 Amazon'da Aç", url=link))
    return m


def _yeni_urun_metni(urun: dict) -> str:
    stok = f"📦 <b>Stok:</b> {urun['stok_adet']} adet\n" if urun.get("stok_adet") else ""
    return (
        "🆕 <b>YENİ DEPO ÜRÜNÜ</b>\n\n"
        f"<b>{urun['isim']}</b>\n\n"
        f"🏷 <b>Fiyat: {urun['fiyat_str']}</b>\n"
        f"{stok}"
        "🏪 Amazon Depo"
    )


def _fiyat_dustu_metni(urun: dict, eski_fiyat: float, indirim_orani: int) -> str:
    stok = f"📦 <b>Stok:</b> {urun['stok_adet']} adet\n" if urun.get("stok_adet") else ""
    return (
        f"📉 <b>FİYAT DÜŞTÜ — %{indirim_orani} İNDİRİM</b>\n\n"
        f"<b>{urun['isim']}</b>\n\n"
        f"💸 <b>Eski:</b> <s>{eski_fiyat:,.2f} TL</s>\n"
        f"✅ <b>Yeni: {urun['fiyat_str']}</b>\n"
        f"{stok}"
        "🏪 Amazon Depo"
    )


def _mesaj_gonder(bot: telebot.TeleBot, chat_id, metin: str, gorsel_url: str, link: str):
    """Görselli gönderimi dener, başarısız olursa düz mesaj atar."""
    markup = _markup_olustur(link)
    try:
        if gorsel_url:
            bot.send_photo(
                chat_id,
                photo=gorsel_url,
                caption=metin,
                parse_mode="HTML",
                reply_markup=markup,
            )
            return
    except Exception as exc:
        log.debug("Görselli mesaj gönderilemedi: %s — düz mesaja geçiliyor", exc)

    try:
        bot.send_message(chat_id, metin, parse_mode="HTML", reply_markup=markup)
    except Exception as exc:
        log.error("Mesaj gönderilemedi (chat_id=%s): %s", chat_id, exc)


def yeni_urun_bildir(bot: telebot.TeleBot, chat_id, urun: dict):
    metin = _yeni_urun_metni(urun)
    _mesaj_gonder(bot, chat_id, metin, urun.get("gorsel_url"), urun["link"])
    time.sleep(1)   # Telegram rate limit koruması


def fiyat_dustu_bildir(bot: telebot.TeleBot, chat_id, urun: dict, eski_fiyat: float, indirim_orani: int):
    metin = _fiyat_dustu_metni(urun, eski_fiyat, indirim_orani)
    _mesaj_gonder(bot, chat_id, metin, urun.get("gorsel_url"), urun["link"])
    time.sleep(1)
