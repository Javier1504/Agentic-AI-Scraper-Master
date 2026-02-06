from __future__ import annotations

import json
import re
import time
from typing import Tuple

from config import MONEY_HINT_RE, FEE_WORD_RE, PRODI_HINT_RE, LEVEL_HINT_RE
from utils import CandidateLink, ValidatedLink

VALIDATE_PROMPT = """Kamu adalah validator halaman/asset biaya kuliah kampus Indonesia.

Tugas: tentukan apakah konten benar-benar berisi *data biaya pendidikan* yang bisa dipetakan ke program studi/jurusan/jenjang.

Kriteria VALID (WAJIB terpenuhi):
1) Ada nominal biaya (angka, mis. Rp 3.500.000 atau 3500000), DAN
2) Ada konteks program: minimal salah satu dari berikut:
   - nama prodi/jurusan/program studi, ATAU
   - jenjang/program (S1/S2/S3/D1-D4/Profesi/Pascasarjana/RPL), ATAU
   - tabel/list yang jelas memuat banyak baris program + biaya.

INVALID jika hanya artikel/berita/pengumuman umum tanpa pairing program+biaya.

Jawab JSON ketat (tanpa markdown), format:
{"is_valid": true/false, "reason": "...", "evidence_snippet": "...(<=200 char)"}
"""

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.S)


def _fast_local_gate(text: str) -> bool:
    t = text or ""
    # Gate cepat: harus ada uang + (fee word atau hint prodi/jenjang)
    if not MONEY_HINT_RE.search(t):
        return False
    if FEE_WORD_RE.search(t):
        return True
    return bool(PRODI_HINT_RE.search(t) or LEVEL_HINT_RE.search(t))


def _parse_json_lenient(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        m = _JSON_OBJ_RE.search(raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _retry_call(fn, *args, tries: int = 3, backoff_s: float = 1.3, **kwargs) -> str:
    last = ""
    for i in range(tries):
        try:
            last = fn(*args, **kwargs) or ""
            if last.strip():
                return last
        except Exception as e:
            last = f"[EXC]{type(e).__name__}:{e}"
        time.sleep(backoff_s * (i + 1))
    return last


def validate_text_with_gemini(gemini, text: str) -> Tuple[str, str, str]:
    if not _fast_local_gate(text):
        return "invalid", "local gate: need (money) + (fee context) + (prodi/jenjang)", ""

    raw = _retry_call(gemini.generate_text, VALIDATE_PROMPT + "\n\nKONTEN:\n" + (text or "")[:12000])
    obj = _parse_json_lenient(raw)
    if not obj:
        return "uncertain", "gemini output not json/empty", (raw or "")[:200]

    ok = bool(obj.get("is_valid"))
    reason = (obj.get("reason") or "")[:200]
    ev = (obj.get("evidence_snippet") or "")[:200]
    return ("valid" if ok else "invalid"), reason, ev


def validate_bytes_with_gemini(gemini, mime: str, data: bytes) -> Tuple[str, str, str]:
    raw = _retry_call(gemini.generate_with_bytes, VALIDATE_PROMPT, data=data, mime_type=mime)
    obj = _parse_json_lenient(raw)
    if not obj:
        return "uncertain", "gemini output not json/empty", (raw or "")[:200]

    ok = bool(obj.get("is_valid"))
    reason = (obj.get("reason") or "")[:200]
    ev = (obj.get("evidence_snippet") or "")[:200]
    return ("valid" if ok else "invalid"), reason, ev


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
