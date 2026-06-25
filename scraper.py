import os
import re
import time
import logging
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

SCRAPER_KEY = os.getenv("SCRAPERAPI_KEY")

BASE_URL = (
    "https://www.amazon.com.tr/Amazon-Depo/s"
    "?i=warehouse-deals&srs=44219324031&bbn=44219324031"
    "&rh=n%3A44219324031&fs=true"
)

SAYFA_BEKLEME  = 3
ISTEK_TIMEOUT  = 90
MAX_BOSH_SAYFA = 2


def _scraper_url(hedef_url, render=False):
    encoded = quote(hedef_url, safe="")
    url = (
        "http://api.scraperapi.com"
        "?api_key=" + SCRAPER_KEY +
        "&url=" + encoded +
        "&country_code=tr&device_type=desktop"
    )
    if render:
        url += "&render=true"
    return url


# Direkt istek icin tarayici gibi gorunen headers
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _urun_div_var_mi(yanit):
    """Gelen HTML'de urun div'i var mi kontrol et."""
    soup = BeautifulSoup(yanit.content, "html.parser")
    return bool(soup.find("div", {"data-component-type": "s-search-result"}))


def _sayfa_indir(sayfa_no):
    hedef = BASE_URL + "&page=" + str(sayfa_no)

    # 1. Direkt istek (kredi harcamaz)
    try:
        yanit = requests.get(hedef, headers=HEADERS, timeout=30)
        log.info("Sayfa %d [direkt] — HTTP %d, %d byte",
                 sayfa_no, yanit.status_code, len(yanit.content))
        if yanit.status_code == 200 and _urun_div_var_mi(yanit):
            return yanit
        log.info("Sayfa %d direkt calismiyor, ScraperAPI deneniyor...", sayfa_no)
    except Exception as exc:
        log.warning("Sayfa %d direkt hata: %s", sayfa_no, exc)

    # 2. ScraperAPI (kredi harcar, sadece gerekirse)
    if not SCRAPER_KEY:
        log.warning("SCRAPERAPI_KEY tanimli degil, sayfa atlaniyor.")
        return None

    for render in (False, True):
        mod = "render=true" if render else "render=false"
        try:
            yanit = requests.get(_scraper_url(hedef, render=render), timeout=ISTEK_TIMEOUT)
            log.info("Sayfa %d [scraper/%s] — HTTP %d, %d byte",
                     sayfa_no, mod, yanit.status_code, len(yanit.content))
            if yanit.status_code == 200 and _urun_div_var_mi(yanit):
                return yanit
            if not render:
                log.info("Sayfa %d render=true deneniyor...", sayfa_no)
        except Exception as exc:
            log.error("Sayfa %d [scraper/%s] hata: %s", sayfa_no, mod, exc)

    return None


def _fiyat_ayristir(urun_soup):
    """
    Amazon Depo fiyat formati: '25.459,05 TL (1 Ikinci El urun)'
    Bu metni iceren linkleri bul, fiyati regex ile cek.
    """
    # Regex: Turkce para birimi formati
    fiyat_re = re.compile(r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*TL")
    stok_re  = re.compile(r"\((\d+)\s+\S*kinci\s+[Ee]l", re.IGNORECASE)

    for el in urun_soup.find_all(["a", "span"]):
        try:
            metin = el.get_text(" ", strip=True)
            # Stok bilgisi olmayan elemani atla
            stok_m = stok_re.search(metin)
            if not stok_m:
                continue
            # Fiyati bul
            fiyat_m = fiyat_re.search(metin)
            if not fiyat_m:
                continue
            fiyat_str = fiyat_m.group(0).strip()
            sayi = float(fiyat_m.group(1).replace(".", "").replace(",", "."))
            if sayi <= 0:
                continue
            stok = stok_m.group(1)
            return fiyat_str, sayi, stok
        except Exception:
            continue
    return None, None, "1"


def urun_listesi_cek(sayfa_soup):
    divler = sayfa_soup.find_all("div", {"data-component-type": "s-search-result"})
    log.info("Ham urun div sayisi: %d", len(divler))
    sonuclar = []
    fiyatsiz = 0

    for urun in divler:
        try:
            isim_el = urun.find("h2")
            if not isim_el:
                continue
            isim = isim_el.get_text(strip=True)

            # Link: ASIN'den olustur
            asin = urun.get("data-asin", "").strip()
            if not asin:
                continue
            link = "https://www.amazon.com.tr/dp/" + asin

            fiyat_str, fiyat, stok = _fiyat_ayristir(urun)
            if not fiyat:
                fiyatsiz += 1
                continue

            gorsel = urun.find("img", class_="s-image")
            sonuclar.append({
                "isim":       isim,
                "asin":       asin,
                "fiyat_str":  fiyat_str,
                "fiyat":      fiyat,
                "gorsel_url": gorsel["src"] if gorsel else None,
                "link":       link,
                "stok_adet":  stok,
            })
        except Exception as exc:
            log.debug("Urun hatasi: %s", exc)

    if fiyatsiz:
        log.info("%d urun fiyatsiz atlandi.", fiyatsiz)
    return sonuclar


def tum_sayfalari_tara():
    tum = []
    bosh = 0
    sayfa = 1

    while True:
        log.info("Sayfa %d taranıyor...", sayfa)
        yanit = _sayfa_indir(sayfa)

        if yanit is None:
            bosh += 1
            if bosh >= MAX_BOSH_SAYFA:
                break
            time.sleep(SAYFA_BEKLEME)
            sayfa += 1
            continue

        soup   = BeautifulSoup(yanit.content, "html.parser")
        urunler = urun_listesi_cek(soup)

        if not urunler:
            bosh += 1
            if bosh >= MAX_BOSH_SAYFA:
                break
        else:
            bosh = 0
            tum.extend(urunler)
            log.info("Sayfa %d: %d urun. Toplam: %d", sayfa, len(urunler), len(tum))

        sayfa += 1
        time.sleep(SAYFA_BEKLEME)

    log.info("Tarama bitti. Toplam: %d urun", len(tum))
    return tum
