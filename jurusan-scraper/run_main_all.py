from __future__ import annotations
from datetime import datetime
import os
import json
import time
from typing import Dict, Any, List, Tuple
from urllib.parse import urldefrag
import pandas as pd
from app.fetcher import PlaywrightFetcher
from app.gemini_client import GeminiJSON
from app.utils import slugify
from app.config import (
    OUT_DIR, STATE_DIR,
    MAX_INTERNAL_CANDIDATES, MAX_PAGES_VISIT,
    MIN_TEXT_TO_EXTRACT, SAVE_EVERY_UNIV
)
from app.selector_jurusan import pick_candidates_jurusan
from app.extractors_jurusan import SCHEMA_JURUSAN, RULES_JURUSAN, normalize_jurusan_item
from app.io_jurusan_excel import (
    build_jurusan_frame,
    save_jurusan_outputs,
    load_jurusan_template,
    load_job_options,
    load_category_options,
)
DEFAULT_UNIV_XLSX = os.path.join(os.path.dirname(__file__), "input.xlsx")
JURUSAN_TEMPLATE_XLSX = os.path.join(os.path.dirname(__file__), "(2) master - Import Jurusan Umum.xlsx")
OUT_XLSX = os.path.join(OUT_DIR, "IMPORT_JURUSAN_FINAL.xlsx")
OUT_CSV  = os.path.join(OUT_DIR, "IMPORT_JURUSAN_FINAL.csv")
OUT_XLSX_PART = os.path.join(OUT_DIR, "IMPORT_JURUSAN_FINAL_partial.xlsx")
OUT_CSV_PART  = os.path.join(OUT_DIR, "IMPORT_JURUSAN_FINAL_partial.csv")
STATE_PATH = os.path.join(STATE_DIR, "state_run_jurusan.json")

def norm_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    u, _ = urldefrag(u)
    return u.rstrip("/")


def looks_blocked(fetch_res) -> bool:
    err = (getattr(fetch_res, "error", "") or "").lower()
    html = (getattr(fetch_res, "html", "") or "").lower()
    ok = bool(getattr(fetch_res, "ok", False))
    text = (getattr(fetch_res, "text", "") or "").strip()
    
    if "cloudflare" in html and ("just a moment" in html or "attention required" in html):
        return True
    if "challenge-platform" in html or "cf-chl" in html:
        return True
    if "blocked" in err or "cloudflare" in err:
        return True
    if (not ok) and len(text) < 80:
        return True
    return False

def detect_univ_columns(df: pd.DataFrame) -> Tuple[str, str, str]:
    id_col = None
    for c in ["id", "university_id", "rank_rank_id", "rank_id", "key"]:
        if c in df.columns:
            id_col = c
            break

    name_col = None
    for c in ["name", "kampus_name", "university_name", "input_name"]:
        if c in df.columns:
            name_col = c
            break

    web_col = None
    for c in ["website", "official_website", "official_website_url", "official_url", "url"]:
        if c in df.columns:
            web_col = c
            break

    assert id_col and name_col and web_col, (
        "Kolom kampus kurang. butuh id/name/website.\n"
        f"columns={list(df.columns)}\n"
        "Kolom yang didukung:\n"
        "- id: id/university_id/rank_rank_id/rank_id/key\n"
        "- name: name/kampus_name/university_name/input_name\n"
        "- website: website/official_website/official_website_url/official_url/url\n"
    )
    return id_col, name_col, web_col


def _safe_int_or_raw(v):
    try:
        if v is None:
            return None
        if pd.isna(v):
            return None
        return int(v)
    except Exception:
        return str(v).strip() if v is not None else None


