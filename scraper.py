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

# Fiyat: "25.459,05 TL" veya "459 TL" gibi formatlari eslestirir
FIYAT_RE  = re.compile(r"([\d]{1,3}(?:[.\d]{4})*(?:,\d{2})?)\s*(TL|\u20ba)")
# Stok: "(1 Ikinci El urun)" veya "(3 ikinci el..." 
STOK_RE   = re.compile(r"\((\d+)\s+\S*kinci\s+[Ee]l", re.IGNORECASE)


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


def _fiyat_ayristir(urun_soup: BeautifulSoup):
    """
    Donus: (fiyat_str, fiyat_float, stok_adet)

    Yontem: TL iceren ve 'kinci El' gecen her elementi tara.
    Fiyati regex ile cikar — boylece uzun metinlerde de calisir:
      "Diger satin alma secenekleri 25.459,05 TL (1 Ikinci El urun)"
    """
    for el in urun_soup.find_all(["a", "span"]):
        try:
            metin = el.get_text(" ", strip=True)

            # "kinci El" gecmeli (I/i farki yok, basi atlayarak eslesir)
            if not STOK_RE.search(metin):
                continue

            # Fiyati regex ile bul
            fiyat_m = FIYAT_RE.search(metin)
            if not fiyat_m:
                continue

            fiyat_str = fiyat_m.group(0).strip()   # "25.459,05 TL"
            ham_sayi  = fiyat_m.group(1)            # "25.459,05"

            # Turkce formatini float'a cevir
            sayi = float(ham_sayi.replace(".", "").replace(",", "."))
            if sayi <= 0:
                continue

            stok_m = STOK_RE.search(metin)
            stok   = stok_m.group(1) if stok_m else "1"

            log.debug("Fiyat: %s — %s adet", fiyat_str, stok)
            return fiyat_str, sayi, stok

        except Exception as exc:
            log.debug("Element hatasi: %s", exc)

    # Yontem 2: .a-price > .a-offscreen (TL zorunlu, yildiz yasak)
    try:
        for kutu in urun_soup.find_all("span", class_="a-price"):
            gizli = kutu.find("span", class_="a-offscreen")
            if not gizli:
                continue
            metin = gizli.get_text(strip=True)
            if "TL" not in metin and "\u20ba" not in metin:
                continue
            if "yildiz" in metin.lower() or "y\u0131ld\u0131z" in metin.lower():
                continue
            fiyat_m = FIYAT_RE.search(metin)
            if not fiyat_m:
                continue
            sayi = float(fiyat_m.group(1).replace(".", "").replace(",", "."))
            if sayi > 0:
                return fiyat_m.group(0).strip(), sayi, "1"
    except Exception as exc:
        log.debug("Yontem 2 hatasi: %s", exc)

    return None, None, "1"


def _urun_linki_bul(urun_soup: BeautifulSoup):
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
            log.debug("Urun hatasi: %s", exc)

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
