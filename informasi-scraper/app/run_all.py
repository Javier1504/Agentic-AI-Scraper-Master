from __future__ import annotations
import os, json, re, time
from typing import Dict, Any, Tuple
from urllib.parse import urldefrag

import pandas as pd

from app.config import (
    DEFAULT_INPUT_XLSX, OUT_DIR, STATE_DIR,
    MAX_PAGES_VISIT, MAX_INTERNAL_CANDIDATES, MAX_COMBINED_TEXT
)
from app.fetcher import PlaywrightFetcher
from app.selector import pick_candidates
from app.gemini_client import GeminiJSON
from app.extractors import (
    SCHEMA_IMPORT, RULES_INFO, normalize_info_keys,
    postprocess_info,
    SCHEMA_VISI, RULES_VISI, normalize_visi,
)
from app.mapper_region import load_region_table, match_region
from app.utils import slugify, best_short_name
from app.io_excel import build_import_frame, save_outputs, IMPORT_COLUMNS

IMPORT_SCHEMA_XLSX = os.path.join(os.path.dirname(__file__), "(3) Import Informasi Kampus.xlsx")


def norm_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    u, _ = urldefrag(u)
    return u.rstrip("/")


def _looks_blocked(fetch_res) -> bool:
    err = (getattr(fetch_res, "error", "") or "").lower()
    if "blocked_cloudflare_like" in err or "cloudflare" in err or "just a moment" in err:
        return True
    # text kosong + ok False => suspicious
    ok = bool(getattr(fetch_res, "ok", False))
    text = (getattr(fetch_res, "text", "") or "").strip()
    if (not ok) and len(text) < 80:
        return True
    return False


def _fetch_with_retry(fetcher: PlaywrightFetcher, url: str, tries: int = 2, base_sleep: float = 3.0):
    last = None
    for t in range(tries):
        r = fetcher.fetch(url)
        last = r
        if getattr(r, "ok", False) and (getattr(r, "text", "") or "").strip():
            return r
        if _looks_blocked(r):
            sleep_s = base_sleep * (t + 1)
            print(f"[FETCH] blocked/suspect -> retry {t+1}/{tries} sleep={sleep_s:.1f}s url={url}")
            time.sleep(sleep_s)
            continue
        return r
    return last


def bundle_text(fetcher: PlaywrightFetcher, seed_url: str, mode: str) -> Tuple[str, str, bool]:
    """
    Multi-hop bundling:
    - Every visited page can contribute new candidate links (like your visimisi.py success pattern)
    - Return: (seed_text, combined_text, blocked_flag)
    """
    visited: set[str] = set()
    queue: list[str] = [seed_url]
    combined = ""
    seed_text = ""
    blocked_flag = False
    discovered: list[str] = []

    while queue and len(visited) < MAX_PAGES_VISIT and len(combined) < MAX_COMBINED_TEXT:
        u = queue.pop(0)
        if u in visited:
            continue
        visited.add(u)

        r = _fetch_with_retry(fetcher, u, tries=2)
        if not r:
            continue

        if _looks_blocked(r):
            blocked_flag = True

        if len(visited) == 1:
            seed_text = (r.text or "")

        if r.text:
            combined += "\n\n" + r.text

        base_url = (r.final_url or u or seed_url)
        cands = pick_candidates(base_url, r.links or [], mode=mode, limit=MAX_INTERNAL_CANDIDATES)

        for x in cands:
            if x not in discovered:
                discovered.append(x)

        for x in cands:
            if x not in visited and x not in queue:
                queue.append(x)

        # pacing kecil
        time.sleep(0.35)

        # early stop
        if mode == "visi":
            if len(combined) >= 1800:
                low = combined.lower()
                if any(k in low for k in ["visi", "misi", "vision", "mission", "sejarah", "tentang", "profil", "about"]):
                    break
        else:
            if len(combined) >= 1200:
                break

    combined = combined[:MAX_COMBINED_TEXT]

    # fallback local: try a few top discovered if VISI still thin
    if mode == "visi" and len(combined.strip()) < 1200 and discovered:
        add = 0
        for u in discovered:
            if u in visited:
                continue
            r = _fetch_with_retry(fetcher, u, tries=2, base_sleep=4.0)
            if r and r.text:
                combined += "\n\n" + r.text
            add += 1
            if add >= 5 or len(combined) >= 1800:
                break
        combined = combined[:MAX_COMBINED_TEXT]

    return seed_text, combined, blocked_flag


