import sqlite3
import os
import logging

log = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "urunler.db")

_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)


def baglanti():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def tablolari_olustur():
    with baglanti() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS urunler (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                isim        TEXT NOT NULL UNIQUE,
                fiyat       REAL NOT NULL,
                gorsel_url  TEXT,
                link        TEXT,
                stok_adet   TEXT,
                guncellendi TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS fiyat_gecmisi (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                urun_isim   TEXT NOT NULL,
                eski_fiyat  REAL NOT NULL,
                yeni_fiyat  REAL NOT NULL,
                tarih       TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS ayarlar (
                anahtar TEXT PRIMARY KEY,
                deger   TEXT
            );
        """)
    log.info("Veritabani hazir: %s", DB_PATH)


def urun_getir(isim):
    with baglanti() as conn:
        return conn.execute("SELECT * FROM urunler WHERE isim = ?", (isim,)).fetchone()


def urun_kaydet(isim, fiyat, gorsel_url, link, stok_adet):
    with baglanti() as conn:
        conn.execute("""
            INSERT INTO urunler (isim, fiyat, gorsel_url, link, stok_adet)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(isim) DO UPDATE SET
                fiyat=excluded.fiyat, gorsel_url=excluded.gorsel_url,
                link=excluded.link, stok_adet=excluded.stok_adet,
                guncellendi=datetime('now','localtime')
        """, (isim, fiyat, gorsel_url, link, stok_adet))


def fiyat_gecmisi_kaydet(isim, eski, yeni):
    with baglanti() as conn:
        conn.execute(
            "INSERT INTO fiyat_gecmisi (urun_isim, eski_fiyat, yeni_fiyat) VALUES (?,?,?)",
            (isim, eski, yeni)
        )


def urunleri_sifirla():
    with baglanti() as conn:
        conn.execute("DELETE FROM urunler")
        conn.execute("DELETE FROM fiyat_gecmisi")
    log.info("Veritabani sifirlandi.")


def toplam_urun():
    with baglanti() as conn:
        return conn.execute("SELECT COUNT(*) FROM urunler").fetchone()[0]


def ayar_yaz(anahtar, deger):
    with baglanti() as conn:
        conn.execute("""
            INSERT INTO ayarlar (anahtar, deger) VALUES (?,?)
            ON CONFLICT(anahtar) DO UPDATE SET deger=excluded.deger
        """, (anahtar, deger))


def ayar_oku(anahtar):
    with baglanti() as conn:
        row = conn.execute("SELECT deger FROM ayarlar WHERE anahtar=?", (anahtar,)).fetchone()
        return row["deger"] if row else None


def son_guncelleme():
    with baglanti() as conn:
        row = conn.execute("SELECT MAX(guncellendi) FROM urunler").fetchone()
        return row[0] if row else None
