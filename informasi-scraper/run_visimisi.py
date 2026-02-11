from __future__ import annotations
import os, json
from typing import Dict, Any

from app.config import DEFAULT_INPUT_XLSX, OUT_DIR, STATE_DIR, MAX_PAGES_VISIT, MAX_INTERNAL_CANDIDATES, MAX_COMBINED_TEXT
from app.fetcher import PlaywrightFetcher
from app.selector import pick_candidates
from app.gemini_client import GeminiJSON
from app.extractors import SCHEMA_VISI, RULES_VISI, normalize_visi
from app.utils import slugify, acronym, compact_text
from app.io_excel import load_seed_xlsx, build_import_frame, save_outputs

def bundle_text(fetcher: PlaywrightFetcher, seed_url: str, mode: str) -> str:
    visited = set()
    to_visit = [seed_url]
    all_links = []
    combined = ""

    while to_visit and len(visited) < MAX_PAGES_VISIT and len(combined) < MAX_COMBINED_TEXT:
        u = to_visit.pop(0)
        if u in visited:
            continue
        visited.add(u)

        r = fetcher.fetch(u)
        if r.text:
            combined += "\n\n" + r.text

        all_links.extend(r.links)

        if len(visited) == 1:
            cands = pick_candidates(r.final_url or seed_url, all_links, mode=mode, limit=MAX_INTERNAL_CANDIDATES)
            to_visit.extend([x for x in cands if x not in visited])

    return compact_text(combined, MAX_COMBINED_TEXT)

def main():
    inp = load_seed_xlsx(DEFAULT_INPUT_XLSX)

    state_path = os.path.join(STATE_DIR, "state_visimisi.json")
    state = {"done": {}} if not os.path.exists(state_path) else json.load(open(state_path, "r", encoding="utf-8"))

    gem = GeminiJSON()
    rows = []
    total_usage = {"prompt_tokens":0,"candidates_tokens":0,"total_tokens":0}

    with PlaywrightFetcher() as fetcher:
        for i, row in inp.iterrows():
            name = str(row.get("kampus_name", "")).strip()
            website = str(row.get("official_website", "")).strip()
            key = f"{i}:{website}"

            if state["done"].get(key) == "ok":
                print(f"[SKIP] {name}")
                continue

            print(f"[VISI] start {i+1}/{len(inp)} | {name} | {website}")

            text = bundle_text(fetcher, website, mode="visi")
            data, usage = gem.extract_json(text=text, schema=SCHEMA_VISI, system_rules=RULES_VISI)
            vv = normalize_visi(data)

            # Map ke description (skema import hanya punya description)
            desc_parts = []
            if vv["sejarah_deskripsi"] != "-":
                desc_parts.append(vv["sejarah_deskripsi"])
            if vv["visi"] != "-":
                desc_parts.append(f"Visi: {vv['visi']}")
            if vv["misi"] != "-":
                desc_parts.append(f"Misi: {vv['misi']}")
            description = "\n\n".join(desc_parts).strip() if desc_parts else None

            out: Dict[str, Any] = {
                "id": i+1,
                "university_code": None,
                "name": name,
                "slug": slugify(name),
                "short_name": acronym(name),
                "description": description,
                "logo": None,
                "type": None,
                "status": None,
                "accreditation": None,
                "website": website,
                "email": None,
                "phone": None,
                "whatsapp": None,
                "facebook": None,
                "instagram": None,
                "twitter": None,
                "youtube": None,
                "address": None,
                "province_id": None,
                "city_id": None,
                "postal_code": None,
                "cover": None,
            }
            rows.append(out)

            for k in total_usage:
                total_usage[k] += int(usage.get(k,0) or 0)

            state["done"][key] = "ok"
            json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

            print(f"[VISI] done | usage={usage} | total={total_usage}")

    df_out = build_import_frame(rows)
    out_xlsx = os.path.join(OUT_DIR, "import_visimisi.xlsx")
    out_csv  = os.path.join(OUT_DIR, "import_visimisi.csv")
    save_outputs(df_out, out_xlsx, out_csv)
    print(f"[DONE] saved: {out_xlsx} + {out_csv}")
    print(f"[TOKENS] total: {total_usage}")

if __name__ == "__main__":
    main()
