from __future__ import annotations

import os
import time
import random
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup


@dataclass
class CrawlerConfig:
    base_url: str
    listing_path: str = "/jurusan"
    page_size: int = 75
    max_pages: int = 0  # 0 = auto
    timeout_s: int = 25
    retry: int = 3

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )


def _session(cfg: CrawlerConfig) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": cfg.user_agent})
    return s


def _listing_url(cfg: CrawlerConfig, page: int) -> str:
    """
    Dari hasil inspeksi listing, paginasi memakai parameter:
    _cari_jurusan_v3_CariJurusanV3Portlet_cur, _delta, dll. :contentReference[oaicite:8]{index=8}
    """
    params = {
        "_cari_jurusan_v3_CariJurusanV3Portlet_cur": str(page),
        "_cari_jurusan_v3_CariJurusanV3Portlet_delta": str(cfg.page_size),
        "_cari_jurusan_v3_CariJurusanV3Portlet_departmentname": "",
        "_cari_jurusan_v3_CariJurusanV3Portlet_resetCur": "false",
        "p_p_id": "cari_jurusan_v3_CariJurusanV3Portlet",
        "p_p_lifecycle": "0",
        "p_p_mode": "view",
        "p_p_state": "normal",
    }
    return urljoin(cfg.base_url, cfg.listing_path) + "?" + urlencode(params)


def fetch_html(s: requests.Session, url: str, timeout_s: int, retry: int) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(1, retry + 1):
        try:
            r = s.get(url, timeout=timeout_s)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            time.sleep(min(2.0 * attempt, 6.0) + random.random())
    raise RuntimeError(f"Failed fetch after {retry} retries: {url}") from last_err


def parse_major_cards(html: str, base_url: str) -> List[Dict[str, str]]:
    """
    Ambil link 'Selengkapnya' yang mengarah ke /detail-jurusan/<id>/<slug>
    """
    soup = BeautifulSoup(html, "lxml")
    majors: List[Dict[str, str]] = []

    # Pattern: anchor text "Selengkapnya" (di listing). :contentReference[oaicite:9]{index=9}
    for a in soup.find_all("a"):
        txt = (a.get_text(" ", strip=True) or "").lower()
        href = a.get("href") or ""
        if "selengkapnya" in txt and "detail-jurusan" in href:
            url = urljoin(base_url, href)
            # Nama jurusan biasanya ada di card dekat link; ambil heuristik:
            # cari parent dan ambil teks baris sebelumnya
            parent = a.parent
            name = ""
            if parent:
                block_text = parent.get_text("\n", strip=True)
                # block_text biasanya: "Nama Jurusan\nRumpun\nSelengkapnya"
                lines = [x.strip() for x in block_text.split("\n") if x.strip()]
                if lines:
                    # ambil item pertama yang bukan "Selengkapnya"
                    for line in lines:
                        if line.lower() != "selengkapnya":
                            name = line
                            break
            majors.append({"name": name or "", "url": url})

    # Dedup by url
    uniq: Dict[str, Dict[str, str]] = {}
    for m in majors:
        uniq[m["url"]] = m
    return list(uniq.values())


def iter_all_majors(cfg: CrawlerConfig) -> Iterator[Dict[str, str]]:
    s = _session(cfg)

    page = 1
    seen_urls = set()

    while True:
        if cfg.max_pages and page > cfg.max_pages:
            break

        url = _listing_url(cfg, page)
        html = fetch_html(s, url, cfg.timeout_s, cfg.retry)
        majors = parse_major_cards(html, cfg.base_url)

        if not majors:
            # kalau kosong, stop
            break

        new_count = 0
        for m in majors:
            if m["url"] in seen_urls:
                continue
            seen_urls.add(m["url"])
            new_count += 1
            yield m

        # kalau di page ini tidak ada item baru, stop (safety)
        if new_count == 0:
            break

        page += 1
