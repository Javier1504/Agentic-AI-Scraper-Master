from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup


@dataclass
class ProspekExtractResult:
    prospek: List[str]
    confidence: float
    method: str
    raw_text: str


_SPLIT_RE = re.compile(r"\s*,\s*|\s*;\s*|\s*\n+\s*")


def _clean_item(x: str) -> str:
    x = re.sub(r"\s+", " ", x).strip(" \t\r\n•-–—")
    x = re.sub(r"\(\s*\)", "", x).strip()
    return x


def extract_prospek_heuristic(html: str) -> ProspekExtractResult:
    """
    Di contoh halaman, prospek muncul setelah ikon 'astro-prospek-kerja'
    dan berupa list dipisahkan koma. :contentReference[oaicite:10]{index=10}
    """
    soup = BeautifulSoup(html, "lxml")

    # 1) cari img yang src/alt mengandung 'astro-prospek-kerja'
    img = soup.find("img", attrs={"src": re.compile(r"astro-prospek-kerja", re.I)})
    if img is None:
        img = soup.find("img", attrs={"alt": re.compile(r"prospek", re.I)})

    candidate_text = ""
    if img:
        # Ambil teks terdekat setelah img
        # coba: parent -> get_text, tapi kadang kebawa section lain
        parent = img.parent
        if parent:
            # cari p terdekat
            p = parent.find_next("p")
            if p and p.get_text(strip=True):
                candidate_text = p.get_text(" ", strip=True)
            else:
                # fallback: next text in document flow
                nxt = img.find_next(string=True)
                if nxt:
                    candidate_text = str(nxt).strip()

        # Alternatif: langsung ambil text node setelah img
        if not candidate_text:
            sib_txt = img.next_sibling
            if isinstance(sib_txt, str):
                candidate_text = sib_txt.strip()

    # 2) fallback: cari blok yang ada kata "Prospek Kerja" lalu ambil p/ul setelahnya
    if not candidate_text:
        header = soup.find(string=re.compile(r"prospek\s*kerja", re.I))
        if header:
            # Ambil 1-3 elemen berikutnya yang berisi teks
            texts = []
            node = header
            for _ in range(6):
                node = getattr(node, "next_element", None)
                if node is None:
                    break
                if hasattr(node, "get_text"):
                    t = node.get_text(" ", strip=True)
                    if t and len(t) > 10:
                        texts.append(t)
                if len(texts) >= 2:
                    break
            candidate_text = " ".join(texts).strip()

    candidate_text = re.sub(r"\s+", " ", candidate_text).strip()

    if not candidate_text or len(candidate_text) < 10:
        return ProspekExtractResult([], 0.0, "heuristic_none", candidate_text)

    items = [_clean_item(x) for x in _SPLIT_RE.split(candidate_text) if _clean_item(x)]
    # Buang item yang terlalu panjang (biasanya paragraf, bukan list)
    items = [x for x in items if len(x) <= 80]

    # confidence sederhana
    conf = 0.4
    if img:
        conf += 0.3
    if len(items) >= 3:
        conf += 0.2
    if len(items) >= 6:
        conf += 0.1
    conf = min(conf, 1.0)

    return ProspekExtractResult(items, conf, "heuristic", candidate_text)


def extract_snippet_around_prospek(html: str, window: int = 2500) -> str:
    """
    Ambil snippet HTML sekitar 'astro-prospek-kerja' supaya prompt Gemini kecil & fokus.
    """
    key = "astro-prospek-kerja"
    idx = html.lower().find(key)
    if idx == -1:
        # fallback: cari 'prospek kerja'
        idx = html.lower().find("prospek kerja")
    if idx == -1:
        # terakhir: potong awal saja
        return html[: min(len(html), window * 2)]

    start = max(0, idx - window)
    end = min(len(html), idx + window)
    return html[start:end]
