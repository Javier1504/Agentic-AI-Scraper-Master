from __future__ import annotations
from typing import List, Dict, Tuple, Union
import re
from .utils import same_site

KW_PRODI = [
    "program studi", "program-studi", "program_studi", "programstudi",
    "prodi", "jurusan", "departemen", "department",
    "fakultas", "faculty",
    "akademik", "academic",
    "study program", "undergraduate", "graduate", "postgraduate", "pascasarjana",
    "sarjana", "magister", "doktor", "diploma",
]

BAD_HINT = [
    "login", "auth", "sso", "logout", "wp-admin",
    "cart", "checkout",
    "news", "berita", "artikel", "press-release",
    "agenda", "event", "kegiatan",
    "pengumuman", "announcement",
    "penerimaan", "admission", "pmb",  # bukan prodi list
]

PATH_BOOST_RE = re.compile(
    r"(program[-_]?studi|prodi|jurusan|departemen|department|faculty|fakultas|academic|akademik)",
    re.I,
)

def _score(href: str, text: str) -> float:
    u = (href or "").lower()
    t = (text or "").lower()
    blob = f"{u} {t}"

    if any(b in blob for b in BAD_HINT):
        return -10.0

    s = 0.0
    for k in KW_PRODI:
        if k in blob:
            s += 2.0

    # boost halaman listing prodi / fakultas
    if PATH_BOOST_RE.search(blob):
        s += 10.0

    if u.endswith(".pdf"):
        s += 1.5

    # penalti halaman sangat umum
    if u.rstrip("/").endswith(("/id", "/en", "/home", "/beranda")):
        s -= 1.0

    return s

def pick_candidates_prodi(seed_url: str, links: Union[List[str], List[Dict[str, str]]], limit: int) -> List[str]:
    items: List[Dict[str, str]] = []
    if links and isinstance(links[0], dict):  # type: ignore[index]
        for it in links:  # type: ignore[assignment]
            href = (it.get("href") or "").strip()
            if href:
                items.append({"href": href, "text": (it.get("text") or "").strip()})
    else:
        for u in (links or []):  # type: ignore[union-attr]
            u = str(u).strip()
            if u:
                items.append({"href": u, "text": ""})

    scored: List[Tuple[float, str]] = []
    for it in items:
        href = (it.get("href") or "").strip()
        text = (it.get("text") or "").strip()
        if not href.startswith("http"):
            continue
        if not same_site(seed_url, href):
            continue
        sc = _score(href, text)
        scored.append((sc, href))

    scored.sort(key=lambda x: x[0], reverse=True)

    picked: List[str] = []
    for sc, href in scored:
        if sc <= 0:
            continue
        if href not in picked:
            picked.append(href)
        if len(picked) >= limit:
            break
    return picked
