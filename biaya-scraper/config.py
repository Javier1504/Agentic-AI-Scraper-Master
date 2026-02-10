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

MONEY_HINT_RE = re.compile(r"(?i)(rp\.?\s*)?\d{1,3}([.,]\d{3})+|\b\d{6,}\b")

FEE_WORD_RE = re.compile(
    r"(?i)\b(ukt|spp|spi|ipi|uang\s*pangkal|biaya\s*(kuliah|pendidikan|studi)|tuition|fee|fees|tarif|iuran)\b"
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

# Banyak halaman biaya tidak menulis kata "prodi"/"program studi" tetapi langsung
# menyebut nama jurusan di tabel (mis. "Teknik Informatika", "Manajemen").
# Regex ini dipakai sebagai sinyal *nama prodi* yang umum di Indonesia.
PRODI_NAME_RE = re.compile(
    r"(?i)\b("
    r"teknik|informatika|sistem\s*informasi|ilmu\s*komputer|data\s*science|"
    r"manajemen|akuntansi|ekonomi|bisnis|kewirausahaan|administrasi|"
    r"hukum|hubungan\s*internasional|ilmu\s*politik|pemerintahan|"
    r"psikologi|komunikasi|desain|dkv|arsitektur|sipil|mesin|elektro|industri|"
    r"kedokteran|kedokteran\s*gigi|farmasi|keperawatan|kebidanan|kesehatan\s*masyarakat|"
    r"pendidikan|matematika|fisika|kimia|biologi|statistika|"
    r"bahasa|sastra|inggris|jepang|arab|"
    r"pertanian|perikanan|kelautan|peternakan|"
    r"pariwisata|perhotelan|"
    r"teknologi\s*informasi|rekayasa\s*perangkat\s*lunak"
    r")\b"
)

# Sinyal baris tabel yang mirip "<nama prodi> ... <nominal>".
PRODI_MONEY_ROW_RE = re.compile(
    r"(?is)\b([A-Za-z]{3,}(?:\s+[A-Za-z]{3,}){0,8})\b[^\n]{0,60}"
    r"((?:rp\.?\s*)?\d{1,3}(?:[\.,]\d{3})+|\b\d{6,}\b)"
)
