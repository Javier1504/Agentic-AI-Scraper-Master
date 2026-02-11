# checkpoint.py
from __future__ import annotations
import json
import os
from typing import Dict, Any
from logger import info

class Checkpoint:
    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, Any] = {"universities": {}}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                pass

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def uni(self, campus: str) -> Dict[str, Any]:
        return self.data["universities"].setdefault(campus, {
            "crawl_done": False,
            "candidates": [],
            "validated_urls": [],
            "extracted_urls": [],
            "done": False
        })

    def mark_done(self, campus: str):
        self.uni(campus)["done"] = True
        self.save()
        info(f"checkpoint | DONE campus='{campus}'")
