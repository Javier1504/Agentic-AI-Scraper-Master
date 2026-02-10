from __future__ import annotations

import os
from tenacity import retry, stop_after_attempt, wait_exponential

# ✅ AUTO load .env dari folder project (aman walau run dari folder lain)
from dotenv import load_dotenv
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

class GeminiClient:
    def __init__(self, model: str | None = None):
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY belum diset. Pastikan file .env ada di folder project "
                "dan berisi GEMINI_API_KEY=..., atau set env var GEMINI_API_KEY."
            )

        self.model = model or os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()

        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key)

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=20))
    def generate_text(self, prompt: str, temperature: float = 0.2) -> str:
        resp = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=self._types.GenerateContentConfig(
                temperature=temperature,
                response_mime_type="text/plain",
            ),
        )
        return (resp.text or "").strip()

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=20))
    def generate_with_bytes(self, prompt: str, data: bytes, mime_type: str) -> str:
        part = self._types.Part.from_bytes(data=data, mime_type=mime_type)
        resp = self._client.models.generate_content(
            model=self.model,
            contents=[prompt, part],
            config=self._types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="text/plain",
            ),
        )
        return (resp.text or "").strip()
