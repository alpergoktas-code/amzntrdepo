"""
database.py — SQLite kalıcı depolama katmanı

Ürünleri ve fiyat geçmişini Railway yeniden başlatmalarında
kaybolmadan saklar.
"""

import sqlite3
import os
import logging

log = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "urunler.db")

# DB_PATH bir klasör içindeyse (örn. /data/urunler.db), o klasörü oluştur
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)


def baglanti_al() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def tablolari_olustur():
    """İlk çalıştırmada gerekli tabloları kurar."""
    with baglanti_al() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS urunler (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                isim        TEXT    NOT NULL UNIQUE,
                fiyat       REAL    NOT NULL,
                gorsel_url  TEXT,
                link        TEXT,
                stok_adet   TEXT,
                guncellendi TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS fiyat_gecmisi (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                urun_isim   TEXT    NOT NULL,
                eski_fiyat  REAL    NOT NULL,
                yeni_fiyat  REAL    NOT NULL,
                tarih       TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS bot_durum (
                anahtar     TEXT PRIMARY KEY,
                deger       TEXT
            );
        """)
    log.info("Veritabanı tabloları hazır: %s", DB_PATH)


# ── Ürün işlemleri ────────────────────────────────────────────────────────────

def urun_getir(isim: str) -> sqlite3.Row | None:
    with baglanti_al() as conn:
        return conn.execute(
            "SELECT * FROM urunler WHERE isim = ?", (isim,)
        ).fetchone()


def urun_kaydet(isim: str, fiyat: float, gorsel_url: str, link: str, stok_adet: str):
    """Yeni ürün ekler veya mevcut kaydı günceller."""
    with baglanti_al() as conn:
        conn.execute("""
            INSERT INTO urunler (isim, fiyat, gorsel_url, link, stok_adet)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(isim) DO UPDATE SET
                fiyat       = excluded.fiyat,
                gorsel_url  = excluded.gorsel_url,
                link        = excluded.link,
                stok_adet   = excluded.stok_adet,
                guncellendi = datetime('now','localtime')
        """, (isim, fiyat, gorsel_url, link, stok_adet))


def fiyat_gecmisi_kaydet(isim: str, eski: float, yeni: float):
    with baglanti_al() as conn:
        conn.execute(
            "INSERT INTO fiyat_gecmisi (urun_isim, eski_fiyat, yeni_fiyat) VALUES (?,?,?)",
            (isim, eski, yeni)
        )


def toplam_urun_sayisi() -> int:
    with baglanti_al() as conn:
        return conn.execute("SELECT COUNT(*) FROM urunler").fetchone()[0]


def son_guncelleme_zamani() -> str | None:
    with baglanti_al() as conn:
        row = conn.execute(
            "SELECT MAX(guncellendi) FROM urunler"
        ).fetchone()
        return row[0] if row else None


# ── Bot durum işlemleri ───────────────────────────────────────────────────────

def durum_yaz(anahtar: str, deger: str):
    with baglanti_al() as conn:
        conn.execute("""
            INSERT INTO bot_durum (anahtar, deger) VALUES (?,?)
            ON CONFLICT(anahtar) DO UPDATE SET deger = excluded.deger
        """, (anahtar, deger))


def durum_oku(anahtar: str) -> str | None:
    with baglanti_al() as conn:
        row = conn.execute(
            "SELECT deger FROM bot_durum WHERE anahtar = ?", (anahtar,)
        ).fetchone()
        return row["deger"] if row else None


# ── Kategori filtresi ─────────────────────────────────────────────────────────

# Amazon Depo ana kategorileri ve anahtar kelimeleri
KATEGORI_LISTESI = {
    "Elektronik":     ["elektronik", "şarj", "bluetooth", "usb", "hdmi", "kablo", "adaptör", "pil", "batarya"],
    "Bilgisayar":     ["laptop", "notebook", "bilgisayar", "macbook", "klavye", "mouse", "ssd", "monitör", "webcam"],
    "Moda":           ["gömlek", "pantolon", "elbise", "ceket", "kazak", "tişört", "etek", "mont", "sweatshirt", "hoodie"],
    "Spor":           ["spor", "ayakkabı", "koşu", "fitness", "yoga", "tayt", "forma", "sneaker", "antrenman"],
    "Oyun":           ["playstation", "xbox", "nintendo", "controller", "konsol", "joystick", "gaming"],
    "Kitap":          ["kitap", "book", "roman", "ansiklopedi", "edition", "lenses"],
    "Ev ve Yaşam":    ["ev", "mutfak", "tava", "tencere", "bardak", "kupa", "yastık", "halı", "perde"],
    "Oyuncak":        ["oyuncak", "lego", "bebek", "kukla", "tamagotchi"],
    "Sağlık":         ["sağlık", "vitamin", "takviye", "maske", "medikal"],
    "Güneş Gözlüğü":  ["güneş gözlüğü", "gözlük", "sunglasses"],
}


def aktif_kategorileri_getir() -> list[str]:
    """Aktif (seçili) kategorileri döndürür. Boş liste = filtre yok (hepsi)."""
    with baglanti_al() as conn:
        row = conn.execute(
            "SELECT deger FROM bot_durum WHERE anahtar = 'aktif_kategoriler'"
        ).fetchone()
        if not row or not row["deger"]:
            return []
        return [k.strip() for k in row["deger"].split(",") if k.strip()]


def aktif_kategorileri_kaydet(kategoriler: list[str]):
    """Aktif kategori listesini kaydeder."""
    deger = ",".join(kategoriler)
    with baglanti_al() as conn:
        conn.execute("""
            INSERT INTO bot_durum (anahtar, deger) VALUES ('aktif_kategoriler', ?)
            ON CONFLICT(anahtar) DO UPDATE SET deger = excluded.deger
        """, (deger,))


def urun_kategoriye_uyuyor_mu(isim: str) -> bool:
    """
    Aktif kategori yoksa True döner (filtre yok).
    Aktif kategori varsa ürün adı anahtar kelimelerden birini içeriyorsa True.
    """
    aktifler = aktif_kategorileri_getir()
    if not aktifler:
        return True  # Filtre yok, hepsini geç

    isim_lower = isim.lower()
    for kategori in aktifler:
        anahtar_kelimeler = KATEGORI_LISTESI.get(kategori, [])
        if any(kelime in isim_lower for kelime in anahtar_kelimeler):
            return True
    return False