def _dedup_jurusan(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for p in items:
        name = (p.get("name") or "").strip().lower()
        if not name or name == "-":
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(p)
    return out


def extract_multi_page(
    fetcher: PlaywrightFetcher,
    gem: GeminiJSON,
    seed_url: str,
    campus_name: str,
    job_list_text: str,
    limit_pages: int = MAX_INTERNAL_CANDIDATES,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], bool]:
    """
    STRATEGI UTAMA (agar tidak "1 doang"):
    - ambil seed page
    - pilih kandidat halaman prodi/fakultas (limit_pages)
    - panggil Gemini PER HALAMAN (bukan bundle)
    - gabungkan + dedup
    Return: (programs, usage, blocked_flag)
    """
    usage_total = {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0}
    programs_all: List[Dict[str, Any]] = []

    r0 = fetcher.fetch(seed_url)
    blocked = looks_blocked(r0)

    base_url = r0.final_url or seed_url
    candidates = pick_candidates_jurusan(base_url, r0.links or [], limit=limit_pages)

    pages = [seed_url] + [u for u in candidates if u != seed_url]
    pages = pages[:limit_pages]

    for idx, url in enumerate(pages, start=1):
        r = fetcher.fetch(url)
        if looks_blocked(r):
            blocked = True

        txt = (r.text or "").strip()
        if len(txt) < MIN_TEXT_TO_EXTRACT:
            continue

        # log
        print(f"  [PAGE] {idx}/{len(pages)} extract via gemini | {url}", flush=True)
        
        rules_with_jobs = RULES_JURUSAN + f"""

        TUGAS TAMBAHAN:
            - Tentukan job_ids yang PALING SESUAI dari daftar berikut.
            - HANYA boleh memilih dari daftar ini.
            - Boleh lebih dari satu.
            - Jika tidak relevan, isi [].

        DAFTAR JOB:
        {job_list_text}
        """

        data, usage = gem.extract_json(text=txt, schema=SCHEMA_JURUSAN, system_rules=rules_with_jobs)
        for k in usage_total:
            usage_total[k] += int((usage or {}).get(k, 0) or 0)

        arr = (data or {}).get("programs", []) if isinstance(data, dict) else []
        for it in arr:
            if not isinstance(it, dict):
                continue
            x = normalize_jurusan_item(it)
            if x:
                # kalau url kosong, isi url sumber halaman
                if (x.get("url") in [None, "", "-"]) and url:
                    x["url"] = url
                programs_all.append(x)

        time.sleep(0.2)

    programs_all = _dedup_jurusan(programs_all)
    return programs_all, usage_total, blocked

