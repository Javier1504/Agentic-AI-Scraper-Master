from __future__ import annotations

import json
from typing import Tuple

from config import MONEY_HINT_RE, FEE_WORD_RE, PRODI_HINT_RE, LEVEL_HINT_RE, PRODI_NAME_RE, PRODI_MONEY_ROW_RE
from utils import CandidateLink, ValidatedLink

VALIDATE_PROMPT = """Kamu adalah validator halaman biaya kuliah kampus Indonesia.

Tentukan apakah konten benar-benar memuat informasi biaya pendidikan (UKT/SPP/SPI/IPI/IPI/uang pangkal).

Kriteria VALID *ketat* (WAJIB):
1) Ada nominal biaya (angka) dan
2) Ada konteks PROGRAM/JENJANG, yaitu salah satu:
   - nama prodi/jurusan/program studi/departemen/fakultas, atau
   - jenjang (S1/S2/S3/D1–D4/Profesi/Spesialis/Pascasarjana),
3) dan konteksnya jelas biaya pendidikan (UKT/SPP/SPI/IPI/uang pangkal/biaya pendidikan) bukan angka lain (tanggal, NIM, telepon, statistik).

INVALID jika:
- hanya artikel/berita/pengumuman tanpa daftar prodi/jenjang + nominal, atau
- ada angka tapi bukan biaya pendidikan, atau
- hanya info umum "biaya kuliah" tanpa rincian prodi/jenjang.

Jawab JSON ketat (tanpa markdown):
{"is_valid": true/false, "evidence_snippet": "...(<=200 char)"}.
"""

def fast_local_gate(text: str) -> bool:
    t = text or ""
    if not MONEY_HINT_RE.search(t):
        return False
    if not (FEE_WORD_RE.search(t) or "ukt" in t.lower() or "biaya" in t.lower() or "tuition" in t.lower()):
        return False
    # prodi bisa muncul sebagai kata "prodi"/"program studi" atau langsung nama jurusan,
    # atau pola baris tabel: <nama> + <nominal>.
    has_prodi = bool(PRODI_HINT_RE.search(t) or PRODI_NAME_RE.search(t) or PRODI_MONEY_ROW_RE.search(t))
    has_level = bool(LEVEL_HINT_RE.search(t))
    return bool(has_prodi or has_level)

def validate_text_with_gemini(gemini, text: str) -> Tuple[str, str, str]:
    if not fast_local_gate(text):
        return "invalid", "", ""

    raw = gemini.generate_text(VALIDATE_PROMPT + "\n\nKONTEN:\n" + text[:12000])
    try:
        obj = json.loads(raw)
        ok = bool(obj.get("is_valid"))
        ev = (obj.get("evidence_snippet") or "")[:200]
        return ("valid" if ok else "invalid"), "", ev
    except Exception:
        return "uncertain", "", raw[:200]

def validate_bytes_with_gemini(gemini, mime: str, data: bytes) -> Tuple[str, str, str]:
    raw = gemini.generate_with_bytes(VALIDATE_PROMPT, data=data, mime_type=mime)
    try:
        obj = json.loads(raw)
        ok = bool(obj.get("is_valid"))
        ev = (obj.get("evidence_snippet") or "")[:200]
        return ("valid" if ok else "invalid"), "", ev
    except Exception:
        return "uncertain", "", raw[:200]

def to_validated(c: CandidateLink, verdict: str, reason: str, snippet: str) -> ValidatedLink:
    return ValidatedLink(
        campus_name=c.campus_name,
        official_website=c.official_website,
        url=c.url,
        kind=c.kind,
        source_page=c.source_page,
        verdict=verdict,
        reason=reason,
        extracted_hint=snippet,
    )
