from __future__ import annotations
from typing import Any, Dict, Optional, List
import re

SCHEMA_JURUSAN: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "programs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "faculty": {"type": "string"},
                    "description": {"type": "string"},
                    "skills": {"type": "string"},
                    "reasons": {"type": "string"},   
                    "url": {"type": "string"},
                    "jobable": {
                        "type": "array",
                        "items": {"type": "integer"}
                        }
                },
                "required": ["name"],
            },
        }
    },
    "required": ["programs"],
}

RULES_JURUSAN = """
ATURAN KETAT:
- Fokus hanya pada FAKULTAS / JURUSAN UMUM / RUMPUN ILMU.
- JANGAN ambil program studi, jenjang, akreditasi, atau detail per-prodi.
- Nama jurusan harus bersifat umum (contoh: Ilmu Teknik dan Rekayasa).
- description: ringkas 1–2 kalimat tentang bidang keilmuan.
- skills: ringkas kemampuan umum lulusan bidang tersebut.
- reasons: alasan memilih jurusan tersebut (prospek, peran keilmuan, dll).
- faculty: isi jika nama fakultas eksplisit, jika tidak "-".
- url: sumber halaman jika ada, jika tidak "-".
- OUTPUT HARUS JSON valid sesuai schema (tanpa teks lain).
""".strip()



def _clean_text(s: str, max_len: int = 4000) -> str:
    s = (s or "").strip()
    if not s:
        return "-"
    s = re.sub(r"\s+", " ", s)
    return s[:max_len]

def normalize_jurusan_item(it: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(it, dict):
        return None

    name = _clean_text(it.get("name", ""), max_len=200)
    if not name or name == "-":
        return None

    faculty = _clean_text(it.get("faculty", "-"), max_len=200)
    description = _clean_text(it.get("description", "-"), max_len=2500)
    skills = _clean_text(it.get("skills", "-"), max_len=1000)
    reasons = _clean_text(it.get("reasons", "-"), max_len=1000)
    url = _clean_text(it.get("url", "-"), max_len=500)

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    
    job_ids = it.get("jobable", [])
    if not isinstance(job_ids, list):
        job_ids = []
        job_ids = [int(x) for x in job_ids if str(x).isdigit()]

    return {
        "id": None,
        "category_id": None,      # bisa diisi mapping belakangan
        "name": name,
        "slug": slug,
        "description": description if description else "-",
        "is_active": True,
        "created_at": None,
        "updated_at": None,
        "deleted_at": None,
        "created_by": None,
        "updated_by": None,
        "deleted_by": None,
        "skills": skills if skills else "-",
        "reasons": reasons if reasons else "-",
        "jobable": job_ids,
    }
