from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from gemini_client import GeminiClient


def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


@dataclass
class JobRecord:
    name: str
    slug: str
    description: str
    min_salary: int
    max_salary: int
    positions: List[str]

    def to_row(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "min_salary": self.min_salary,
            "max_salary": self.max_salary,
            "positions": self.positions,
        }


JOB_JSON_SCHEMA_HINT = {
    "required": ["name", "description", "min_salary", "max_salary", "positions"]
}


def enrich_job_with_gemini(gem: GeminiClient, job_title: str) -> JobRecord:
    system = (
        "Anda adalah asisten data untuk mengisi database pekerjaan (jobs). "
        "Tugas Anda: dari satu nama pekerjaan, buat ringkasan deskripsi kerja, "
        "rentang gaji bulanan di Indonesia (perkiraan wajar), dan daftar posisi terkait."
    )

    user = f"""
Nama pekerjaan: "{job_title}"

Keluarkan JSON dengan struktur:
{{
  "name": "<nama pekerjaan dalam Title Case Indonesia>",
  "description": "<1-2 kalimat, jelas, bukan promosi>",
  "min_salary": <integer gaji minimum per bulan (IDR)>,
  "max_salary": <integer gaji maksimum per bulan (IDR)>,
  "positions": ["<posisi terkait 1>", "..."]
}}

Aturan:
- positions minimal 3 item, maksimal 12 item.
- min_salary <= max_salary.
- Jika benar-benar tidak yakin, tetap isi angka konservatif yang masuk akal (bukan 0).
"""

    data = gem.generate_json(system=system, user=user, schema_hint=JOB_JSON_SCHEMA_HINT)

    name = str(data["name"]).strip() or job_title.strip()
    desc = str(data["description"]).strip()

    try:
        min_sal = int(data["min_salary"])
    except Exception:
        min_sal = 0
    try:
        max_sal = int(data["max_salary"])
    except Exception:
        max_sal = 0

    pos = data.get("positions") or []
    if not isinstance(pos, list):
        pos = []
    pos = [str(x).strip() for x in pos if str(x).strip()]
    pos = pos[:12]

    if min_sal and max_sal and min_sal > max_sal:
        min_sal, max_sal = max_sal, min_sal

    return JobRecord(
        name=name,
        slug=slugify(name),
        description=desc,
        min_salary=min_sal,
        max_salary=max_sal,
        positions=pos,
    )


def enrich_jobs_with_gemini_batch(
    gem: GeminiClient,
    job_titles: List[str],
) -> List[Tuple[str, JobRecord]]:
    """
    Batch enrichment: 1 panggilan Gemini untuk banyak job title.

    Return: list tuple (source_title, JobRecord)
    - source_title = judul input (untuk checkpoint resume yang akurat)
    """
    system = (
        "Anda adalah asisten data untuk mengisi database pekerjaan (jobs). "
        "Untuk setiap nama pekerjaan, buat deskripsi singkat, rentang gaji bulanan IDR, "
        "dan daftar posisi terkait."
    )

    jobs_txt = "\n".join([f"- {t}" for t in job_titles])

    user = f"""
Daftar pekerjaan (INPUT):
{jobs_txt}

Keluarkan JSON ARRAY (list) dengan jumlah item SAMA seperti input.
SETIAP item wajib memiliki field 'source_title' yang persis sama dengan salah satu item input.

Struktur setiap item:
{{
  "source_title": "<SAMA persis dengan input>",
  "name": "<Title Case Indonesia>",
  "description": "<1-2 kalimat, jelas, bukan promosi>",
  "min_salary": <integer IDR>,
  "max_salary": <integer IDR>,
  "positions": ["<posisi terkait 1>", "..."]
}}

Aturan:
- positions minimal 3, maksimal 7.
- min_salary <= max_salary.
- Jangan mengeluarkan markdown/backticks.
- Jangan menambahkan label seperti "json" di awal.
- Jika ada instansi/lembaga (misal 'OJK', 'Kementerian'), ubah menjadi NAMA POSISI yang relevan.
"""

    data = gem.generate_json(
        system=system,
        user=user,
        schema_hint={"required": ["source_title", "name", "description", "min_salary", "max_salary", "positions"]},
    )

    if not isinstance(data, list):
        raise ValueError("Batch enrich: output bukan JSON array/list.")

    out: List[Tuple[str, JobRecord]] = []

    for item in data:
        if not isinstance(item, dict):
            continue

        source_title = str(item.get("source_title", "")).strip()
        name = str(item.get("name", "")).strip()
        desc = str(item.get("description", "")).strip()

        try:
            min_sal = int(item.get("min_salary", 0))
            max_sal = int(item.get("max_salary", 0))
        except Exception:
            min_sal, max_sal = 0, 0

        positions = item.get("positions") or []
        if not isinstance(positions, list):
            positions = []
        positions = [str(x).strip() for x in positions if str(x).strip()][:7]

        if not source_title:
            continue
        if not name:
            name = source_title

        if min_sal and max_sal and min_sal > max_sal:
            min_sal, max_sal = max_sal, min_sal

        out.append(
            (
                source_title,
                JobRecord(
                    name=name,
                    slug=slugify(name),
                    description=desc,
                    min_salary=min_sal,
                    max_salary=max_sal,
                    positions=positions,
                ),
            )
        )

    return out
