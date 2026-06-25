import sqlite3
import os
import re
import logging

log = logging.getLogger(__name__)

_ASIN_RE = re.compile(r"/dp/([A-Za-z0-9]+)")


def _link_den_asin_cikar(link):
    """Urun linkinden ASIN'i cikarir (link her zaman /dp/ASIN formatinda)."""
    if not link:
        return None
    m = _ASIN_RE.search(link)
    return m.group(1) if m else None

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
                isim        TEXT NOT NULL,
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
    _asin_migrasyonu()
    log.info("Veritabani hazir: %s", DB_PATH)


def _asin_migrasyonu():
    """
    Eski (isim bazli) semadan ASIN bazli semaya gecis.
    Hem ilk kurulumda hem de var olan (Railway uzerindeki) bir
    veritabaninda guvenle, veri kaybetmeden calisir.
    """
    with baglanti() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(urunler)").fetchall()]
        if "asin" not in cols:
            conn.execute("ALTER TABLE urunler ADD COLUMN asin TEXT")
            log.info("Migrasyon: 'urunler.asin' sutunu eklendi.")

        fg_cols = [r[1] for r in conn.execute("PRAGMA table_info(fiyat_gecmisi)").fetchall()]
        if "urun_asin" not in fg_cols:
            conn.execute("ALTER TABLE fiyat_gecmisi ADD COLUMN urun_asin TEXT")
            log.info("Migrasyon: 'fiyat_gecmisi.urun_asin' sutunu eklendi.")

        # Asin'i bos olan eski kayitlari linkten cikararak doldur
        eksikler = conn.execute(
            "SELECT id, isim, link FROM urunler WHERE asin IS NULL OR asin = ''"
        ).fetchall()

        isim_to_asin = {}
        yedek_sayac = 0
        for row in eksikler:
            asin = _link_den_asin_cikar(row["link"])
            if not asin:
                # Linkten cikarilamadiysa (olmamali ama guvenlik icin)
                asin = "ISIM:" + row["isim"]
                yedek_sayac += 1
            conn.execute("UPDATE urunler SET asin = ? WHERE id = ?", (asin, row["id"]))
            isim_to_asin[row["isim"]] = asin

        if eksikler:
            log.info(
                "Migrasyon: %d urun icin asin dolduruldu (%d yedek anahtarli).",
                len(eksikler), yedek_sayac
            )

        # Eski isim-bazli sistemde ayni urun, baslik degisikligiyle
        # birden fazla satir olarak kayitli olabilir. Ayni asin'e
        # dusenlerden en son guncellenen kalsin, digerleri silinsin.
        silinen = conn.execute("""
            DELETE FROM urunler
            WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY asin ORDER BY guncellendi DESC, id DESC
                           ) AS rn
                    FROM urunler
                ) WHERE rn = 1
            )
        """)
        if silinen.rowcount and silinen.rowcount > 0:
            log.info("Migrasyon: %d duplike urun satiri temizlendi.", silinen.rowcount)

        # Fiyat gecmisindeki eski (asin'siz) kayitlara da asin isle
        if isim_to_asin:
            for isim, asin in isim_to_asin.items():
                conn.execute(
                    "UPDATE fiyat_gecmisi SET urun_asin = ? "
                    "WHERE urun_isim = ? AND (urun_asin IS NULL OR urun_asin = '')",
                    (asin, isim)
                )

        # Artik duplike olmadigina gore unique index'i guvenle olustur
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_urunler_asin ON urunler(asin)")


def urun_getir(asin):
    with baglanti() as conn:
        return conn.execute("SELECT * FROM urunler WHERE asin = ?", (asin,)).fetchone()


def urun_kaydet(asin, isim, fiyat, gorsel_url, link, stok_adet):
    with baglanti() as conn:
        conn.execute("""
            INSERT INTO urunler (asin, isim, fiyat, gorsel_url, link, stok_adet)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(asin) DO UPDATE SET
                isim=excluded.isim, fiyat=excluded.fiyat, gorsel_url=excluded.gorsel_url,
                link=excluded.link, stok_adet=excluded.stok_adet,
                guncellendi=datetime('now','localtime')
        """, (asin, isim, fiyat, gorsel_url, link, stok_adet))


def fiyat_gecmisi_kaydet(asin, isim, eski, yeni):
    with baglanti() as conn:
        conn.execute(
            "INSERT INTO fiyat_gecmisi (urun_asin, urun_isim, eski_fiyat, yeni_fiyat) VALUES (?,?,?,?)",
            (asin, isim, eski, yeni)
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
