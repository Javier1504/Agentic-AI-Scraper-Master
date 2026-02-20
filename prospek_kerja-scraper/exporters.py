from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List

from openpyxl import Workbook


MASTER_COLUMNS = ["name", "slug", "description", "min_salary", "max_salary", "positions"]


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def write_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_master_xlsx(path: str, rows: List[Dict[str, Any]]) -> None:
    """
    Sheet output = format import (tanpa kolom id), sesuai 'Format Excel' master Anda.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Format Excel"

    ws.append(MASTER_COLUMNS)
    for r in rows:
        positions_json = json.dumps(r["positions"], ensure_ascii=False)
        ws.append([
            r["name"],
            r["slug"],
            r["description"],
            int(r["min_salary"]),
            int(r["max_salary"]),
            positions_json,
        ])

    wb.save(path)
