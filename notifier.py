import logging
import time

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

log = logging.getLogger(__name__)


def _markup(link):
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("Ac", url=link))
    return m


def _gonder(bot, chat_id, metin, gorsel_url, link):
    try:
        if gorsel_url:
            bot.send_photo(chat_id, photo=gorsel_url, caption=metin,
                           parse_mode="HTML", reply_markup=_markup(link))
            return
    except Exception:
        pass
    try:
        bot.send_message(chat_id, metin, parse_mode="HTML", reply_markup=_markup(link))
    except Exception as exc:
        log.error("Mesaj gonderilemedi: %s", exc)


def yeni_urun_bildir(bot, chat_id, urun):
    stok = "\n Stok: " + urun["stok_adet"] + " adet" if urun.get("stok_adet") else ""
    metin = (
        "<b>" + urun["isim"] + "</b>\n\n"
        + urun["fiyat_str"] + stok + "\n"
        "Amazon Depo"
    )
    _gonder(bot, chat_id, metin, urun.get("gorsel_url"), urun["link"])
    time.sleep(1)


def fiyat_dustu_bildir(bot, chat_id, urun, eski_fiyat, indirim):
    stok = "\n Stok: " + urun["stok_adet"] + " adet" if urun.get("stok_adet") else ""
    metin = (
        "FIYAT DUSTU -%" + str(indirim) + "\n\n"
        "<b>" + urun["isim"] + "</b>\n\n"
        "Eski: " + str(round(eski_fiyat, 2)) + " TL\n"
        "Yeni: " + urun["fiyat_str"] + stok + "\n"
        "Amazon Depo"
    )
    _gonder(bot, chat_id, metin, urun.get("gorsel_url"), urun["link"])
    time.sleep(1)
