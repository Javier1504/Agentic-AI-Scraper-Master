from __future__ import annotations
from typing import List, Dict, Any
import pandas as pd

EXCEL_CELL_LIMIT = 32767

JURUSAN_COLUMNS = [
    "id",
    "category_id",
    "name",
    "slug",
    "description",
    "is_active",
    "created_at",
    "updated_at",
    "deleted_at",
    "created_by",
    "updated_by",
    "deleted_by",
    "skills",
    "reasons",
    "jobable",
]

def _truncate_cell(x, limit: int = EXCEL_CELL_LIMIT) -> Any:
    if x is None:
        return None
    s = str(x)
    if len(s) <= limit:
        return x
    # simpan aman
    return s[: limit - 20] + " ...[TRUNCATED]"

def load_job_options(path: str) -> List[Dict[str, Any]]:
    xls = pd.ExcelFile(path)

    # cari sheet yang mengandung "job"
    sheet_name = None
    for s in xls.sheet_names:
        if "job" in s.lower():
            sheet_name = s
            break

    if not sheet_name:
        raise ValueError(f"Sheet job tidak ditemukan. Available: {xls.sheet_names}")

    df = pd.read_excel(path, sheet_name=sheet_name)

    # Rename kolom supaya sesuai sistem
    df = df.rename(columns={
        "Key": "id",
        "Value": "name"
    })

    df = df.dropna(subset=["id", "name"])

    return df[["id", "name"]].to_dict("records")

def load_jurusan_template(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    if "id" in df.columns:
        df2 = df[df["id"].apply(lambda x: str(x).strip().isdigit() if pd.notna(x) else False)].copy()
    else:
        df2 = df.iloc[0:0].copy()

    for c in JURUSAN_COLUMNS:
        if c not in df2.columns:
            df2[c] = None
    return df2[JURUSAN_COLUMNS].iloc[0:0].copy()

def build_jurusan_frame(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in JURUSAN_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[JURUSAN_COLUMNS]
    # truncate string columns
    for c in ["description", "name", "slug", "skills", "reasons"]:
        if c in df.columns:
            df[c] = df[c].apply(_truncate_cell)
    return df

def save_jurusan_outputs(df: pd.DataFrame, out_xlsx: str, out_csv: str) -> None:
    df.to_excel(out_xlsx, index=False)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
