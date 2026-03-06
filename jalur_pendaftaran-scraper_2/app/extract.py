from datetime import datetime
import json
import re
from zoneinfo import ZoneInfo
from pypdf import PdfReader

from .gemini_client import call_gemini_json
from .utils import slugify_simple

ADMISSION_KW = [
    "jalur pendaftaran",
    "jadwal pendaftaran",
    "pmb",
    "penerimaan mahasiswa baru",
    "seleksi mandiri",
    "snpm",
    "snbp",
    "utbk",
    "jadwal seleksi",
    "timeline pendaftaran"
]

def _focus_snippet(text: str, window: int = 2500, max_snips: int = 6) -> str:
    t = text
    lower = t.lower()
    snips = []
    for kw in ADMISSION_KW:
        idx = 0
        while True:
            pos = lower.find(kw, idx)
            if pos == -1:
                break
            start = max(0, pos - window)
            end = min(len(t), pos + window)
            snips.append(t[start:end])
            idx = pos + len(kw)
            if len(snips) >= max_snips:
                break
        if len(snips) >= max_snips:
            break
    if snips:
        return ("\n\n---\n\n".join(snips))[:120_000]
    return t[:120_000]

def _pdf_bytes_to_text(pdf_bytes: bytes, max_chars: int = 120_000) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))  # noqa: F821
    except Exception:
        import io
        reader = PdfReader(io.BytesIO(pdf_bytes))
    out = []
    for page in reader.pages[:15]:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            continue
    text = "\n".join(out)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]

def extract_jalur_from_url(url: str, fetcher, gemini_api_key: str, model: str, university_id):
    fetched = fetcher.fetch(url)
    if not fetched.get("ok"):
        return []

    final_url = fetched.get("final_url") or url
    ct = (fetched.get("content_type") or "").lower()

    # Ambil teks dari HTML atau PDF
    if ("application/pdf" in ct) or final_url.lower().endswith(".pdf"):
        if not fetched.get("content_bytes"):
            return []
        text = _pdf_bytes_to_text(fetched["content_bytes"])
    else:
        if not fetched.get("text"):
            return []
        text = fetcher.html_to_text(fetched["text"])

    if not text:
        return []

    # Filter ringan: kalau gak ada keyword ADMISSION sama sekali, skip
    if not any(k in text.lower() for k in ADMISSION_KW):
        return []

    focused = _focus_snippet(text)

    prompt = (
        "Kamu adalah extractor informasi jalur pendaftaran universitas Indonesia.\n"
        "Ekstrak jalur pendaftaran dan jadwalnya.\n"
        "Keluarkan JSON VALID SAJA.\n\n"

        "Skema JSON:\n"
        "{\n"
        '  "items": [\n'
        "    {\n"
        '      "name": "string",\n'
        '      "description": "string|null",\n'
        '      "start_date": "YYYY-MM-DD|null",\n'
        '      "end_date": "YYYY-MM-DD|null"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"

        "Aturan:\n"
        "- Jalur bisa berupa SNBP, SNBT, Mandiri, Prestasi, International, dll\n"
        "- start_date = tanggal mulai pendaftaran\n"
        "- end_date = tanggal penutupan pendaftaran\n"
        "- Jika tidak ada tanggal, isi null\n"
        "- Format tanggal harus YYYY-MM-DD\n\n"

        "TEKS:\n<<<\n"
        f"{focused}\n"
        ">>>\n"
    )

    raw = call_gemini_json(gemini_api_key, model, prompt)
    try:
        data = json.loads(raw)
    except Exception:
        return []

    def to_int(x):
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return int(x)
        s = str(x)
        s = re.sub(r"[^\d]", "", s)  # buang Rp/titik/koma/spasi
        return int(s) if s else None
    
    
    def now_wib_str():
        return datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M:%S")


    items = []
    for it in (data.get("items") or []):
        jalur_name = (it.get("name") or "").strip()
        now_wib = now_wib_str()
        if not jalur_name:
            continue

        items.append({
            "id": None,
            "university_id": university_id,
            "name": jalur_name,
            "slug": slugify_simple(jalur_name),
            "description": it.get("description"),
            "start_date": it.get("start_date"),
            "end_date": it.get("end_date"),
            "url": final_url,
            "is_active": True,
            "created_at": now_wib,
            "updated_at": now_wib,
            "deleted_at": None,
            "created_by": None,
            "updated_by": None,
            "deleted_by": None,
        })

    return items