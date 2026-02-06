from __future__ import annotations
import re

FEE_KEYWORDS = [
    "ukt", "uang kuliah", "uang kuliah tunggal", "biaya kuliah", "biaya pendidikan",
    "biaya studi", "biaya per semester", "biaya per tahun", "spp", "spi", "ipi",
    "uang pangkal", "uang gedung", "dpp", "dana pengembangan", "biaya registrasi",
    "biaya herregistrasi", "tarif", "iuran", "tuition", "fee", "fees"
]

NOISE_KEYWORDS = [
    "berita", "news", "event", "agenda", "pengumuman", "artikel", "press",
    "galeri", "gallery", "opini", "blog", "riset", "penelitian",
    "karir", "career", "alumni", "profile", "profil", "sejarah", "history",
    "visi", "misi", "kemahasiswaan", "kemahasiswaan", "beasiswa", "scholarship",
    "download", "repository", "perpustakaan", "library"
]

PDF_EXT = (".pdf",)
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")

# Asset boleh lintas domain kalau ditemukan dari halaman resmi (bukan ditebak).
# Ini penting karena banyak kampus host PDF/gambar di CDN/S3/Drive.
ALLOWED_ASSET_HOSTS = [
    "drive.google.com", "docs.google.com", "storage.googleapis.com",
    "googleusercontent.com", "cloudfront.net", "amazonaws.com",
    "blob.core.windows.net",
]

MONEY_HINT_RE = re.compile(r"(?i)(rp\.?\s*)?\d{1,3}([.,]\d{3})+|\b\d{6,}\b")

FEE_WORD_RE = re.compile(
    r"(?i)\b("
    r"ukt|"
    r"spp|"
    r"spi|"
    r"ipi|"
    r"uang[\W_]*pangkal|"
    r"uang[\W_]*kuliah([\W_]*tunggal)?|"
    r"biaya[\W_]*(kuliah|pendidikan|studi|registrasi|herregistrasi)|"
    r"dpp|dana[\W_]*pengembangan|uang[\W_]*gedung|"
    r"tuition|fee|fees|tarif|iuran"
    r")\b"
)


# Hint bahwa konten menyebut program/jurusan/jenjang.
# Dipakai untuk *validasi ketat* (halaman dianggap benar bila ada PRODI/JENJANG + NOMINAL).
PRODI_HINT_RE = re.compile(
    r"(?i)\b("
    r"prodi|program\s*studi|jurusan|departemen|fakultas|"
    r"konsentrasi|peminatan|"
    r"program\s*(sarjana|magister|doktor)|"
    r"study\s*program|department|faculty"
    r")\b"
)

LEVEL_HINT_RE = re.compile(
    r"(?i)\b("
    r"s1|s2|s3|d[1-4]|diploma|sarjana|magister|doktor|"
    r"keprofesian|profesi|spesialis|pascasarjana|"
    r"undergraduate|graduate|master|phd"
    r")\b"
)
