"""
notifier.py — Telegram bildirim katmanı
"""

import logging
import time

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

log = logging.getLogger(__name__)


def _markup(link: str) -> InlineKeyboardMarkup:
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("🔗 Aç", url=link))
    return m


def _gonder(bot: telebot.TeleBot, chat_id, metin: str, gorsel_url: str, link: str):
    markup = _markup(link)
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
        log.debug("Görselli gönderim başarısız: %s", exc)
    try:
        bot.send_message(chat_id, metin, parse_mode="HTML", reply_markup=markup)
    except Exception as exc:
        log.error("Mesaj gönderilemedi (chat_id=%s): %s", chat_id, exc)


def yeni_urun_bildir(bot: telebot.TeleBot, chat_id, urun: dict):
    stok = f"\n📦 {urun['stok_adet']} adet" if urun.get("stok_adet") else ""
    metin = (
        f"🆕 <b>{urun['isim']}</b>\n\n"
        f"🏷 <b>{urun['fiyat_str']}</b>{stok}\n"
        f"🏪 Amazon Depo"
    )
    _gonder(bot, chat_id, metin, urun.get("gorsel_url"), urun["link"])
    time.sleep(1)


def fiyat_dustu_bildir(bot: telebot.TeleBot, chat_id, urun: dict, eski_fiyat: float, indirim_orani: int):
    stok = f"\n📦 {urun['stok_adet']} adet" if urun.get("stok_adet") else ""
    metin = (
        f"📉 <b>{urun['isim']}</b>\n\n"
        f"🏷 <b>{urun['fiyat_str']}</b>{stok}\n"
        f"💬 Önceki fiyatın %{indirim_orani} altında\n"
        f"🏪 Amazon Depo"
    )
    _gonder(bot, chat_id, metin, urun.get("gorsel_url"), urun["link"])
    time.sleep(1)
