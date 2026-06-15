"""
scraper.py — Amazon Depo urun cekme motoru
"""

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
    "?i=warehouse-deals"
    "&srs=44219324031"
    "&bbn=44219324031"
    "&rh=n%3A44219324031"
    "&fs=true"
)

SAYFA_BEKLEME  = 3
ISTEK_TIMEOUT  = 90
MAX_BOSH_SAYFA = 2

IKINCI_EL_RE = re.compile(r"\(\d+\s+ikinci\s+el", re.IGNORECASE)
STOK_RE      = re.compile(r"\((\d+)\s+ikinci\s+el", re.IGNORECASE)


def _scraper_url(hedef_url: str, render: bool = False) -> str:
    encoded = quote(hedef_url, safe="")
    url = (
        f"http://api.scraperapi.com"
        f"?api_key={SCRAPER_KEY}"
        f"&url={encoded}"
        f"&country_code=tr"
        f"&device_type=desktop"
    )
    if render:
        url += "&render=true"
    return url


def _sayfa_indir(sayfa_no: int):
    hedef = f"{BASE_URL}&page={sayfa_no}"
    for render in (False, True):
        mod = "render=true" if render else "render=false"
        try:
            yanit = requests.get(_scraper_url(hedef, render=render), timeout=ISTEK_TIMEOUT)
            log.info("Sayfa %d [%s] — HTTP %d, %d byte, %.1f sn",
                     sayfa_no, mod, yanit.status_code, len(yanit.content),
                     yanit.elapsed.total_seconds())
            if yanit.status_code != 200:
                continue
            soup = BeautifulSoup(yanit.content, "html.parser")
            if soup.find("div", {"data-component-type": "s-search-result"}):
                return yanit
            baslik = soup.find("title")
            log.info("Sayfa %d [%s] div=0 | Baslik=[%s]",
                     sayfa_no, mod,
                     baslik.get_text(strip=True) if baslik else "yok")
            if not render:
                log.info("Sayfa %d render=true deneniyor...", sayfa_no)
        except requests.RequestException as exc:
            log.error("Sayfa %d [%s] istek hatasi: %s", sayfa_no, mod, exc)
    return None


def _metin_fiyata(metin: str):
    try:
        temiz = metin.split("(")[0]
        temiz = (
            temiz
            .replace("TL", "").replace("\u20ba", "").replace("\xa0", "")
            .replace(".", "").replace(",", ".").strip()
        )
        m = re.search(r"\d+(?:\.\d+)?", temiz)
        return float(m.group()) if m else None
    except Exception:
        return None


def _fiyat_ayristir(urun_soup: BeautifulSoup):
    """
    Donus: (fiyat_str, fiyat_float, stok_adet)

    Ornek hedef metin: "25.459,05 TL (1 Ikinci El urun)"

    Kural:
      - Metin "ikinci el" (buyuk/kucuk harf farksiz) icermeli
      - Metin TL veya lira isareti icermeli
      - Parantezden onceki kisim (fiyat) 25 karakterden kisa olmali
        -> model numaralari ve urun adlari bu siniri gece
    """

    # Yontem 1: ikinci el metni iceren a/span
    try:
        for el in urun_soup.find_all(["a", "span"]):
            metin = el.get_text(" ", strip=True)

            # TL veya lira isareti olmali
            if "TL" not in metin and "\u20ba" not in metin:
                continue

            # "ikinci el" gecmeli
            if not IKINCI_EL_RE.search(metin):
                continue

            # Parantezden onceki kisim
            fiyat_kismi = metin.split("(")[0].strip()

            # Cok uzunsa urun adi veya model numarasi iceriyor demektir
            if len(fiyat_kismi) > 25:
                continue

            sayi = _metin_fiyata(fiyat_kismi)
            if not sayi or sayi <= 0:
                continue

            stok_m = STOK_RE.search(metin)
            stok = stok_m.group(1) if stok_m else "1"
            log.debug("Yontem 1: %s — %s adet", fiyat_kismi, stok)
            return fiyat_kismi, sayi, stok
    except Exception as exc:
        log.debug("Yontem 1 hata: %s", exc)

    # Yontem 2: .a-price > .a-offscreen
    try:
        for kutu in urun_soup.find_all("span", class_="a-price"):
            gizli = kutu.find("span", class_="a-offscreen")
            if not gizli:
                continue
            metin = gizli.get_text(strip=True)
            if "TL" not in metin and "\u20ba" not in metin:
                continue
            if "yildiz" in metin.lower():
                continue
            if len(metin) > 20:
                continue
            sayi = _metin_fiyata(metin)
            if sayi and sayi > 0:
                log.debug("Yontem 2: %s", metin)
                return metin, sayi, "1"
    except Exception as exc:
        log.debug("Yontem 2 hata: %s", exc)

    return None, None, "1"


