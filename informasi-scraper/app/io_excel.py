from __future__ import annotations
from typing import Dict, Any, List, Optional
import os
import re
import pandas as pd

IMPORT_COLUMNS = [
    "id","university_code","name","slug","short_name","description","logo",
    "type","status","accreditation",
    "website","email","phone","whatsapp",
    "facebook","instagram","twitter","youtube",
    "address","province_id","city_id","postal_code","cover"
]

def load_seed_xlsx(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)
    return df

def build_import_frame(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in IMPORT_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[IMPORT_COLUMNS]
    return df

def _is_empty(v) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except Exception:
        pass
    s = str(v).strip()
    return s == "" or s == "-" or s.lower() in {"n/a","na","none","null","unknown"}

def _valid_postal(v) -> bool:
    if _is_empty(v):
        return False
    s = str(v).strip()
    return bool(re.fullmatch(r"\d{5}", s))

def _merge_existing(existing: pd.DataFrame, incoming: pd.DataFrame, key: str = "id") -> pd.DataFrame:
    """Upsert merge yang aman saat resume.

    Prinsip:
    - Jangan hilangkan baris lama.
    - Untuk key yang sama: hanya overwrite kolom tertentu jika nilai incoming *lebih baik*.
      * Incoming yang kosong / '-' tidak boleh menimpa nilai existing yang sudah terisi.
      * postal_code: hanya menimpa jika incoming valid 5 digit.
    """
    if existing is None or existing.empty:
        return incoming
    if incoming is None or incoming.empty:
        return existing
    if key not in existing.columns or key not in incoming.columns:
        out = pd.concat([existing, incoming], ignore_index=True)
        return out

    # normalize schema
    all_cols = list(dict.fromkeys(list(existing.columns) + list(incoming.columns)))
    for c in all_cols:
        if c not in existing.columns:
            existing[c] = None
        if c not in incoming.columns:
            incoming[c] = None
    existing = existing[all_cols]
    incoming = incoming[all_cols]

    ex = existing.set_index(key)
    inc = incoming.set_index(key)

    # start from existing, then selectively update from incoming
    out = ex.copy()
    for idx, row in inc.iterrows():
        if idx not in out.index:
            out.loc[idx] = row
            continue
        for c in all_cols:
            if c == key:
                continue
            newv = row[c]
            oldv = out.at[idx, c]
            if c == "postal_code":
                # overwrite only if incoming postal valid; else keep old
                if _valid_postal(newv):
                    out.at[idx, c] = str(newv).strip()
                else:
                    # keep old (even if old invalid; we don't worsen)
                    pass
            else:
                # general rule: overwrite only if incoming not empty
                if not _is_empty(newv):
                    out.at[idx, c] = newv
                else:
                    # keep old
                    pass

    out = out.reset_index()
    return out
    # Ensure same columns superset
    for c in incoming.columns:
        if c not in existing.columns:
            existing[c] = None
    for c in existing.columns:
        if c not in incoming.columns:
            incoming[c] = None
    incoming = incoming[existing.columns]
    out = pd.concat([existing, incoming], ignore_index=True)
    out = out.drop_duplicates(subset=[key], keep="last")
    return out

def save_outputs(df: pd.DataFrame, out_xlsx: str, out_csv: str, key: str = "id"):
    """Save outputs but preserve previous scraping results on resume.

    If out_xlsx/out_csv already exist, we MERGE (upsert) by `key`:
    - rows in df overwrite existing rows with same key
    - rows not present in df are kept from existing file
    """
    os.makedirs(os.path.dirname(out_xlsx) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)

    df_out = df.copy()

    # Normalize columns order
    for c in IMPORT_COLUMNS:
        if c not in df_out.columns:
            df_out[c] = None
    df_out = df_out[IMPORT_COLUMNS]

    # Merge with existing XLSX (preferred source of truth)
    if os.path.exists(out_xlsx):
        try:
            old = pd.read_excel(out_xlsx)
            # normalize old columns to schema
            for c in IMPORT_COLUMNS:
                if c not in old.columns:
                    old[c] = None
            old = old[IMPORT_COLUMNS]
            df_out = _merge_existing(old, df_out, key=key)
            df_out = df_out[IMPORT_COLUMNS]
        except Exception:
            # If existing file is unreadable, overwrite with df_out
            pass

    df_out.to_excel(out_xlsx, index=False)
    df_out.to_csv(out_csv, index=False, encoding="utf-8-sig")
