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
