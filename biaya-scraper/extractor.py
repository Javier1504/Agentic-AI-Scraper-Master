from __future__ import annotations

import json
import re
import time
from typing import List, Dict, Any, Optional

from utils import slugify

EXTRACT_PROMPT = """Kamu extractor biaya kuliah/UKT kampus Indonesia untuk import database.

Output HARUS JSON ketat (tanpa markdown), salah satu bentuk:
1) Array of objects
2) Object dengan field "items" (array of objects)

Fokus ekstraksi (PENTING):
- HANYA ambil baris yang memetakan **program studi/jurusan** atau **jenjang/program (S1/S2/S3/D1-D4/Profesi/Pascasarjana)** ke **nominal biaya**.
- Jika sudah menemukan tabel/daftar utama *biaya kuliah/UKT/SPP per prodi/jenjang*, JANGAN mengekstrak bagian lain di halaman (mis. berita, syarat pendaftaran, jalur masuk, beasiswa, diskon umum, informasi promo, kalender, dll).
- Abaikan biaya yang tidak terkait per prodi/jenjang (mis. formulir, pendaftaran, registrasi/herregistrasi) KECUALI jelas ada pairing prodi/jenjang + nominal.

Skema item:
- name (string) WAJIB: nama prodi/jurusan atau jenjang + prodi (mis. "S1 Informatika", "D3 Akuntansi")
- slug (string) opsional
- description (string) opsional (singkat, boleh kosong)
- price_type: "fixed" atau "range" atau null
- fixed_price (number|null)
- min_price (number|null)
- max_price (number|null)
- payment_type (string|null) mis. "UKT", "SPP", "Tuition"
- payment_frequency (string|null) mis. "per_semester" / "per_tahun" / null
- promotion_type, discount_value, discount_unit, cashback_value, cashback_unit (boleh null)
- priceable_type (string|null) mis. "Campus" atau "StudyProgram"
- priceable_id (string|number|null)

Aturan angka:
- Nominal harus numerik tanpa pemisah (contoh 3500000).
- Jangan buat angka perkiraan. Jika tidak ada nominal, jangan keluarkan item itu.
"""

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.S)

from config import MONEY_HINT_RE, PRODI_HINT_RE, LEVEL_HINT_RE

_NOISE_RE = re.compile(r"(?i)\b(berita|news|event|agenda|pengumuman|artikel|blog|visi|misi|beasiswa|scholarship|jadwal|kalender|alur|syarat|ketentuan|pembayaran|cara\s+bayar|pembiayaan|cicil|angsuran|promo|diskon|potongan|cashback|faq)\b")
_GENERIC_NON_PROGRAM_FEE_RE = re.compile(r"(?i)\b(formulir|pendaftaran|registrasi|herregistrasi|daftar\s+ulang|administrasi|materai|seragam|asrama|ujian|tes|wisuda)\b")

def _has_price(it: Dict[str, Any]) -> bool:
    return any(it.get(k) not in (None, "", 0) for k in ("fixed_price", "min_price", "max_price"))

def _looks_like_program_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    # must contain letters
    if not re.search(r"[A-Za-zÀ-ÿ]", n):
        return False
    # avoid pure generic fee lines unless they also contain prodi/jenjang cues
    if _NOISE_RE.search(n):
        return False
    if _GENERIC_NON_PROGRAM_FEE_RE.search(n) and not (LEVEL_HINT_RE.search(n) or PRODI_HINT_RE.search(n)):
        return False
    return True

def narrow_fee_items(items: List[Dict[str, Any]], max_items: int = 120) -> List[Dict[str, Any]]:
    """
    Tujuan: batasi hasil agar tidak terlalu luas.
    Strategi:
    - pilih item yang punya nominal + terlihat seperti prodi/jenjang (atau nama prodi yang masuk akal)
    - ambil blok paling awal (kontigu) yang relevan; berhenti ketika mulai masuk bagian lain
    """
    if not items:
        return []
    kept: List[Dict[str, Any]] = []
    started = False
    bad_streak = 0
    for it in items:
        name = (it.get("name") or "").strip()
        ok = _has_price(it) and _looks_like_program_name(name) and (LEVEL_HINT_RE.search(name) or PRODI_HINT_RE.search(name) or True)
        # ok uses _looks_like_program_name as primary; allow prodi without explicit keywords
        if ok:
            kept.append(it)
            started = True
            bad_streak = 0
        else:
            if started:
                bad_streak += 1
                # once we already have enough, stop if we keep seeing unrelated lines
                if len(kept) >= 8 and bad_streak >= 5:
                    break
    # If nothing kept via contiguous scan, fall back to global filter (still strict)
    if not kept:
        kept = [it for it in items if _has_price(it) and _looks_like_program_name((it.get("name") or ""))]
    return kept[:max_items]



def _retry_call(fn, *args, tries: int = 4, backoff: float = 1.3, **kwargs) -> str:
    last = ""
    for i in range(tries):
        try:
            last = fn(*args, **kwargs) or ""
            if last.strip():
                return last
        except Exception as e:
            last = f"[EXC]{type(e).__name__}:{e}"
        time.sleep(backoff * (i + 1))
    return last


def _parse_json_lenient(raw: str) -> Any:
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


def _coerce_items(data: Any) -> List[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "data", "results"):
            v = data.get(key)
            if isinstance(v, list):
                return v
    return []


def _digits_to_int(v: Any) -> Any:
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        digits = re.sub(r"[^0-9]", "", s)
        if digits:
            try:
                return int(digits)
            except Exception:
                return v
    return v


def _normalize_item(x: Any) -> Optional[Dict[str, Any]]:
    # model kadang mengembalikan string; jadikan item minimal agar pipeline tidak crash
    if isinstance(x, dict):
        obj = dict(x)
    elif isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        obj = {"name": s}
    else:
        return None

    name = (obj.get("name") or "").strip()
    if not name:
        return None

    obj["slug"] = (obj.get("slug") or "").strip() or slugify(name)

    # normalize numeric fields
    for k in ("fixed_price", "min_price", "max_price", "discount_value", "cashback_value"):
        if k in obj:
            obj[k] = _digits_to_int(obj.get(k))

    return obj


def extract_fee_items_from_text(gemini, text: str) -> List[Dict[str, Any]]:
    raw = _retry_call(gemini.generate_text, EXTRACT_PROMPT + "\n\nKONTEN:\n" + (text or "")[:16000], tries=4)
    parsed = _parse_json_lenient(raw)
    items_any = _coerce_items(parsed)

    out: List[Dict[str, Any]] = []
    for it in items_any:
        norm = _normalize_item(it)
        if norm:
            out.append(norm)
    return out


def extract_fee_items_from_bytes(gemini, mime: str, data: bytes) -> List[Dict[str, Any]]:
    raw = _retry_call(gemini.generate_with_bytes, EXTRACT_PROMPT, data=data, mime_type=mime, tries=4)
    parsed = _parse_json_lenient(raw)
    items_any = _coerce_items(parsed)

    out: List[Dict[str, Any]] = []
    for it in items_any:
        norm = _normalize_item(it)
        if norm:
            out.append(norm)
    return out
