from __future__ import annotations

import json
from typing import Tuple

from datetime import datetime
from zoneinfo import ZoneInfo

from config import (
    JALUR_WORD_RE,
    DATE_HINT_RE,
    DATE_RANGE_RE,
    LEVEL_HINT_RE,
    JALUR_DATE_ROW_RE,
)
from utils import CandidateLink, ValidatedLink


VALIDATE_PROMPT = """Kamu adalah validator halaman jalur dan jadwal pendaftaran kampus Indonesia.

Tentukan apakah konten benar-benar memuat informasi:
- Jalur masuk (SNBP/SNBT/Mandiri/PMB/gelombang/dll) DAN
- Jadwal pendaftaran (tanggal mulai/akhir/periode pendaftaran).

Kriteria VALID (WAJIB):
1) Ada penyebutan jalur seleksi (contoh: SNBP, SNBT, Mandiri, PMB, Gelombang 1, dll), DAN
2) Ada informasi tanggal atau rentang tanggal pendaftaran, DAN
3) Konteks jelas tentang penerimaan mahasiswa baru (bukan berita umum/event/artikel)

ATURAN PENTING TANGGAL:
- Jika ada rentang tanggal, gunakan tanggal akhir sebagai end_date.
- Jika hanya ada satu tanggal penutupan, itu dianggap end_date.
- Jika tidak jelas tahun atau tanggal ambigu → INVALID.

INVALID jika:
- Hanya menyebut jalur tanpa tanggal,
- Hanya menyebut tanggal tanpa konteks jalur,
- Hanya berita/artikel tanpa info periode pendaftaran,

Jawab JSON ketat (tanpa markdown):
{
  "is_valid": true/false,
  "end_date_detected": "YYYY-MM-DD atau null",
  "evidence_snippet": "...(<=200 char)"
}
"""


# =========================
# Fast Local Gate (filter awal sebelum LLM)
# =========================
def fast_local_gate(text: str) -> bool:
    t = text or ""

    # Harus ada kata jalur/seleksi/mandiri/dll
    if not JALUR_WORD_RE.search(t):
        return False

    # Harus ada indikasi tanggal
    if not (DATE_HINT_RE.search(t) or DATE_RANGE_RE.search(t)):
        return False

    return True


# =========================
# Validate Text (HTML/text page)
# =========================
def validate_text_with_gemini(gemini, text: str) -> Tuple[str, str, str]:
    if not fast_local_gate(text):
        return "invalid", "", ""

    today = today_wib_str()

    raw = gemini.generate_text(
        VALIDATE_PROMPT
        + f"\n\nTODAY: {today}"
        + "\n\nKONTEN:\n"
        + text[:12000]
    )

    try:
        obj = json.loads(raw)
        ok = bool(obj.get("is_valid"))
        ev = (obj.get("evidence_snippet") or "")[:200]
        return ("valid" if ok else "invalid"), "", ev
    except Exception:
        return "uncertain", "", raw[:200]


# =========================
# Validate Binary (PDF, dll)
# =========================
def validate_bytes_with_gemini(gemini, mime: str, data: bytes) -> Tuple[str, str, str]:
    today = today_wib_str()

    raw = gemini.generate_with_bytes(
        VALIDATE_PROMPT + f"\n\nTODAY: {today}",
        data=data,
        mime_type=mime,
    )

    try:
        obj = json.loads(raw)
        ok = bool(obj.get("is_valid"))
        ev = (obj.get("evidence_snippet") or "")[:200]
        return ("valid" if ok else "invalid"), "", ev
    except Exception:
        return "uncertain", "", raw[:200]

def today_wib_str():
    return datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d")

# =========================
# Convert to ValidatedLink
# =========================
def to_validated(
    c: CandidateLink,
    verdict: str,
    reason: str,
    snippet: str,
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
