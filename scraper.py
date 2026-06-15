"""
scraper.py — Amazon Depo ürün çekme motoru

Düzeltilen sorunlar (eski koda göre):
  1. URL encoding: BASE_URL içindeki & işaretleri artık ScraperAPI
     parametreleriyle karışmıyor.
  2. render=true: JavaScript ile yüklenen ürün kartları artık geliyor.
  3. Fiyat ayrıştırma: Birden fazla HTML yapısını kapsayan savunmacı
     bir zincir kullanıldı; başarısız adımlar loglanıyor.
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

# Amazon Depo (warehouse-deals) satıcı sayfası
BASE_URL = (
    "https://www.amazon.com.tr/Amazon-Depo/s"
    "?i=warehouse-deals"
    "&srs=44219324031"
    "&bbn=44219324031"
    "&rh=n%3A44219324031"
    "&fs=true"
)

SAYFA_BEKLEME   = 3    # saniye — sayfalar arası bekleme
ISTEK_TIMEOUT   = 90   # saniye — ScraperAPI render=true için yeterli süre
MAX_BOSH_SAYFA  = 2    # arka arkaya bu kadar boş sayfa gelirse dur


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _scraper_url(hedef_url: str) -> str:
    """Hedef URL'yi ScraperAPI proxy formatına çevirir."""
    encoded = quote(hedef_url, safe="")          # & ? = gibi karakterleri encode et
    return (
        f"http://api.scraperapi.com"
        f"?api_key={SCRAPER_KEY}"
        f"&url={encoded}"
        f"&country_code=tr"
        f"&render=true"                          # JavaScript render zorunlu
        f"&premium=true"                         # Amazon için anti-bot koruması
    )


def sayfa_cek(sayfa_no: int) -> requests.Response | None:
    """Tek bir Amazon listeleme sayfasını ScraperAPI üzerinden çeker."""
    hedef = f"{BASE_URL}&page={sayfa_no}"
    url   = _scraper_url(hedef)
    try:
        yanit = requests.get(url, timeout=ISTEK_TIMEOUT)
        log.debug(
            "Sayfa %d — HTTP %d, %d byte",
            sayfa_no, yanit.status_code, len(yanit.content)
        )
        if yanit.status_code != 200:
            log.warning("Sayfa %d beklenmeyen HTTP kodu: %d", sayfa_no, yanit.status_code)
            return None
        return yanit
    except requests.RequestException as exc:
        log.error("Sayfa %d isteği başarısız: %s", sayfa_no, exc)
        return None


# ── HTML Ayrıştırma ───────────────────────────────────────────────────────────

def _fiyat_ayristir(urun_soup: BeautifulSoup) -> tuple[str, float, str]:
    """
    Birden fazla Amazon HTML desenini deneyen fiyat ayrıştırıcı.
    Dönüş: (fiyat_str, fiyat_float, stok_adet)
    """

    # Yöntem 1 — "X ikinci el" veya "seçenekleri" içeren bağlantı metni
    try:
        for link in urun_soup.find_all("a", class_="a-link-normal"):
            metin = link.get_text(" ", strip=True)
            if ("TL" in metin or "₺" in metin) and (
                "ikinci el" in metin.lower() or "seçenekleri" in metin.lower()
            ):
                stok_m = re.search(r"(\d+)\s+ikinci\s+el", metin.lower())
                stok   = stok_m.group(1) if stok_m else "1"
                temiz  = metin.split("(")[0].strip() if "(" in metin else metin
                sayi   = _metin_fiyata(temiz)
                if sayi:
                    return temiz, sayi, stok
    except Exception as exc:
        log.debug("Yöntem 1 başarısız: %s", exc)

    # Yöntem 2 — .a-price > .a-offscreen
    try:
        kutu = urun_soup.find("span", class_="a-price")
        if kutu:
            gizli = kutu.find("span", class_="a-offscreen")
            if gizli:
                metin = gizli.get_text(strip=True)
                sayi  = _metin_fiyata(metin)
                if sayi:
                    return metin, sayi, "1"
    except Exception as exc:
        log.debug("Yöntem 2 başarısız: %s", exc)

    # Yöntem 3 — data-a-price attribute
    try:
        fiyat_el = urun_soup.find(attrs={"data-a-price": True})
        if fiyat_el:
            raw  = fiyat_el["data-a-price"]
            sayi = _metin_fiyata(raw)
            if sayi:
                return raw, sayi, "1"
    except Exception as exc:
        log.debug("Yöntem 3 başarısız: %s", exc)

    return None, None, None


