from __future__ import annotations

import json
from typing import List, Dict, Any
from utils import slugify

EXTRACT_PROMPT = """Kamu extractor jalur dan jadwal pendaftaran kampus Indonesia untuk import database.

Keluaran HARUS JSON ketat (tanpa markdown) berupa array of objects.
Setiap object boleh berisi:
- name (string) → nama jalur (contoh: "SNBP", "SNBT", "Mandiri Reguler", "Pascasarjana Gelombang 1")
- slug (string)
- description (string)
- start_date (string|null) format YYYY-MM-DD
- end_date (string|null) format YYYY-MM-DD
- url (string|null)
- is_active (boolean|null)

Aturan KETAT (WAJIB dipatuhi):
1) Hanya jalur pendaftaran resmi kampus (SNBP, SNBT, Mandiri, Gelombang 1/2/3, Pascasarjana, Profesi, dll).
2) Setiap item HARUS mengandung minimal:
   - Nama jalur (name)
   - Minimal salah satu tanggal (start_date atau end_date).
3) Jika ada beberapa gelombang atau beberapa jalur, keluarkan banyak item (satu per jalur/gelombang).
4) Format tanggal HARUS YYYY-MM-DD.
5) Jika tidak ada informasi jalur + tanggal pendaftaran yang jelas, kembalikan [] (array kosong).
6) Jangan mengarang tanggal.

Ekstrak dari konten berikut:
"""


def extract_jalur_items_from_text(gemini, text: str) -> List[Dict[str, Any]]:
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

            start_date = obj.get("start_date")
            end_date = obj.get("end_date")

            # minimal harus ada salah satu tanggal
            if not start_date and not end_date:
                continue

            obj["slug"] = (obj.get("slug") or "").strip() or slugify(name)
            out.append(obj)

    return out


def extract_jalur_items_from_bytes(gemini, mime: str, data: bytes) -> List[Dict[str, Any]]:
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

            start_date = obj.get("start_date")
            end_date = obj.get("end_date")

            if not start_date and not end_date:
                continue

            obj["slug"] = (obj.get("slug") or "").strip() or slugify(name)
            out.append(obj)

    return out
