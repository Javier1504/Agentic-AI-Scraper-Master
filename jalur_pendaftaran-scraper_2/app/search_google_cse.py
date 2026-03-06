import os
import time
import requests
from .utils import load_json, save_json, sha1

CSE_NORMAL = "https://www.googleapis.com/customsearch/v1"
CSE_SITE = "https://www.googleapis.com/customsearch/v1/siterestrict"

def _call(url, params, headers, timeout):
    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    return r

def google_cse_links(query: str, api_key: str, cx: str,
                     num: int, max_results: int,
                     state_dir: str, timeout: int, user_agent: str):
    os.makedirs(state_dir, exist_ok=True)
    cache_path = os.path.join(state_dir, "serp_cache.json")
    cache = load_json(cache_path, default={})

    key = sha1(f"googlecse|{query}|{num}|{max_results}|{cx}")
    if key in cache and cache[key].get("links"):
        return cache[key]["links"]

    headers = {"User-Agent": user_agent}
    links = []
    start = 1
    step = min(max(num, 1), 10)

    while start <= max_results:
        params = {"key": api_key, "cx": cx, "q": query, "num": step, "start": start}

        r = _call(CSE_NORMAL, params, headers, timeout)
        if r.status_code == 403:
            # fallback to site-restricted endpoint
            r = _call(CSE_SITE, params, headers, timeout)

        r.raise_for_status()
        data = r.json()

        items = data.get("items") or []
        if not items:
            break

        for it in items:
            u = it.get("link")
            if u:
                links.append(u)

        start += step
        time.sleep(0.2)

    # dedup preserve order
    seen = set()
    out = []
    for u in links:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)

    cache[key] = {"links": out}
    save_json(cache_path, cache)
    return out