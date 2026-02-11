from __future__ import annotations

import json
import re
from typing import Tuple

from config import JALUR_WORD_RE, DATE_HINT_RE
from utils import CandidateLink, ValidatedLink, normalize_url


VALIDATE_PROMPT = """Kamu adalah validator halaman JALUR PENDAFTARAN mahasiswa baru kampus Indonesia.

Tentukan apakah konten benar-benar memuat informasi jalur pendaftaran mahasiswa baru.

VALID jika ada minimal salah satu:
- Nama jalur pendaftaran (mis. SNBP, SNBT, Mandiri, Prestasi, Afirmasi, Kerja Sama),
- Informasi seleksi/admisi (syarat, alur, tahapan, mekanisme pendaftaran),
- Informasi periode/tahun/gelombang pendaftaran yang jelas.

INVALID jika hanya:
- berita/pengumuman umum,
- profil kampus/fakultas,
- visi misi,
- artikel tanpa informasi jalur pendaftaran.
- informasi biaya kuliah,
- informasi daya tampung/kuota.

Jawab JSON ketat (tanpa markdown):
{"is_valid": true/false, "reason": "...", "evidence_snippet": "...(<=200 char)"}.
"""
HARD_CONTENT_REJECT = [
    "daya tampung",
    "kapasitas",
    "kuota",
    "jumlah mahasiswa",
    "per prodi",
    "program studi",
    "fakultas",
    "biaya",
    "fee",
    "ukt",
    "bayar",
    "berkas",
    "dokumen",
    "animo",
    "ppds",
    "pengumuman",
    "syarat dan ketentuan",
    "pembelajaran",
    "forum",
]

def _content_is_definition_page(text: str) -> bool:
    t = text.lower()

    must_have = (
        "pendaftaran" in t
        or "jadwal" in t
        or "tahapan seleksi" in t
        or "alur seleksi" in t
    )


    reject = any(k in t for k in HARD_CONTENT_REJECT)

    return must_have and not reject

def _fast_local_gate(text: str) -> bool:
    t = (text or "").lower()

    has_jalur = bool(JALUR_WORD_RE.search(t))
    has_time_signal = (
        "jadwal" in t
        or "timeline" in t
        or "periode" in t
        or "gelombang" in t
        or DATE_HINT_RE.search(t)
        or re.search(r"20\d{2}", t)
    )

    return has_jalur and has_time_signal




def validate_text_with_gemini(gemini, text: str, url: str, official_website: str) -> Tuple[str, str, str]:
    
     # 🔥 FIX UTAMA — homepage bukan halaman jalur
    if normalize_url(url).rstrip("/") == normalize_url(official_website).rstrip("/"):
        return "invalid", "homepage is not admission page", ""
    
    if not _fast_local_gate(text):
        return "invalid", "local gate: no jalur keyword + no date/period hint", ""

    raw = gemini.generate_text(
        VALIDATE_PROMPT + "\n\nKONTEN:\n" + text[:12000]
    )

    try:
        obj = json.loads(raw)
        ok = bool(obj.get("is_valid"))
        reason = (obj.get("reason") or "")[:200]
        ev = (obj.get("evidence_snippet") or "")[:200]
        return ("valid" if ok else "invalid"), reason, ev
    except Exception:
        return "uncertain", "gemini output not strict json", raw[:200]


def validate_bytes_with_gemini(
    gemini,
    mime: str,
    data: bytes
) -> Tuple[str, str, str]:
    raw = gemini.generate_with_bytes(
        VALIDATE_PROMPT,
        data=data,
        mime_type=mime,
    )

    try:
        obj = json.loads(raw)
        ok = bool(obj.get("is_valid"))
        reason = (obj.get("reason") or "")[:200]
        ev = (obj.get("evidence_snippet") or "")[:200]
        return ("valid" if ok else "invalid"), reason, ev
    except Exception:
        return "uncertain", "gemini output not strict json", raw[:200]


def to_validated(
    c: CandidateLink,
    verdict: str,
    reason: str,
    snippet: str
) -> ValidatedLink:
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
