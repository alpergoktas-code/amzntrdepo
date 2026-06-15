"""
scraper.py — Amazon Depo ürün çekme motoru
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
                log.warning("Sayfa %d HTTP %d", sayfa_no, yanit.status_code)
                continue

            soup = BeautifulSoup(yanit.content, "html.parser")
            divler = soup.find_all("div", {"data-component-type": "s-search-result"})

            if divler:
                log.info("Sayfa %d [%s] — %d ürün div'i bulundu.", sayfa_no, mod, len(divler))
                return yanit

            # Div bulunamadı — ne geldiğini teşhis et
            baslik = soup.find("title")
            baslik_str = baslik.get_text(strip=True) if baslik else "yok"
            html_kesit = yanit.text[:600].replace("\n", " ").replace("\r", "")
            log.info("Sayfa %d [%s] div=0 | Baslik=[%s] | HTML=%s",
                     sayfa_no, mod, baslik_str, html_kesit)

            if not render:
                log.info("Sayfa %d render=true deneniyor...", sayfa_no)

        except requests.RequestException as exc:
            log.error("Sayfa %d [%s] istek hatası: %s", sayfa_no, mod, exc)

    return None


def _metin_fiyata(metin: str):
    try:
        temiz = metin.split("(")[0]
        temiz = (
            temiz
            .replace("TL", "").replace("₺", "").replace("\xa0", "")
            .replace(".", "").replace(",", ".").strip()
        )
        m = re.search(r"\d+(?:\.\d+)?", temiz)
        return float(m.group()) if m else None
    except Exception:
        return None


def _fiyat_ayristir(urun_soup: BeautifulSoup):
    # Yöntem 1: "X TL (N İkinci El ürün)" metni
    try:
        for el in urun_soup.find_all(["a", "span"]):
            metin = el.get_text(" ", strip=True)
            if re.search(r"\d", metin) and ("TL" in metin or "₺" in metin):
                stok_m = re.search(r"\((\d+)\s+[İi]kinci\s+[Ee]l", metin)
                if stok_m:
                    stok = stok_m.group(1)
                    sayi = _metin_fiyata(metin)
                    if sayi:
                        return metin.split("(")[0].strip(), sayi, stok
    except Exception:
        pass

    # Yöntem 2: .a-price > .a-offscreen
    try:
        kutu = urun_soup.find("span", class_="a-price")
        if kutu:
            gizli = kutu.find("span", class_="a-offscreen")
            if gizli:
                metin = gizli.get_text(strip=True)
                sayi = _metin_fiyata(metin)
                if sayi:
                    return metin, sayi, "1"
    except Exception:
        pass

    # Yöntem 3: data-a-price
    try:
        el = urun_soup.find(attrs={"data-a-price": True})
        if el:
            raw = el["data-a-price"]
            sayi = _metin_fiyata(raw)
            if sayi:
                return raw, sayi, "1"
    except Exception:
        pass

    return None, None, "1"


def urun_listesi_cek(sayfa_soup: BeautifulSoup) -> list[dict]:
    div_listesi = sayfa_soup.find_all("div", {"data-component-type": "s-search-result"})
    sonuclar = []
    fiyatsiz = 0

    for urun in div_listesi:
        try:
            isim_el = urun.find("h2")
            if not isim_el:
                continue
            isim = isim_el.get_text(strip=True)

            fiyat_str, fiyat_float, stok = _fiyat_ayristir(urun)
            if not fiyat_float:
                fiyatsiz += 1
                continue

            gorsel_el = urun.find("img", class_="s-image")
            gorsel_url = gorsel_el["src"] if gorsel_el else None

            h2 = urun.find("h2")
            link_el = h2.find("a") if h2 else None
            link = ("https://www.amazon.com.tr" + link_el["href"]) if link_el else "#"

            sonuclar.append({
                "isim": isim,
                "fiyat_str": fiyat_str,
                "fiyat": fiyat_float,
                "gorsel_url": gorsel_url,
                "link": link,
                "stok_adet": stok,
            })
        except Exception as exc:
            log.debug("Ürün ayrıştırma hatası: %s", exc)

    if fiyatsiz:
        log.info("%d ürün fiyat bulunamadığı için atlandı.", fiyatsiz)
    return sonuclar


def tum_sayfalari_tara() -> list[dict]:
    tum_urunler = []
    bosh_sayfa = 0
    sayfa_no = 1

    while True:
        log.info("Sayfa %d taranıyor...", sayfa_no)
        yanit = _sayfa_indir(sayfa_no)

        if yanit is None:
            bosh_sayfa += 1
            if bosh_sayfa >= MAX_BOSH_SAYFA:
                log.info("Boş sayfa limiti aşıldı, tarama durduruluyor.")
                break
            time.sleep(SAYFA_BEKLEME)
            sayfa_no += 1
            continue

        soup = BeautifulSoup(yanit.content, "html.parser")
        urunler = urun_listesi_cek(soup)

        if not urunler:
            bosh_sayfa += 1
            if bosh_sayfa >= MAX_BOSH_SAYFA:
                break
        else:
            bosh_sayfa = 0
            tum_urunler.extend(urunler)
            log.info("Sayfa %d: %d ürün eklendi. Toplam: %d", sayfa_no, len(urunler), len(tum_urunler))

        sayfa_no += 1
        time.sleep(SAYFA_BEKLEME)

    log.info("Tarama tamamlandı. Toplam: %d ürün", len(tum_urunler))
    return tum_urunler
