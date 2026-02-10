from __future__ import annotations

import json
from typing import List, Dict, Any
from utils import slugify

EXTRACT_PROMPT = """Kamu extractor biaya kuliah kampus Indonesia untuk import database.

Keluaran HARUS JSON ketat (tanpa markdown) berupa array of objects.
Setiap object boleh berisi:
- name (string)
- slug (string)
- description (string)
- price_type: "fixed" atau "range" atau null
- fixed_price (number|null)
- min_price (number|null)
- max_price (number|null)
- payment_type (string|null)
- payment_frequency (string|null) contoh: "semester"|"year"|"once"
- promotion_type, discount_value, discount_unit, cashback_value, cashback_unit (boleh null)
- priceable_type (string|null) contoh "Campus" atau "StudyProgram" (kalau tidak yakin null)
- priceable_id (string|number|null)

Aturan KETAT (WAJIB dipatuhi):
1) Hanya item biaya pendidikan (UKT/SPP/SPI/IPI/uang pangkal/DPP/biaya semester/tahun) untuk kampus.
2) Setiap item HARUS mengandung minimal:
   - Nama PRODI/JURUSAN/PROGRAM (contoh: "Teknik Informatika"), dan
   - Nominal biaya (fixed atau range).
3) Jika jenjang ada (S1/S2/S3/D1-D4/Profesi/Spesialis/Pascasarjana), masukkan ke "name" atau "description".
4) Jika konten hanya menyebut biaya umum tanpa rincian prodi/jurusan + nominal, kembalikan [] (array kosong).
5) Angka harus numerik tanpa pemisah (contoh 3500000).
6) Jika ada banyak prodi + biaya, keluarkan banyak item (satu per prodi/jenjang).

Ekstrak dari konten berikut:
"""

def extract_fee_items_from_text(gemini, text: str) -> List[Dict[str, Any]]:
    raw = gemini.generate_text(EXTRACT_PROMPT + "\n\nKONTEN:\n" + text[:16000])
    data = json.loads(raw)
    out = []
    if isinstance(data, list):
        for obj in data:
            if not isinstance(obj, dict):
                continue
            name = (obj.get("name") or "").strip()
            if not name:
                continue
            obj["slug"] = (obj.get("slug") or "").strip() or slugify(name)
            out.append(obj)
    return out

def extract_fee_items_from_bytes(gemini, mime: str, data: bytes) -> List[Dict[str, Any]]:
    raw = gemini.generate_with_bytes(EXTRACT_PROMPT, data=data, mime_type=mime)
    data = json.loads(raw)
    out = []
    if isinstance(data, list):
        for obj in data:
            if not isinstance(obj, dict):
                continue
            name = (obj.get("name") or "").strip()
            if not name:
                continue
            obj["slug"] = (obj.get("slug") or "").strip() or slugify(name)
            out.append(obj)
    return out
