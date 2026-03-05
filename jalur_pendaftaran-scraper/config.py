from __future__ import annotations
import re

JALUR_KEYWORDS = [
    # Standar keywords
    "jalur pendaftaran", "jalur seleksi", "jalur masuk", "jadwal seleksi", 
    "jadwal pendaftaran", "snbp", "snbt", "mandiri", "seleksi mandiri",
    "ujian mandiri", "utbk", "pmb", "ppmb", "snpmb", "penerimaan mahasiswa baru",
    "timeline pendaftaran", "registrasi", "registration", "admission", "admissions",
    "intake", "application period", "pendaftaran", "seleksi", 
    "penerimaan", "daftar masuk", "enrollment",
    
    # Jalur khusus
    "gelombang 1", "gelombang 2", "gelombang 3", "gelombang",
    "iup", "international undergraduate", "pbutm", "pbub",
    "sbub", "sbupn", "um", "ujian mandiri",
    "prestasi", "kemitraan", "transfer",
    "pascasarjana", "magister", "s2", "s3", "doktor", "profesi", "spesialis",
    
    # Variasi Indonesia/English
    "openingperiod", "jadwalbukatutp", "kalender", "timeline",
    "spmb", "sipenmaru", "snpmbn"
]

NOISE_KEYWORDS = [
    # Content yang bukan admission
    "berita", "news", "event", "agenda", "pengumuman", "artikel", "press",
    "galeri", "gallery", "opini", "blog", "riset", "penelitian",
    "karir", "career", "alumni", "profile", "profil", "sejarah", "history",
    "visi", "misi", "kemahasiswaan", "beasiswa", "scholarship",
    "download", "repository", "perpustakaan", "library","uang kuliah", 
    "biaya kuliah", "tuition fee", "biaya pendidikan", "program studi", 
    "fakultas", "departemen", "jurusan", "prodi", "logo", "kontak", "contact", 
    "lokasi", "location", "peta", "map", "faq", "help", "bantuan", 
    "testimoni", "testimonial", "tentang kami", "about us", 
    "feedback", "saran", "kritik", "portofolio", "portfolio"
]

HARD_NOISE_KEYWORDS = [
    "/berita/",
    "/news/",
    "/category/",
    "/tag/",
    "/author/",
    "/page/",
    "/feed/",
    "/blog/",
    "/article/",
]

# phrases that occur with "pendaftaran" but usually unrelated to the admission schedule
CONTEXT_NOISE_RE = re.compile(r"(?i)pendaftaran\s+kendaraan|pendaftaran\s+covid|donasi", re.I)

PDF_EXT = (".pdf",)
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")

# Deteksi tanggal umum (Indonesia & English)
DATE_HINT_RE = re.compile(
    r"(?i)\b("
    r"\d{1,2}\s*(januari|februari|maret|april|mei|juni|juli|agustus|"
    r"september|oktober|november|desember|"
    r"jan|feb|mar|apr|mei|jun|jul|agu|sep|okt|nov|des|"
    r"january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s*\d{2,4}|"
    r"\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}/\d{1,2}/\d{2,4}|"
    r"\d{1,2}\s*-\s*\d{1,2}\s*(jan|feb|mar|apr|mei|jun|jul|agu|sep|okt|nov|des)\s*\d{4}"
    r")\b"
)

# Deteksi rentang tanggal (misal: 1 Februari 2026 - 15 Maret 2026)
DATE_RANGE_RE = re.compile(
    r"(?is)"
    r"(\d{1,2}\s*[A-Za-z]+\s*\d{2,4}|\d{4}-\d{2}-\d{2})"
    r"\s*(?:-|–|—|s/d|s\.d\.|sd|hingga|to|sampai|until)\s*"
    r"(\d{1,2}\s*[A-Za-z]+\s*\d{2,4}|\d{4}-\d{2}-\d{2})"
)

JALUR_WORD_RE = re.compile(
    r"(?i)\b("
    r"snbp|snbt|mandiri|seleksi\s*mandiri|"
    r"ujian\s*mandiri|pmb|ppmb|snpmb|"
    r"jalur\s*(prestasi|reguler|internasional|rapor|undangan)|"
    r"gelombang\s*\d+|"
    r"jadwal|timeline|schedule|"
    r"iup|pbutm|pbub|sbub|sbupn|"
    r"pascasarjana|magister|profesi|spesialis|"
    r"penerimaan|pendaftaran|registrasi"
    r")\b"
)

# Hint jenjang pendidikan (untuk validasi halaman admission)
LEVEL_HINT_RE = re.compile(
    r"(?i)\b("
    r"s1|s2|s3|d[1-4]|diploma|sarjana|magister|doktor|"
    r"keprofesian|profesi|spesialis|pascasarjana|"
    r"undergraduate|graduate|master|phd|diploma"
    r")\b"
)

# Deteksi baris tabel yang mengandung nama jalur + tanggal
JALUR_DATE_ROW_RE = re.compile(
    r"(?is)\b([A-Za-z]{3,}(?:\s+[A-Za-z0-9]{2,}){0,8})\b"
    r"[^\n]{0,80}"
    r"(\d{1,2}\s*[A-Za-z]+\s*\d{2,4}|\d{4}-\d{2}-\d{2})"
)