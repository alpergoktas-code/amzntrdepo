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
                continue
            soup = BeautifulSoup(yanit.content, "html.parser")
            if soup.find("div", {"data-component-type": "s-search-result"}):
                return yanit
            baslik = soup.find("title")
            html_kesit = yanit.text[:500].replace("\n", " ")
            log.info("Sayfa %d [%s] div=0 | Baslik=[%s] | HTML=%s",
                     sayfa_no, mod,
                     baslik.get_text(strip=True) if baslik else "yok",
                     html_kesit)
            if not render:
                log.info("Sayfa %d render=true deneniyor...", sayfa_no)
        except requests.RequestException as exc:
            log.error("Sayfa %d [%s] istek hatası: %s", sayfa_no, mod, exc)
    return None


def _metin_fiyata(metin: str):
    """
    'X.XXX,XX TL (N İkinci El ürün)' → float

    Kritik kural: Fiyat metni mutlaka 'TL' veya '₺' içermeli
    VE sayı virgüllü ondalık formatta olmalı (Türkçe para birimi formatı).
    Model numaralarını (TH 2200/S gibi) elemek için
    metnin 'ikinci el' veya '₺'/'TL' içerdiğini kontrol ediyoruz.
    """
    try:
        # Parantez içini at
        temiz = metin.split("(")[0]
        # TL veya ₺ işaretini kaldır
        temiz = temiz.replace("TL", "").replace("₺", "").replace("\xa0", "")
        # Türkçe format: nokta=binlik, virgül=ondalık
        # Örnek: "1.234,56" → "1234.56"
        temiz = temiz.replace(".", "").replace(",", ".").strip()
        m = re.search(r"\d+(?:\.\d+)?", temiz)
        return float(m.group()) if m else None
    except Exception:
        return None


def _fiyat_ayristir(urun_soup: BeautifulSoup):
    """
    Dönüş: (fiyat_str, fiyat_float, stok_adet)

    En güvenilir yöntem: Amazon'un offer-listing linkini bul.
    Bu link her zaman "X TL (N İkinci El ürün)" metnini içerir.
    """

    # ── Yöntem 1: /gp/offer-listing/ href'li link ────────────────────────────
    # Bu Amazon'un standart "ikinci el seçenekleri" linki.
    # Metin tam olarak "25.459,05 TL (1 İkinci El ürün)" formatındadır.
    try:
        for a in urun_soup.find_all("a", href=True):
            href = a.get("href", "")
            if "offer-listing" not in href and "olp" not in href:
                continue
            # Bu link ikinci el fiyat linki — metnini al
            metin = a.get_text(" ", strip=True)
            if not metin:
                continue
            # Fiyat kısmını ayıkla (parantezden önce)
            fiyat_kismi = metin.split("(")[0].strip()
            sayi = _metin_fiyata(fiyat_kismi)
            if not sayi or sayi <= 0:
                continue
            # Stok adedini bul
            stok_m = re.search(r"\((\d+)\s+[İi]kinci", metin)
            stok = stok_m.group(1) if stok_m else "1"
            log.debug("Yöntem 1 (offer-listing): %s TL, %s adet", sayi, stok)
            return fiyat_kismi, sayi, stok
    except Exception as exc:
        log.debug("Yöntem 1 hata: %s", exc)

    # ── Yöntem 2: .a-price > .a-offscreen (TL zorunlu, yıldız yasak) ─────────
    try:
        for kutu in urun_soup.find_all("span", class_="a-price"):
            gizli = kutu.find("span", class_="a-offscreen")
            if not gizli:
                continue
            metin = gizli.get_text(strip=True)
            # Kesinlikle TL veya ₺ içermeli; yıldız puanı olmamalı
            if ("TL" not in metin and "₺" not in metin):
                continue
            if "yıldız" in metin.lower() or "yildiz" in metin.lower():
                continue
            sayi = _metin_fiyata(metin)
            if sayi and sayi > 0:
                log.debug("Yöntem 2 (a-offscreen): %s", metin)
                return metin, sayi, "1"
    except Exception as exc:
        log.debug("Yöntem 2 hata: %s", exc)

    return None, None, "1"

    return None, None, "1"


def _urun_linki_bul(urun_soup: BeautifulSoup) -> str:
    """
    Ürünün Amazon detay sayfası linkini bulur.
    Önce data-asin'li div'den ASIN alır, sonra fallback olarak
    h2 içindeki ilk <a> etiketini dener.
    """
    # ASIN varsa standart ürün URL'si oluştur (en güvenilir yöntem)
    asin = urun_soup.get("data-asin", "").strip()
    if asin:
        return f"https://www.amazon.com.tr/dp/{asin}"

    # Fallback: h2 içindeki link
    try:
        h2 = urun_soup.find("h2")
        if h2:
            a = h2.find("a", href=True)
            if a and a["href"].startswith("/"):
                return "https://www.amazon.com.tr" + a["href"]
    except Exception:
        pass

    return None   # Link bulunamadı — bu ürün atlanacak


def urun_listesi_cek(sayfa_soup: BeautifulSoup) -> list[dict]:
    div_listesi = sayfa_soup.find_all("div", {"data-component-type": "s-search-result"})
    log.info("Ham ürün div sayısı: %d", len(div_listesi))
    sonuclar = []
    fiyatsiz = 0
    linksiz  = 0

    for urun in div_listesi:
        try:
            isim_el = urun.find("h2")
            if not isim_el:
                continue
            isim = isim_el.get_text(strip=True)

            # Link bulunamazsa ürünü atla (# URL Telegram'ı engelliyor)
            link = _urun_linki_bul(urun)
            if not link:
                linksiz += 1
                log.debug("Link bulunamadı, atlandı: %.60s", isim)
                continue

            fiyat_str, fiyat_float, stok = _fiyat_ayristir(urun)
            if not fiyat_float:
                fiyatsiz += 1
                log.debug("Fiyat bulunamadı, atlandı: %.60s", isim)
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
            log.debug("Ürün ayrıştırma hatası: %s", exc)

    if fiyatsiz:
        log.info("%d ürün fiyat bulunamadığı için atlandı.", fiyatsiz)
    if linksiz:
        log.info("%d ürün link bulunamadığı için atlandı.", linksiz)
    return sonuclar


def tum_sayfalari_tara() -> list[dict]:
    tum_urunler = []
    bosh_sayfa  = 0
    sayfa_no    = 1

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

        soup    = BeautifulSoup(yanit.content, "html.parser")
        urunler = urun_listesi_cek(soup)

        if not urunler:
            bosh_sayfa += 1
            if bosh_sayfa >= MAX_BOSH_SAYFA:
                break
        else:
            bosh_sayfa = 0
            tum_urunler.extend(urunler)
            log.info("Sayfa %d: %d ürün eklendi. Toplam: %d",
                     sayfa_no, len(urunler), len(tum_urunler))

        sayfa_no += 1
        time.sleep(SAYFA_BEKLEME)

    log.info("Tarama tamamlandı. Toplam: %d ürün", len(tum_urunler))
    return tum_urunler