def detect_category_id(jurusan_slug: str, category_slug_map: Dict[str, Any]):
    jurusan_slug = jurusan_slug.lower()

    for cat_slug, cat_id in category_slug_map.items():
        if cat_slug in jurusan_slug:
            return cat_id

    return None

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)

    assert os.path.exists(JURUSAN_TEMPLATE_XLSX), f"Template jurusan tidak ada: {JURUSAN_TEMPLATE_XLSX}"
    _ = load_jurusan_template(JURUSAN_TEMPLATE_XLSX)
    
    # LOAD JOB OPTIONS
    job_options = load_job_options(JURUSAN_TEMPLATE_XLSX)

    job_list_text = "\n".join(
        [f"{j['id']} = {j['name']}" for j in job_options]
    ) or "Tidak ada job yang tersedia."
    
    # LOAD CATEGORY OPTIONS
    category_options = load_category_options(JURUSAN_TEMPLATE_XLSX)

    # buat mapping slug → id
    category_slug_map = {
        c["slug"].strip().lower(): c["id"]
        for c in category_options
    }
    assert os.path.exists(DEFAULT_UNIV_XLSX), (
        f"Input kampus tidak ada: {DEFAULT_UNIV_XLSX}\n"
        f"Taruh input.xlsx di folder proyek (sejajar run_main_all.py)."
    )

    univ = pd.read_excel(DEFAULT_UNIV_XLSX)
    id_col, name_col, web_col = detect_univ_columns(univ)

    state = {"done": {}, "meta": {"saved_at": None}} if not os.path.exists(STATE_PATH) else json.load(
        open(STATE_PATH, "r", encoding="utf-8")
    )

    gem = GeminiJSON()
    rows: List[Dict[str, Any]] = []
    total_usage = {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0}
    next_id = 1

    with PlaywrightFetcher() as fetcher:
        for i, r in univ.iterrows():
            univ_id = r.get(id_col)
            name = str(r.get(name_col, "")).strip()
            website = norm_url(str(r.get(web_col, "")).strip())
            key = f"{univ_id}:{website}"

            # SKIP hanya jika benar-benar 'ok'
            if state["done"].get(key) == "ok":
                print(f"[SKIP] {i+1}/{len(univ)} {name}")
                continue

            print(f"[START] {i+1}/{len(univ)} | {name} | {website}")

            try:
                # multi-page extraction
                programs, usage1, blocked = extract_multi_page(fetcher, gem, website, name, job_list_text,)

                for k in total_usage:
                    total_usage[k] += int((usage1 or {}).get(k, 0) or 0)

                #fallback jika hasil masih kosong atau blocked berat
                if not programs:
                    print("[JURUSAN] hasil kosong jadi harus fallback gemini (URL-guided)", flush=True)
                    data2, usage2 = gem.extract_json_browse(
                        url=website, campus_name=name, schema=SCHEMA_JURUSAN, system_rules=RULES_JURUSAN
                    )
                    for k in total_usage:
                        total_usage[k] += int((usage2 or {}).get(k, 0) or 0)

                    arr2 = (data2 or {}).get("programs", []) if isinstance(data2, dict) else []
                    tmp = []
                    for it in arr2:
                        if not isinstance(it, dict):
                            continue
                        x = normalize_jurusan_item(it)
                        if x:
                            tmp.append(x)
                    programs = _dedup_jurusan(tmp)

                # kalo kosong jangan di 'ok'
                if not programs:
                    state["done"][key] = "empty"
                    state["meta"]["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    json.dump(state, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                    print(f"[EMPTY] {name} | tidak menemukan jurusan", flush=True)
                    continue

                out_rows = []
                for p in programs:
                    jurusan_name = p["name"]
                    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    cat_id = detect_category_id(slugify(jurusan_name), category_slug_map)

                    out_rows.append({
                        "id": next_id,
                        "category_id": cat_id,
                        "name": jurusan_name,
                        "slug": slugify(jurusan_name),
                        "description": p.get("description", "-"),
                        "is_active": True,
                        "created_at": now_ts,
                        "updated_at": now_ts,
                        "deleted_at": None,
                        "created_by": None,
                        "updated_by": None,
                        "deleted_by": None,
                        "skills": p.get("skills", "-"),
                        "reasons": p.get("reasons", "-"),
                        "jobable": ",".join(map(str, p.get("jobable", []))),
                    })
                    next_id += 1


                rows.extend(out_rows)

                # sukses beneran baru ok
                state["done"][key] = "ok"
                state["meta"]["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                json.dump(state, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

                # autosave partial
                if SAVE_EVERY_UNIV and ((i + 1) % SAVE_EVERY_UNIV == 0):
                    df_tmp = build_jurusan_frame(rows)
                    save_jurusan_outputs(df_tmp, OUT_XLSX_PART, OUT_CSV_PART)

                print(f"[DONE] {name} | jurusan={len(out_rows)} | total_tokens={total_usage['total_tokens']}")

            except Exception as e:
                print(f"[ERROR] {name} | {website} | err={e}")
                state["done"][key] = f"error:{type(e).__name__}"
                state["meta"]["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                json.dump(state, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

                if rows:
                    df_tmp = build_jurusan_frame(rows)
                    save_jurusan_outputs(df_tmp, OUT_XLSX_PART, OUT_CSV_PART)
                continue

    df_out = build_jurusan_frame(rows)
    save_jurusan_outputs(df_out, OUT_XLSX, OUT_CSV)

    print(f"[FINAL] saved: {OUT_XLSX} + {OUT_CSV}")
    print(f"[TOKENS] total: {total_usage}")


if __name__ == "__main__":
    main()
