from __future__ import annotations

import json
import re
from typing import List, Dict, Any
from utils import slugify

EXTRACT_PROMPT = """Kamu extractor jalur dan jadwal pendaftaran kampus Indonesia untuk import database.

Keluaran HARUS JSON ketat (tanpa markdown) berupa array of objects.
Setiap object boleh berisi:
- name (string) → nama jalur (contoh: "SNBP", "SNBT", "Mandiri Reguler", "Pascasarjana Gelombang 1", "IUP Batch 1")
- slug (string)
- description (string)
- start_date (string|null) format YYYY-MM-DD
- end_date (string|null) format YYYY-MM-DD
- url (string|null)
- is_active (boolean|null)

Aturan KETAT (WAJIB dipatuhi):
1) Hanya jalur pendaftaran resmi kampus (SNBP, SNBT, Mandiri, Gelombang 1/2/3, Pascasarjana, Profesi, IUP, dll).
2) Setiap item HARUS mengandung minimal:
   - Nama jalur (name)
   - Minimal salah satu tanggal (start_date atau end_date).
3) Jika ada beberapa gelombang atau beberapa jalur, keluarkan banyak item (satu per jalur/gelombang).
4) Format tanggal HARUS YYYY-MM-DD (contoh: 2026-01-15).
5) RELAXED: Jika hanya ada 1 tanggal (tanpa tahun jelas), GUNAKAN tahun saat ini (2026).
6) Jangan mengarang tanggal - jika absen/unclear, gunakan null.
7) Jangan menyertakan item yang hanya berupa berita umum, artikel, atau event tanpa info jalur + tanggal pendaftaran.
8) Jangan menyertakan item yang hanya berupa info biaya kuliah, program studi, atau info kampus umum tanpa info jalur + tanggal pendaftaran.

CONTOH OUTPUT YANG BENAR:
[
  {
    "name": "SNBP 2026",
    "slug": "snbp-2026",
    "description": "Jalur SNBP untuk Sarjana",
    "start_date": "2026-01-15",
    "end_date": "2026-02-28",
    "is_active": true
  },
  {
    "name": "IUP Batch 1",
    "slug": "iup-batch-1",
    "description": "International Undergraduate Program Batch 1",
    "start_date": "2026-01-26",
    "end_date": "2026-02-27",
    "is_active": true
  }
]

Ekstrak dari konten berikut:
"""


def _preprocess_text(text: str) -> str:
    """Preprocess text untuk extraction yang lebih baik"""
    if not text:
        return ""
    
    # Remove multiple spaces
    text = re.sub(r'\s+', ' ', text)
    
    # Common replacements
    text = text.replace('\n', ' ')
    text = text.replace('\r', ' ')
    text = text.replace('\t', ' ')
    
    # Normalize date separators (– to -)
    text = text.replace('–', '-')
    text = text.replace('—', '-')
    
    return text.strip()


def extract_jalur_items_from_text(gemini, text: str) -> List[Dict[str, Any]]:
    # Preprocess text
    text = _preprocess_text(text)
    
    if not text or len(text) < 50:
        return []
    
    raw = gemini.generate_text(EXTRACT_PROMPT + "\n\nKONTEN:\n" + text[:20000])  # Increase dari 16000

    if not raw or not raw.strip():
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        # Try to extract JSON if LLM added extra text
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
            except:
                print(f"Failed to parse extracted JSON: {e}")
                return []
        else:
            return []
    
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
            
            print(f"Extracted Item: name={name}, start_date={start_date}, end_date={end_date}, slug={obj['slug']}")
            
    return out



def extract_jalur_items_from_bytes(gemini, mime: str, data: bytes) -> List[Dict[str, Any]]:
    raw = gemini.generate_with_bytes(EXTRACT_PROMPT, data=data, mime_type=mime)
    if not raw or not raw.strip():
        return []

    try:
        data_obj = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if json_match:
            try:
                data_obj = json.loads(json_match.group())
            except:
                return []
        else:
            return []
    
    out = []

    if isinstance(data_obj, list):
        for obj in data_obj:
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
            
            print(f"Extracted Item (from bytes): name={name}, start_date={start_date}, end_date={end_date}, slug={obj['slug']}")

    return out