from __future__ import annotations
from typing import Any, Dict, Optional, List
import re

SCHEMA_PRODI: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "programs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "faculty": {"type": "string"},
                    "name": {"type": "string"},
                    "level": {"type": "string"},          # D3/D4/S1/S2/S3/Profesi/Sp-1/Sp-2
                    "accreditation": {"type": "string"},  # UNGGUL/BAIK SEKALI/BAIK/A/B/C/
                    "type": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["name"],
            },
        }
    },
    "required": ["programs"],
}

RULES_PRODI = """
ATURAN KETAT:
- Fokus hanya pada PROGRAM STUDI / JURUSAN (PRODI). Jangan ambil berita, event, organisasi, dll.
- Jika halaman berisi daftar fakultas, ambil prodi yang tercantum di bawah fakultas tersebut.
- level: salah satu: D3, D4, S1, S2, S3, Profesi, Sp-1, Sp-2, atau "-" jika tidak ada bukti.
- accreditation: ambil apa adanya dari bukti (UNGGUL/BAIK SEKALI/BAIK/A/B/C/dll), atau "-" jika tidak ada.
- type: array. Default ["Reguler"] jika tidak ada bukti (mis: Reguler, Internasional, Kelas Karyawan).
- faculty: isi nama fakultas jika ada, kalau tidak "-".
- description: ringkas 1-2 kalimat berdasarkan teks. Jika tidak ada "-".
- url: isi url sumber jika ada/terlihat, kalau tidak "-".
- OUTPUT HARUS JSON valid sesuai schema (tanpa teks lain).
""".strip()

LEVEL_MAP = {
    "diploma iii": "D3", "diploma 3": "D3", "d3": "D3",
    "diploma iv": "D4", "diploma 4": "D4", "d4": "D4", "sarjana terapan": "D4",
    "sarjana": "S1", "s1": "S1", "undergraduate": "S1",
    "magister": "S2", "master": "S2", "s2": "S2", "graduate": "S2",
    "doktor": "S3", "doctor": "S3", "phd": "S3", "s3": "S3",
    "profesi": "Profesi",
    "spesialis 1": "Sp-1", "sp-1": "Sp-1",
    "spesialis 2": "Sp-2", "sp-2": "Sp-2",
}

ACC_HINTS = [
    "unggul", "baik sekali", "baik", "a", "b", "c", "ba", "bs",
]

def normalize_level(s: str) -> str:
    t = (s or "").strip().lower()
    if not t:
        return "-"
    up = t.upper().replace(" ", "")
    if up in {"D3","D4","S1","S2","S3"}:
        return up
    for k, v in LEVEL_MAP.items():
        if k in t:
            return v
    # tangkap pola seperti "S1" "S2"
    m = re.search(r"\b(d3|d4|s1|s2|s3)\b", t)
    if m:
        return m.group(1).upper()
    return "-"

def normalize_accreditation(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return "-"
    low = t.lower()
    # biarkan jika mengandung kata penting
    if any(h in low for h in ["unggul", "baik"]):
        return t
    # A/B/C/BA/BS
    m = re.search(r"\b(ba|bs|a|b|c)\b", low)
    if m:
        return m.group(1).upper()
    return t[:40] if len(t) > 40 else t

def normalize_type(v) -> List[str]:
    if isinstance(v, list):
        arr = [str(x).strip() for x in v if str(x).strip()]
        return arr or ["Reguler"]
    s = str(v or "").strip()
    if not s or s == "-":
        return ["Reguler"]
    return [s]

def _clean_text(s: str, max_len: int = 4000) -> str:
    s = (s or "").strip()
    if not s:
        return "-"
    s = re.sub(r"\s+", " ", s)
    return s[:max_len]

def normalize_program_item(it: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(it, dict):
        return None
    name = _clean_text(str(it.get("name", "")).strip(), max_len=200)
    if not name or name == "-":
        return None

    faculty = _clean_text(str(it.get("faculty", "-")), max_len=200)
    level = normalize_level(str(it.get("level", "-")))
    acc = normalize_accreditation(str(it.get("accreditation", "-")))
    typ = normalize_type(it.get("type", ["Reguler"]))
    desc = _clean_text(str(it.get("description", "-")), max_len=2500)
    url = _clean_text(str(it.get("url", "-")), max_len=500)

    return {
        "faculty": faculty if faculty else "-",
        "name": name,
        "level": level,
        "accreditation": acc if acc else "-",
        "type": typ,
        "description": desc if desc else "-",
        "url": url if url else "-",
    }
