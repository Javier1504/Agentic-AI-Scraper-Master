from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union, List

from google import genai  # pip install google-genai


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_LEADING_LABEL_RE = re.compile(r"^\s*(json|JSON)\s*[\r\n]+", re.IGNORECASE)


def _clean_model_text(text: Any) -> str:
    if text is None:
        return ""
    t = str(text).strip()

    # buang triple backticks wrapper
    t = _JSON_FENCE_RE.sub("", t).strip()

    # buang label "json" di awal
    t = _LEADING_LABEL_RE.sub("", t).strip()

    # kadang ada "Output:" atau semacamnya
    t = re.sub(r"^\s*(output|result|hasil)\s*:\s*", "", t, flags=re.IGNORECASE).strip()
    return t


def _extract_first_json_value(text: str) -> str:
    """
    Ambil JSON value pertama (object ATAU array) dari output model.
    Menangani:
    - JSON object: {...}
    - JSON array : [...]
    """
    if not text:
        return text

    s = text.strip()
    # cari start token pertama '{' atau '['
    i_obj = s.find("{")
    i_arr = s.find("[")
    if i_obj == -1 and i_arr == -1:
        return s

    if i_obj == -1:
        start = i_arr
        open_ch, close_ch = "[", "]"
    elif i_arr == -1:
        start = i_obj
        open_ch, close_ch = "{", "}"
    else:
        # ambil yang muncul lebih dulu
        if i_arr < i_obj:
            start = i_arr
            open_ch, close_ch = "[", "]"
        else:
            start = i_obj
            open_ch, close_ch = "{", "}"

    # scan dengan stack sederhana (hormati string quotes)
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(s)):
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue

        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return s[start:j + 1]

    # kalau tidak ketemu penutup, fallback potong dari start
    return s[start:]


def _validate_schema(data: Any, schema_hint: Optional[Dict[str, Any]]) -> None:
    if not schema_hint:
        return

    required = schema_hint.get("required", [])
    if not required:
        return

    # dict: cek keys
    if isinstance(data, dict):
        for k in required:
            if k not in data:
                raise ValueError(f"Gemini JSON missing required key: {k}")
        return

    # list: cek tiap item dict punya keys
    if isinstance(data, list):
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"Gemini JSON list item {idx} is not an object")
            for k in required:
                if k not in item:
                    raise ValueError(f"Gemini JSON list item {idx} missing key: {k}")
        return

    raise ValueError("Gemini JSON is neither object nor list")


@dataclass
class GeminiClient:
    api_key: str
    model: str = "gemini-2.5-flash"

    def __post_init__(self) -> None:
        self._client = genai.Client(api_key=self.api_key)

    def generate_json(
        self,
        system: str,
        user: str,
        schema_hint: Optional[Dict[str, Any]] = None,
        retries: int = 2,
        sleep_s: float = 1.0,
    ) -> Any:
        """
        Minta output JSON. Parser dibuat robust:
        - strip label/fence
        - extract first JSON object/array
        - retry jika gagal parse / schema
        """
        prompt = f"""SYSTEM:
{system}

USER:
{user}

OUTPUT RULES:
- Output MUST be valid JSON only (no markdown, no backticks).
- Do NOT add any leading label like "json".
"""

        last_err: Optional[Exception] = None
        last_text: str = ""

        for attempt in range(1, retries + 2):  # total tries = retries+1
            resp = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
            )

            raw = getattr(resp, "text", None)
            if raw is None:
                raw = str(resp)

            last_text = str(raw)
            cleaned = _clean_model_text(last_text)

            candidate = cleaned.strip()
            # kalau tidak dimulai { atau [, ambil json value pertama
            if not (candidate.startswith("{") or candidate.startswith("[")):
                candidate = _extract_first_json_value(candidate).strip()

            try:
                data = json.loads(candidate)
                _validate_schema(data, schema_hint)
                return data
            except Exception as e:
                last_err = e
                if attempt <= retries + 1:
                    time.sleep(sleep_s * attempt)

        preview = _clean_model_text(last_text)[:600]
        raise ValueError(
            "Gemini returned non-JSON or unparseable output.\n"
            f"Preview (cleaned) first 600 chars:\n{preview}"
        ) from last_err


def build_gemini_from_env() -> Optional[GeminiClient]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    return GeminiClient(api_key=api_key, model=model)
