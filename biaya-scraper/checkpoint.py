from __future__ import annotations

import json
import os
import time
import hashlib
from typing import Any, Dict, Optional

from utils import slugify


def make_campus_id(campus_name: str, official_website: str) -> str:
    """Stable ID per campus based on name + website."""
    name_part = slugify(campus_name)[:40]
    h = hashlib.sha1((official_website or "").strip().encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{name_part}_{h}" if name_part else f"campus_{h}"


def now_iso() -> str:
    # Simple ISO-ish, no tz. Good enough for logs.
    return time.strftime("%Y-%m-%d %H:%M:%S")


def atomic_write_json(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def checkpoint_path(checkpoint_dir: str, campus_id: str) -> str:
    return os.path.join(checkpoint_dir, f"{campus_id}.json")


def init_checkpoint(campus_id: str, campus_name: str, official_website: str) -> Dict[str, Any]:
    return {
        "campus_id": campus_id,
        "campus_name": campus_name,
        "official_website": official_website,
        "status": "started",  # started | crawled | done
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "candidates": [],
        "validated": [],
        "fee_items": [],
        "errors": [],
        "stats": {"candidates": 0, "validated": 0, "fee_items": 0},
    }


def touch_stats(state: Dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    state["stats"] = {
        "candidates": len(state.get("candidates", []) or []),
        "validated": len(state.get("validated", []) or []),
        "fee_items": len(state.get("fee_items", []) or []),
    }
