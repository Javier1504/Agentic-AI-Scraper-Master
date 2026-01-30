from __future__ import annotations
from typing import List, Dict, Any
import pandas as pd

EXCEL_CELL_LIMIT = 32767

PRODI_COLUMNS = [
    "id", "major_id", "university_id", "name", "slug", "description",
    "level", "accreditation", "type",
    "created_at", "updated_at", "deleted_at",
    "created_by", "updated_by", "deleted_by", "id_siakad",
]

def _truncate_cell(x, limit: int = EXCEL_CELL_LIMIT) -> Any:
    if x is None:
        return None
    s = str(x)
    if len(s) <= limit:
        return x
    # simpan aman
    return s[: limit - 20] + " ...[TRUNCATED]"

def load_prodi_template(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    if "id" in df.columns:
        df2 = df[df["id"].apply(lambda x: str(x).strip().isdigit() if pd.notna(x) else False)].copy()
    else:
        df2 = df.iloc[0:0].copy()

    for c in PRODI_COLUMNS:
        if c not in df2.columns:
            df2[c] = None
    return df2[PRODI_COLUMNS].iloc[0:0].copy()

def build_prodi_frame(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in PRODI_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[PRODI_COLUMNS]
    # truncate string columns
    for c in ["description", "name", "slug", "type", "accreditation", "level"]:
        if c in df.columns:
            df[c] = df[c].apply(_truncate_cell)
    return df

def save_prodi_outputs(df: pd.DataFrame, out_xlsx: str, out_csv: str) -> None:
    df.to_excel(out_xlsx, index=False)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
