from __future__ import annotations
import re
from urllib.parse import urlparse

def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    s = re.sub(r"\s+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s or "item"

def _registrable_domain(host: str) -> str:
    """
    Heuristik ringan pengganti tldextract:
    - dukung domain Indonesia umum: *.ac.id, *.sch.id, *.go.id, *.or.id, *.co.id, *.web.id, *.my.id
    - kalau tidak cocok, pakai 2 label terakhir.
    """
    host = (host or "").lower().strip().replace("www.", "")
    host = host.split(":")[0]
    if not host:
        return ""

    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host

    # suffix 2-level ID
    suffix2 = {"ac.id", "sch.id", "go.id", "or.id", "co.id", "web.id", "my.id"}
    last2 = ".".join(parts[-2:])
    last3 = ".".join(parts[-3:])

    if last2 in suffix2 and len(parts) >= 3:
        return last3  # contoh: ui.ac.id, ub.ac.id
    return ".".join(parts[-2:])  # contoh: itb.ac.id (tetap ok), undip.ac.id, etc.

def same_site(a: str, b: str) -> bool:
    """
    Lebih longgar dari sebelumnya:
    - dianggap satu situs jika registrable domain sama (mendukung subdomain)
    """
    try:
        pa = urlparse(a)
        pb = urlparse(b)
        ha = _registrable_domain(pa.netloc)
        hb = _registrable_domain(pb.netloc)
        return ha != "" and ha == hb
    except Exception:
        return False