def _metin_fiyata(metin: str) -> float | None:
    """'1.234,56 TL' → 1234.56 float dönüşümü."""
    try:
        temiz = (
            metin
            .replace("TL", "")
            .replace("₺", "")
            .replace("\xa0", "")
            .replace(".", "")    # Türkçe binlik ayracı
            .replace(",", ".")   # Türkçe ondalık ayracı
            .strip()
        )
        return float(re.search(r"[\d.]+", temiz).group())
    except Exception:
        return None


def urun_listesi_cek(sayfa_soup: BeautifulSoup) -> list[dict]:
    """
    Bir sayfa HTML'inden ürün listesi çıkarır.
    Her ürün: {isim, fiyat_str, fiyat_float, gorsel_url, link, stok_adet}
    """
    urunler  = sayfa_soup.find_all("div", {"data-component-type": "s-search-result"})
    sonuclar = []

    for urun in urunler:
        try:
            isim_el = urun.find("h2")
            if not isim_el:
                continue
            isim = isim_el.get_text(strip=True)

            fiyat_str, fiyat_float, stok = _fiyat_ayristir(urun)
            if not fiyat_float:
                log.debug("Fiyat okunamadı, ürün atlandı: %.50s", isim)
                continue

            gorsel_el  = urun.find("img", class_="s-image")
            gorsel_url = gorsel_el["src"] if gorsel_el else None

            link_el = urun.find("a", class_="a-link-normal s-no-outline")
            link    = (
                "https://www.amazon.com.tr" + link_el["href"]
                if link_el else "#"
            )

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

    return sonuclar


# ── Ana tarama döngüsü ────────────────────────────────────────────────────────

def tum_sayfalari_tara() -> list[dict]:
    """
    Tüm listeleme sayfalarını tarar, tüm ürünleri birleştirilmiş
    liste olarak döndürür.
    """
    tum_urunler  = []
    bosh_sayfa   = 0
    sayfa_no     = 1

    while True:
        log.info("Sayfa %d taranıyor...", sayfa_no)
        yanit = sayfa_cek(sayfa_no)

        if yanit is None:
            bosh_sayfa += 1
            log.warning("Sayfa %d alınamadı (%d/%d)", sayfa_no, bosh_sayfa, MAX_BOSH_SAYFA)
            if bosh_sayfa >= MAX_BOSH_SAYFA:
                break
            time.sleep(SAYFA_BEKLEME)
            sayfa_no += 1
            continue

        soup   = BeautifulSoup(yanit.content, "html.parser")
        urunler = urun_listesi_cek(soup)

        if not urunler:
            bosh_sayfa += 1
            log.info(
                "Sayfa %d boş geldi (%d/%d) — render sorunu veya son sayfa",
                sayfa_no, bosh_sayfa, MAX_BOSH_SAYFA
            )
            if bosh_sayfa >= MAX_BOSH_SAYFA:
                break
        else:
            bosh_sayfa = 0
            tum_urunler.extend(urunler)
            log.info("Sayfa %d: %d ürün alındı", sayfa_no, len(urunler))

        sayfa_no += 1
        time.sleep(SAYFA_BEKLEME)

    log.info("Tarama bitti. Toplam: %d ürün", len(tum_urunler))
    return tum_urunler