def _urun_linki_bul(urun_soup: BeautifulSoup) -> str:
    asin = urun_soup.get("data-asin", "").strip()
    if asin:
        return f"https://www.amazon.com.tr/dp/{asin}"
    try:
        h2 = urun_soup.find("h2")
        if h2:
            a = h2.find("a", href=True)
            if a and a["href"].startswith("/"):
                return "https://www.amazon.com.tr" + a["href"]
    except Exception:
        pass
    return None


def urun_listesi_cek(sayfa_soup: BeautifulSoup) -> list:
    div_listesi = sayfa_soup.find_all("div", {"data-component-type": "s-search-result"})
    log.info("Ham urun div sayisi: %d", len(div_listesi))
    sonuclar = []
    fiyatsiz = 0
    linksiz  = 0

    for urun in div_listesi:
        try:
            isim_el = urun.find("h2")
            if not isim_el:
                continue
            isim = isim_el.get_text(strip=True)

            link = _urun_linki_bul(urun)
            if not link:
                linksiz += 1
                continue

            fiyat_str, fiyat_float, stok = _fiyat_ayristir(urun)
            if not fiyat_float:
                fiyatsiz += 1
                continue

            gorsel_el  = urun.find("img", class_="s-image")
            gorsel_url = gorsel_el["src"] if gorsel_el else None

            sonuclar.append({
                "isim":       isim,
                "fiyat_str":  fiyat_str,
                "fiyat":      fiyat_float,
                "gorsel_url": gorsel_url,
                "link":       link,
                "stok_adet":  stok,
            })
        except Exception as exc:
            log.debug("Urun ayristirma hatasi: %s", exc)

    if fiyatsiz:
        log.info("%d urun fiyat bulunamadigi icin atlandi.", fiyatsiz)
    if linksiz:
        log.info("%d urun link bulunamadigi icin atlandi.", linksiz)
    return sonuclar


def tum_sayfalari_tara() -> list:
    tum_urunler = []
    bosh_sayfa  = 0
    sayfa_no    = 1

    while True:
        log.info("Sayfa %d taranıyor...", sayfa_no)
        yanit = _sayfa_indir(sayfa_no)

        if yanit is None:
            bosh_sayfa += 1
            if bosh_sayfa >= MAX_BOSH_SAYFA:
                log.info("Bos sayfa limiti asildi, tarama durduruluyor.")
                break
            time.sleep(SAYFA_BEKLEME)
            sayfa_no += 1
            continue

        soup    = BeautifulSoup(yanit.content, "html.parser")
        urunler = urun_listesi_cek(soup)

        if not urunler:
            bosh_sayfa += 1
            if bosh_sayfa >= MAX_BOSH_SAYFA:
                break
        else:
            bosh_sayfa = 0
            tum_urunler.extend(urunler)
            log.info("Sayfa %d: %d urun eklendi. Toplam: %d",
                     sayfa_no, len(urunler), len(tum_urunler))

        sayfa_no += 1
        time.sleep(SAYFA_BEKLEME)

    log.info("Tarama tamamlandi. Toplam: %d urun", len(tum_urunler))
    return tum_urunler