def main():
    assert os.path.exists(IMPORT_SCHEMA_XLSX), (
        f"File skema tidak ditemukan: {IMPORT_SCHEMA_XLSX}\n"
        "Taruh (3) Import Informasi Kampus.xlsx di folder proyek (sejajar run_all.py)."
    )

    inp = pd.read_excel(DEFAULT_INPUT_XLSX)

    # normalize input columns
    if "kampus_name" not in inp.columns:
        for c in ["name", "campus_name", "university", "Nama Kampus"]:
            if c in inp.columns:
                inp = inp.rename(columns={c: "kampus_name"})
                break
    if "official_website" not in inp.columns:
        for c in ["official_website_url", "official_url", "website", "official_site"]:
            if c in inp.columns:
                inp = inp.rename(columns={c: "official_website"})
                break

    assert "kampus_name" in inp.columns and "official_website" in inp.columns, \
        "Kolom input wajib: kampus_name dan official_website"

    region_df = load_region_table(IMPORT_SCHEMA_XLSX)

    # checkpoint
    state_path = os.path.join(STATE_DIR, "state_run_all.json")
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    state = {"done": {}} if not os.path.exists(state_path) else json.load(open(state_path, "r", encoding="utf-8"))

    gem = GeminiJSON()
    rows: list[Dict[str, Any]] = []
    total_usage = {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0}

    with PlaywrightFetcher() as fetcher:
        for i, r in inp.iterrows():
            name = str(r.get("kampus_name", "")).strip()
            website_raw = str(r.get("official_website", "")).strip()
            website = norm_url(website_raw)
            key = f"{i}:{website}"

            if state["done"].get(key) == "ok":
                print(f"[SKIP] {i+1}/{len(inp)} {name}")
                continue

            print(f"[START] {i+1}/{len(inp)} | {name} | {website}")

            try:
                # ========= INFO =========
                seed_info, text_info, blocked_info = bundle_text(fetcher, website, mode="info")

                use_browse_info = (len((text_info or "").strip()) < 900) or blocked_info
                if use_browse_info:
                    print("[INFO] bundle pendek/blocked -> gemini fallback browse")
                    data_info, usage_info = gem.extract_json_browse(
                        url=website, campus_name=name, schema=SCHEMA_IMPORT, system_rules=RULES_INFO
                    )
                else:
                    data_info, usage_info = gem.extract_json(
                        text=text_info, schema=SCHEMA_IMPORT, system_rules=RULES_INFO
                    )

                if not data_info:
                    print("[WARN] Gemini INFO gagal -> isi default '-'")
                    data_info = {}
                info = normalize_info_keys(data_info)
                # ✅ post-process: type/status Bahasa Indonesia + koreksi PTN/PTS
                info = postprocess_info(name=name, website=website_raw, info=info, text_blob=text_info)

                prov_id, city_id = match_region(region_df, info.get("province_name", "-"), info.get("city_name", "-"))

                # ========= VISI/MISI =========
                seed_visi, text_visi, blocked_visi = bundle_text(fetcher, website, mode="visi")

                use_browse_visi = (len((text_visi or "").strip()) < 1200) or blocked_visi
                if use_browse_visi:
                    print("[VISI] bundle pendek/blocked -> gemini fallback browse")
                    data_visi, usage_visi = gem.extract_json_browse(
                        url=website, campus_name=name, schema=SCHEMA_VISI, system_rules=RULES_VISI
                    )
                else:
                    data_visi, usage_visi = gem.extract_json(
                        text=text_visi, schema=SCHEMA_VISI, system_rules=RULES_VISI
                    )

                if not data_visi:
                    print("[WARN] Gemini VISI gagal -> description kosong")
                    data_visi = {}
                vv = normalize_visi(data_visi)

                desc_parts = []
                if vv.get("sejarah_deskripsi") not in (None, "", "-"):
                    desc_parts.append(vv["sejarah_deskripsi"])
                if vv.get("visi") not in (None, "", "-"):
                    desc_parts.append(f"Visi: {vv['visi']}")
                if vv.get("misi") not in (None, "", "-"):
                    desc_parts.append(f"Misi: {vv['misi']}")
                description = "\n\n".join(desc_parts).strip() if desc_parts else None

                short_name = best_short_name(name, website)  # Anda bilang sudah berhasil

                out: Dict[str, Any] = {
                    "id": i + 1,
                    "university_code": None,
                    "name": name,
                    "slug": slugify(name),
                    "short_name": short_name,
                    "description": description,
                    "logo": None,
                    "type": info.get("type", "-"),
                    "status": info.get("status", "-"),
                    "accreditation": info.get("accreditation", "-"),
                    "website": website_raw,
                    "email": info.get("email", "-"),
                    "phone": info.get("phone", "-"),
                    "whatsapp": info.get("whatsapp", "-"),
                    "facebook": info.get("facebook", "-"),
                    "instagram": info.get("instagram", "-"),
                    "twitter": info.get("twitter", "-"),
                    "youtube": info.get("youtube", "-"),
                    "address": info.get("address", "-"),
                    "province_id": prov_id,
                    "city_id": city_id,
                    "postal_code": info.get("postal_code", "-"),
                    "cover": None,
                }
                rows.append(out)

                for k in total_usage:
                    total_usage[k] += int((usage_info or {}).get(k, 0) or 0) + int((usage_visi or {}).get(k, 0) or 0)

                state["done"][key] = "ok"
                json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

                # autosave per kampus
                df_tmp = build_import_frame(rows)
                save_outputs(
                    df_tmp,
                    os.path.join(OUT_DIR, "IMPORT_FINAL_partial.xlsx"),
                    os.path.join(OUT_DIR, "IMPORT_FINAL_partial.csv"),
                )

                print(f"[DONE] {name} | short={short_name} | total_tokens={total_usage['total_tokens']}")

            except Exception as e:
                print(f"[ERROR] {name} | {website} | err={e}")
                state["done"][key] = f"error:{type(e).__name__}"
                json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

                if rows:
                    df_tmp = build_import_frame(rows)
                    save_outputs(
                        df_tmp,
                        os.path.join(OUT_DIR, "IMPORT_FINAL_partial.xlsx"),
                        os.path.join(OUT_DIR, "IMPORT_FINAL_partial.csv"),
                    )
                continue

    # FINAL save
    df_out = build_import_frame(rows)
    for c in IMPORT_COLUMNS:
        if c not in df_out.columns:
            df_out[c] = None
    df_out = df_out[IMPORT_COLUMNS]

    save_outputs(
        df_out,
        os.path.join(OUT_DIR, "IMPORT_FINAL.xlsx"),
        os.path.join(OUT_DIR, "IMPORT_FINAL.csv"),
    )

    print(f"[FINAL] saved: {os.path.join(OUT_DIR,'IMPORT_FINAL.xlsx')} + {os.path.join(OUT_DIR,'IMPORT_FINAL.csv')}")
    print(f"[TOKENS] total: {total_usage}")


if __name__ == "__main__":
    main()