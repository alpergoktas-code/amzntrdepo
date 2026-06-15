"""
scraper.py — Amazon Depo ürün çekme motoru

Strateji:
  1. Önce render=false ile dener (hızlı, ucuz)
  2. Ürün bulunamazsa render=true ile tekrar dener (yavaş, pahalı)

Fiyat formatı (ekran görüntüsünden tespit edildi):
  "25.459,05 TL (1 İkinci El ürün)"
  "34.325,10 TL (2 İkinci El ürün)"
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

SAYFA_BEKLEME  = 3    # saniye
ISTEK_TIMEOUT  = 90   # saniye
MAX_BOSH_SAYFA = 2


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _scraper_url(hedef_url: str, render: bool = False) -> str:
    encoded = quote(hedef_url, safe="")
    url = (
        f"http://api.scraperapi.com"
        f"?api_key={SCRAPER_KEY}"
        f"&url={encoded}"
        f"&country_code=tr"
    )
    if render:
        url += "&render=true"
    return url


def _sayfa_indir(sayfa_no: int) -> requests.Response | None:
    """
    Önce render=false dener. Ürün div'i gelmezse render=true ile tekrar ister.
    """
    hedef = f"{BASE_URL}&page={sayfa_no}"

    for render in (False, True):
        mod = "render=true" if render else "render=false"
        try:
            yanit = requests.get(_scraper_url(hedef, render=render), timeout=ISTEK_TIMEOUT)
            log.info("Sayfa %d [%s] — HTTP %d, %d byte",
                     sayfa_no, mod, yanit.status_code, len(yanit.content))

            if yanit.status_code != 200:
                log.warning("Sayfa %d HTTP %d", sayfa_no, yanit.status_code)
                continue

            # Ürün div var mı kontrol et
            soup = BeautifulSoup(yanit.content, "html.parser")
            if soup.find("div", {"data-component-type": "s-search-result"}):
                log.info("Sayfa %d [%s] ürün div'leri bulundu.", sayfa_no, mod)
                return yanit

            log.info("Sayfa %d [%s] ürün div'i yok%s",
                     sayfa_no, mod,
                     " — render=true deneniyor..." if not render else " — sayfa boş.")

        except requests.RequestException as exc:
            log.error("Sayfa %d [%s] istek hatası: %s", sayfa_no, mod, exc)

    return None


# ── Fiyat ayrıştırma ─────────────────────────────────────────────────────────

def _metin_fiyata(metin: str) -> float | None:
    """'25.459,05 TL' → 25459.05"""
    try:
        # Parantez içini at: "25.459,05 TL (1 İkinci El ürün)" → "25.459,05 TL"
        temiz = metin.split("(")[0]
        temiz = (
            temiz
            .replace("TL", "")
            .replace("₺", "")
            .replace("\xa0", "")
            .replace(".", "")    # binlik ayracı
            .replace(",", ".")   # ondalık ayracı
            .strip()
        )
        m = re.search(r"\d+(?:\.\d+)?", temiz)
        return float(m.group()) if m else None
    except Exception:
        return None


def _fiyat_ayristir(urun_soup: BeautifulSoup) -> tuple[str | None, float | None, str]:
    """
    Dönüş: (fiyat_str, fiyat_float, stok_adet)

    Hedef format: "25.459,05 TL (1 İkinci El ürün)"
    Bu metin, ürün kartındaki bir <a> veya <span> içinde geçiyor.
    """

    # ── Yöntem 1: İkinci El bağlantı metni ──────────────────────────────────
    # Örnek: <a ...>25.459,05 TL (1 İkinci El ürün)</a>
    try:
        for el in urun_soup.find_all(["a", "span"]):
            metin = el.get_text(" ", strip=True)
            if re.search(r"\d", metin) and ("TL" in metin or "₺" in metin):
                stok_m = re.search(r"\((\d+)\s+[İi]kinci\s+[Ee]l", metin)
                if stok_m:
                    stok  = stok_m.group(1)
                    sayi  = _metin_fiyata(metin)
                    if sayi:
                        fiyat_str = metin.split("(")[0].strip()
                        log.debug("Yöntem 1 başarılı: %s — %s adet", fiyat_str, stok)
                        return fiyat_str, sayi, stok
    except Exception as exc:
        log.debug("Yöntem 1 hata: %s", exc)

    # ── Yöntem 2: .a-price > .a-offscreen ───────────────────────────────────
    try:
        kutu = urun_soup.find("span", class_="a-price")
        if kutu:
            gizli = kutu.find("span", class_="a-offscreen")
            if gizli:
                metin = gizli.get_text(strip=True)
                sayi  = _metin_fiyata(metin)
                if sayi:
                    log.debug("Yöntem 2 başarılı: %s", metin)
                    return metin, sayi, "1"
    except Exception as exc:
        log.debug("Yöntem 2 hata: %s", exc)

    # ── Yöntem 3: data-a-price JSON attribute ────────────────────────────────
    try:
        el = urun_soup.find(attrs={"data-a-price": True})
        if el:
            raw  = el["data-a-price"]
            sayi = _metin_fiyata(raw)
            if sayi:
                log.debug("Yöntem 3 başarılı: %s", raw)
                return raw, sayi, "1"
    except Exception as exc:
        log.debug("Yöntem 3 hata: %s", exc)

    return None, None, "1"


# ── Ürün listesi çıkarma ──────────────────────────────────────────────────────

def urun_listesi_cek(sayfa_soup: BeautifulSoup) -> list[dict]:
    div_listesi = sayfa_soup.find_all("div", {"data-component-type": "s-search-result"})
    log.info("Ham ürün div sayısı: %d", len(div_listesi))

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
                log.debug("Fiyat bulunamadı: %.60s", isim)
                continue

            gorsel_el  = urun.find("img", class_="s-image")
            gorsel_url = gorsel_el["src"] if gorsel_el else None

            # Ürün detay linki
            link_el = urun.find("a", class_=re.compile(r"a-link-normal.*s-no-outline|s-no-outline.*a-link-normal"))
            if not link_el:
                link_el = urun.find("h2").find("a") if urun.find("h2") else None
            link = ("https://www.amazon.com.tr" + link_el["href"]) if link_el else "#"

            sonuclar.append({
                "isim":       isim,
                "fiyat_str":  fiyat_str,
                "fiyat":      fiyat_float,
                "gorsel_url": gorsel_url,
                "link":       link,
                "stok_adet":  stok,
            })
        except Exception as exc:
            log.debug("Ürün ayrıştırma hatası: %s", exc)

    if fiyatsiz:
        log.info("%d ürün fiyat bulunamadığı için atlandı.", fiyatsiz)

    return sonuclar


# ── Ana tarama döngüsü ────────────────────────────────────────────────────────

def tum_sayfalari_tara() -> list[dict]:
    tum_urunler = []
    bosh_sayfa  = 0
    sayfa_no    = 1

    while True:
        log.info("Sayfa %d taranıyor...", sayfa_no)
        yanit = _sayfa_indir(sayfa_no)

        if yanit is None:
            bosh_sayfa += 1
            log.warning("Sayfa %d alınamadı (%d/%d)", sayfa_no, bosh_sayfa, MAX_BOSH_SAYFA)
            if bosh_sayfa >= MAX_BOSH_SAYFA:
                log.info("Arka arkaya %d boş sayfa — tarama durduruluyor.", MAX_BOSH_SAYFA)
                break
            time.sleep(SAYFA_BEKLEME)
            sayfa_no += 1
            continue

        soup    = BeautifulSoup(yanit.content, "html.parser")
        urunler = urun_listesi_cek(soup)

        if not urunler:
            bosh_sayfa += 1
            if bosh_sayfa >= MAX_BOSH_SAYFA:
                log.info("Son sayfa veya boş sayfa limiti aşıldı, duruluyor.")
                break
        else:
            bosh_sayfa = 0
            tum_urunler.extend(urunler)
            log.info("Sayfa %d: %d ürün eklendi. (Toplam: %d)", sayfa_no, len(urunler), len(tum_urunler))

        sayfa_no += 1
        time.sleep(SAYFA_BEKLEME)

    log.info("Tarama tamamlandı. Toplam ürün: %d", len(tum_urunler))
    return tum_urunler
