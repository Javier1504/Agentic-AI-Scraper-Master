from __future__ import annotations
from typing import Any, Dict, Tuple, Optional
import json
import random
import time
from google import genai
from google.genai import errors as genai_errors
from .config import GEMINI_API_KEY, GEMINI_MODEL

def _usage_from_resp(resp) -> Dict[str, int]:
    usage = {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0}
    try:
        um = getattr(resp, "usage_metadata", None)
        if um:
            usage["prompt_tokens"] = int(getattr(um, "prompt_token_count", 0) or 0)
            usage["candidates_tokens"] = int(getattr(um, "candidates_token_count", 0) or 0)
            usage["total_tokens"] = int(getattr(um, "total_token_count", 0) or 0)
    except Exception:
        pass
    return usage

def _safe_json_loads(s: str) -> Dict[str, Any]:
    if not s:
        return {}
    s = s.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s.replace("json", "", 1).strip()
    try:
        return json.loads(s)
    except Exception:
        return {}

class GeminiJSON:
    """
    Robust JSON extractor:
    - Retry 503/429 with exponential backoff + jitter
    - Fallback model chain
    - Never crash pipeline: return {} on total failure
    """

    def __init__(self, model: Optional[str] = None):
        assert GEMINI_API_KEY, "GEMINI_API_KEY kosong. Pastikan ada di .env"
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        primary = (model or GEMINI_MODEL).strip()
        #kalo gemini 2.5 flash gagal, fallback ke model lain dulu
        self.models = [
            primary,
            "gemini-2.0-flash-lite",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ]

    def _call(
        self,
        model_name: str,
        payload: str,
        schema: Dict[str, Any],
    ):
        return self.client.models.generate_content(
            model=model_name,
            contents=[{"role": "user", "parts": [{"text": payload}]}],
            config={
                "temperature": 0.0,
                "response_mime_type": "application/json",
                "response_schema": schema,
            },
        )

    def extract_json(
        self,
        text: str,
        schema: Dict[str, Any],
        system_rules: str,
        max_retries: int = 7,
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        payload = system_rules + "\n\n=== BUKTI TEKS (hasil crawl) ===\n" + (text or "")
        return self._extract(payload, schema=schema, max_retries=max_retries)

    def extract_json_browse(
        self,
        url: str,
        campus_name: str,
        schema: Dict[str, Any],
        system_rules: str,
        max_retries: int = 7,
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        # NOTE: ini bukan browser sungguhan, tapi URL-guided extraction.
        payload = (
            system_rules
            + "\n\n=== KONTEKS ===\n"
            + f"Nama kampus: {campus_name}\n"
            + f"Website (gunakan sebagai rujukan utama): {url}\n"
            + "\n"
            + "Instruksi tambahan:\n"
            + "- Jika butuh bukti, cari di halaman yang biasanya berisi daftar prodi/fakultas.\n"
            + "- Jangan mengarang. Jika tidak ada, isi '-' / [] sesuai schema.\n"
        )
        return self._extract(payload, schema=schema, max_retries=max_retries)

    def _extract(
        self,
        payload: str,
        schema: Dict[str, Any],
        max_retries: int,
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        total_usage = {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0}
        last_err: Optional[Exception] = None

        for model_name in self.models:
            for attempt in range(1, max_retries + 1):
                try:
                    resp = self._call(model_name, payload, schema)
                    usage = _usage_from_resp(resp)
                    for k in total_usage:
                        total_usage[k] += int(usage.get(k, 0) or 0)

                    data = _safe_json_loads(getattr(resp, "text", "") or "")
                    if isinstance(data, dict) and data:
                        return data, total_usage
                    # kalau kosong, coba retry ringan dengan delay 0.8 detik
                    time.sleep(0.8)

                except (genai_errors.ServerError, genai_errors.RateLimitError) as e:
                    last_err = e
                    msg = str(e).lower()
                    # 503 overloaded unavailable, 429 rate limit
                    if ("503" in msg) or ("unavailable" in msg) or ("overloaded" in msg) or ("429" in msg) or ("rate" in msg):
                        sleep_s = min(60.0, (2 ** (attempt - 1))) + random.uniform(0.0, 1.2)
                        time.sleep(sleep_s)
                        continue
                    break
                except Exception as e:
                    last_err = e
                    break

        # total fail
        return {}, total_usage
