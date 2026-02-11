from __future__ import annotations
import re

# =========================================================
# KEYWORDS JALUR PENDAFTARAN
# =========================================================
JALUR_KEYWORDS = [
    "jalur pendaftaran", "jalur seleksi", "jalur masuk", "penerimaan mahasiswa baru",
    "pmb", "spmb", "admisi", "admission", "selma",
    "snbp", "snbt", "snmptn", "sbmptn",
    "mandiri", "rpl", "jadwal seleksi", "tahapan seleksi", "prestasi", "cbt",
    "reguler", "internasional", "kelas internasional",
]

# =========================================================
# KEYWORDS NOISE (HALAMAN TIDAK RELEVAN)
# =========================================================
NOISE_KEYWORDS = [
    "berita", "news", "event", "agenda", "artikel", "press",
    "galeri", "gallery", "opini", "blog",
    "riset", "penelitian", "tentang kami","tentang", "about us", "biaya", "fee","ukt",
    "kontak", "contact", "lokasi", "location", "peta situs", "sitemap",
    "karir", "career", "alumni",
    "profil", "profile", "sejarah", "history",
    "visi", "misi", "kemahasiswaan",
    "beasiswa", "scholarship",
    "download", "repository", "perpustakaan", "library"
]

# =========================================================
# FILE EXTENSION YANG DIABAIKAN
# =========================================================
PDF_EXT = (".pdf",)
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")

# =========================================================
# REGEX HINT UNTUK JALUR PENDAFTARAN
# (tahun, gelombang, periode, dll)
# =========================================================
DATE_HINT_RE = re.compile(
    r"(?i)\b(20\d{2}|gelombang\s*\d+|periode\s*\d+|tahun\s*akademik)\b"
)

JALUR_WORD_RE = re.compile(
    r"""(?i)\b(
        jalur\s*(pendaftaran|seleksi|masuk) |
        penerimaan\s*mahasiswa |
        rpl | jadwal seleksi | tahapan seleksi
        mahasiswa\s*baru |
        pmb | spmb |
        snbp | snbt | snmptn | sbmptn |
        mandiri | prestasi | afirmasi | kerjasama |
        reguler | internasional 
    )\b""",
    re.VERBOSE
)
