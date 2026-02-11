from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, date
import json
import os
import re
from io import BytesIO
from typing import Dict, Any, List

import pandas as pd
from bs4 import BeautifulSoup
from pypdf import PdfReader
from dotenv import load_dotenv

from logger import setup, info, warn, error
from fetcher import RequestsFetcher, PlaywrightFetcher
from crawler import crawl_site
from gemini_client import GeminiClient
from validator import validate_text_with_gemini, validate_bytes_with_gemini, to_validated
from extractor import extract_jalur_items_from_text, extract_jalur_items_from_bytes
from checkpoint import Checkpoint


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Input xlsx (kolom: kampus_name, official_website)")
    ap.add_argument("--sheet", default=None, help="Sheet name (optional)")
    ap.add_argument("--template", required=True, help="Template xlsx Import biaya (kolom DB)")
    ap.add_argument("--outdir", default="out", help="Folder output")
    ap.add_argument("--max-pages", type=int, default=80)
    ap.add_argument("--min-score", type=float, default=2.0)
    ap.add_argument("--timeout-ms", type=int, default=25000)
    ap.add_argument("--wait-after-ms", type=int, default=500)
    ap.add_argument("--no-playwright", action="store_true", help="Disable Playwright (requests only)")
    ap.add_argument("--validate-only", action="store_true", help="Hanya validasi link, tanpa ekstraksi biaya")
    ap.add_argument("--concurrency", type=int, default=2, help="Parallel kampus (hati-hati rate limit)")
    ap.add_argument("--log-level", default=None, help="DEBUG/INFO/WARN/ERROR")
    return ap.parse_args()

def ensure_outdir(path: str):
    os.makedirs(path, exist_ok=True)

def read_pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(data))
        parts = []
        for p in reader.pages[:15]:
            t = p.extract_text() or ""
            if t.strip():
                parts.append(t.strip())
        return "\n".join(parts)[:20000]
    except Exception:
        return ""
    
def is_valid_jalur_object(obj: dict) -> bool:
    return (
        obj.get("name")
        and (
            obj.get("registration_start")
            or obj.get("registration_end")
            or obj.get("selection_method")
            or obj.get("description")
        )
    )

def html_to_text(html_bytes: bytes) -> str:
    try:
        soup = BeautifulSoup(html_bytes.decode("utf-8", errors="ignore"), "lxml")
        return soup.get_text(" ", strip=True)
    except Exception:
        return html_bytes.decode("utf-8", errors="ignore")
    
MONTHS = {
    # English
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,

    # Indonesian
    "januari": 1, "februari": 2, "maret": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "agustus": 8,
    "september": 9, "oktober": 10, "november": 11, "desember": 12,
}

    
def is_expired(registration_end: str | None) -> bool:
    if not registration_end:
        return False

    text = registration_end.strip().lower()

    # 1️⃣ ISO: YYYY-MM-DD
    try:
        d = datetime.fromisoformat(text).date()
        return d < date.today()
    except Exception:
        pass

    # 2️⃣ Format: 12 March 2025 / 12 Maret 2025
    m = re.search(r"(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})", text)
    if m:
        day = int(m.group(1))
        month_name = m.group(2)
        year = int(m.group(3))

        month = MONTHS.get(month_name)
        if month:
            try:
                d = date(year, month, day)
                return d < date.today()
            except Exception:
                pass

    # 3️⃣ Fallback: tahun saja
    years = re.findall(r"(20\d{2})", text)
    if years:
        last_year = max(int(y) for y in years)
        return last_year < date.today().year

    # 4️⃣ Ambigu → jangan buang
    return False

def dedupe_jalur_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Menghapus duplikasi jadwal berdasarkan MAKNA,
    bukan URL.
    """
    seen = set()
    result = []

    for it in items:
        key = (
            (it.get("_campus_name") or "").lower(),
            (it.get("name") or "").lower(),
            it.get("registration_start"),
            it.get("registration_end"),
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(it)

    return result

def normalize_jalur_item(it: dict) -> dict:
    it["name"] = (it.get("name") or "").strip()
    it["slug"] = (it.get("slug") or "").strip().lower()
    it["description"] = (it.get("description") or "").strip()

    # kosong → None
    for k in ["registration_start", "registration_end"]:
        if not it.get(k):
            it[k] = None

    return it

def is_too_old(it: dict) -> bool:
    name = (it.get("name") or "").lower()
    years = re.findall(r"(20\d{2})", name)
    if years:
        latest = max(int(y) for y in years)
        return latest < date.today().year
    return False



async def main():
    args = parse_args()
    ensure_outdir(args.outdir)

    setup(log_file_path=os.path.join(args.outdir, "run.log"), level=args.log_level)
    
    checkpoint = Checkpoint(
    os.path.join(args.outdir, "checkpoint.json")
    )

    info("start | initializing")
    info(f"config | outdir={args.outdir} max_pages={args.max_pages} concurrency={args.concurrency} no_playwright={args.no_playwright}")

    df = pd.read_excel(args.input, sheet_name=args.sheet) if args.sheet else pd.read_excel(args.input)
    required = {"kampus_name", "official_website", "rank_rank_id"}
    if not required.issubset(set(df.columns)):
        raise RuntimeError(f"Kolom input wajib: {required}. Kolom kamu: {list(df.columns)}")

    gemini = GeminiClient()  # model ambil dari .env GEMINI_MODEL kalau ada
    req = RequestsFetcher(timeout_s=max(10, args.timeout_ms // 1000))

    all_candidates: List[Dict[str, Any]] = []
    all_validated: List[Dict[str, Any]] = []
    all_jalur_items: List[Dict[str, Any]] = []

    sem = asyncio.Semaphore(max(1, args.concurrency))

    async with (PlaywrightFetcher(timeout_ms=args.timeout_ms, headless=True) if not args.no_playwright else _DummyAsyncContext()) as pw:

        async def fetch_html_async(url: str):
            if args.no_playwright:
                fr = req.fetch(url)
                # kalau content-type kosong tapi status ok, anggap html
                if fr.ok and not fr.content_type:
                    fr.content_type = "text/html"
                return fr
            return await pw.fetch_html(url, wait_after_ms=args.wait_after_ms)

        async def process_one(idx: int, total: int, row) -> None:
            campus = str(row["kampus_name"]).strip()
            base = str(row["official_website"]).strip()
            university_id = int(row["rank_rank_id"])

            async with sem:
                info(f"[{idx}/{total}] START univ='{campus}' base={base}")

                candidates = await crawl_site(
                    campus_name=campus,
                    official_website=base,
                    fetcher=pw if not args.no_playwright else req,
                    max_pages=args.max_pages,
                )


                info(f"[{idx}/{total}] CRAWL_DONE univ='{campus}' candidates={len(candidates)}")
                
                # ===== CHECKPOINT: CRAWL DONE =====
                state = checkpoint.uni(campus)
                state["crawl_done"] = True
                state["candidates"] = [c.__dict__ for c in candidates]
                checkpoint.save()
                
                # save candidates
                for c in candidates:
                    all_candidates.append({
                        "campus_name": c.campus_name,
                        "official_website": c.official_website,
                        "url": c.url,
                        "kind": c.kind,
                        "source_page": c.source_page,
                        "context_hint": c.context_hint,
                        "score": c.score,
                    })

                # validate + extract
                for j, c in enumerate(candidates, start=1):
                    info(f"validate | univ='{campus}' {j}/{len(candidates)} kind={c.kind} url={c.url}")

                    try:
                        if c.kind == "html":
                            fr = await fetch_html_async(c.url)
                            if not fr.ok or not fr.content:
                                continue

                            full_text = html_to_text(fr.content)

                            verdict, reason, snippet = validate_text_with_gemini(gemini, full_text, c.url, c.official_website)
                            v = to_validated(c, verdict, reason, snippet)
                            all_validated.append(v.__dict__)
                            info(f"validate_result | univ='{campus}' verdict={verdict} reason='{reason[:80]}'")
                            
                            # ===== CHECKPOINT: VALIDATED URL =====
                            checkpoint.uni(campus)["validated_urls"].append(c.url)
                            checkpoint.save()

                            if verdict != "valid" or args.validate_only:
                                continue

                            info(f"extract | univ='{campus}' kind=html url={c.url}")
                            items = extract_jalur_items_from_text(gemini, full_text)
                            info(f"extract_done | univ='{campus}' items={len(items)} url={c.url}")

                            for it in items:
                                it["_campus_name"] = campus
                                it["_official_website"] = base
                                it["_source_url"] = c.url
                                it["_source_page"] = c.source_page
                                it["_university_id"] = university_id
                                if is_valid_jalur_object(it):
                                    all_jalur_items.append(it)
                                    
                            # ===== CHECKPOINT: EXTRACTED URL =====
                            checkpoint.uni(campus)["extracted_urls"].append(c.url)
                            checkpoint.save()


                        elif c.kind == "pdf":
                            fr = await fetch_html_async(c.url)
                            if not fr.ok or not fr.content:
                                v = to_validated(c, "invalid", f"fetch failed status={fr.status}", "")
                                all_validated.append(v.__dict__)
                                continue

                            pdf_text = read_pdf_text(fr.content)

                            if pdf_text:
                                verdict, reason, snippet = validate_text_with_gemini(gemini, pdf_text)
                            else:
                                verdict, reason, snippet = validate_bytes_with_gemini(gemini, "application/pdf", fr.content)

                            v = to_validated(c, verdict, reason, snippet)
                            all_validated.append(v.__dict__)
                            info(f"validate_result | univ='{campus}' verdict={verdict} reason='{reason[:80]}'")
                            
                            # ===== CHECKPOINT: VALIDATED URL =====
                            checkpoint.uni(campus)["validated_urls"].append(c.url)
                            checkpoint.save()

                            if verdict != "valid" or args.validate_only:
                                continue

                            info(f"extract | univ='{campus}' kind=pdf url={c.url}")
                            items = extract_jalur_items_from_text(gemini, pdf_text) if pdf_text else extract_jalur_items_from_bytes(gemini, "application/pdf", fr.content)
                            info(f"extract_done | univ='{campus}' items={len(items)} url={c.url}")

                            for it in items:
                                it["_campus_name"] = campus
                                it["_official_website"] = base
                                it["_source_url"] = c.url
                                it["_source_page"] = c.source_page
                                it["_university_id"] = university_id    
                                if is_valid_jalur_object(it):
                                    all_jalur_items.append(it)
                                    
                            # ===== CHECKPOINT: EXTRACTED URL =====
                            checkpoint.uni(campus)["extracted_urls"].append(c.url)
                            checkpoint.save()


                        elif c.kind == "image":
                            fr = await fetch_html_async(c.url)
                            if not fr.ok or not fr.content:
                                v = to_validated(c, "invalid", f"fetch failed status={fr.status}", "")
                                all_validated.append(v.__dict__)
                                continue

                            mime = fr.content_type or "image/jpeg"
                            verdict, reason, snippet = validate_bytes_with_gemini(gemini, mime, fr.content)

                            v = to_validated(c, verdict, reason, snippet)
                            all_validated.append(v.__dict__)
                            info(f"validate_result | univ='{campus}' verdict={verdict} reason='{reason[:80]}'")
                            
                            # ===== CHECKPOINT: VALIDATED URL =====
                            checkpoint.uni(campus)["validated_urls"].append(c.url)
                            checkpoint.save()

                            if verdict != "valid" or args.validate_only:
                                continue

                            info(f"extract | univ='{campus}' kind=image url={c.url}")
                            items = extract_jalur_items_from_bytes(gemini, mime, fr.content)
                            info(f"extract_done | univ='{campus}' items={len(items)} url={c.url}")

                            for it in items:
                                it["_campus_name"] = campus
                                it["_official_website"] = base
                                it["_source_url"] = c.url
                                it["_source_page"] = c.source_page
                                it["_university_id"] = university_id    
                                if is_valid_jalur_object(it):
                                    all_jalur_items.append(it)
                                    
                            # ===== CHECKPOINT: EXTRACTED URL =====
                            checkpoint.uni(campus)["extracted_urls"].append(c.url)
                            checkpoint.save()

                    except Exception as e:
                        warn(f"validate/extract exception | univ='{campus}' kind={c.kind} url={c.url} err={type(e).__name__}:{e}")
                        v = to_validated(c, "uncertain", f"exception: {type(e).__name__}: {e}", "")
                        all_validated.append(v.__dict__)
                        
                        # ===== CHECKPOINT: VALIDATED URL (EXCEPTION) =====
                        checkpoint.uni(campus)["validated_urls"].append(c.url)
                        checkpoint.save()
                
                checkpoint.mark_done(campus)
                info(f"[{idx}/{total}] DONE univ='{campus}'")


        total = len(df)
        for idx, (_, row) in enumerate(df.iterrows(), start=1):
            await process_one(idx, total, row)


    # SAVE JSON outputs
    cand_path = os.path.join(args.outdir, "candidates_all.json")
    val_path = os.path.join(args.outdir, "validated_links.json")
    valid_only_path = os.path.join(args.outdir, "valid_links_only.json")

    with open(cand_path, "w", encoding="utf-8") as f:
        json.dump(all_candidates, f, ensure_ascii=False, indent=2)
    with open(val_path, "w", encoding="utf-8") as f:
        json.dump(all_validated, f, ensure_ascii=False, indent=2)

    valid_only = [x for x in all_validated if x.get("verdict") == "valid"]
    with open(valid_only_path, "w", encoding="utf-8") as f:
        json.dump(valid_only, f, ensure_ascii=False, indent=2)

    info(f"save | candidates={cand_path}")
    info(f"save | validated={val_path}")
    info(f"save | valid_only={valid_only_path}")

    if args.validate_only:
        info("DONE | validate-only mode")
        return

    # ===================== DEDUPE JALUR ITEMS =====================
    info(f"dedupe | before={len(all_jalur_items)}")
    all_jalur_items = dedupe_jalur_items(all_jalur_items)
    info(f"dedupe | after={len(all_jalur_items)}")

    jalur_json = os.path.join(args.outdir, "jalur_items_extracted.json")
    with open(jalur_json, "w", encoding="utf-8") as f:
        json.dump(all_jalur_items, f, ensure_ascii=False, indent=2)
    info(f"save | jalur_items={jalur_json}")


    # Build output xlsx based on template columns
    # ===================== BUILD OUTPUT XLSX =====================
    tpl = pd.read_excel(args.template)
    tpl_cols = list(tpl.columns)

    rows_out = []
    skipped_expired = 0
    auto_id = 1
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for it in all_jalur_items:
        
        it = normalize_jalur_item(it)
        # FILTER EXPIRED DI SINI SAJA
        if is_expired(it.get("registration_end")) or is_too_old(it):
            skipped_expired += 1
            continue

        row = {c: None for c in tpl_cols}

        # ====== MAPPING FINAL SESUAI DB ======
        row["id"] = auto_id
        auto_id += 1
        row["university_id"] = it.get("_university_id")
        row["name"] = it.get("name")
        row["slug"] = it.get("slug")
        row["description"] = it.get("description")

        row["start_date"] = it.get("registration_start")
        row["end_date"] = it.get("registration_end")

        row["url"] = it.get("_source_url")
        row["is_active"] = True

        row["created_at"] = now_ts
        row["updated_at"] = now_ts

        # kolom opsional biarkan NULL
        # deleted_at, created_by, updated_by, deleted_by

        rows_out.append(row)

    info(f"excel_filter | skipped_expired={skipped_expired}")

    out_df = pd.DataFrame(rows_out, columns=tpl_cols)
    out_xlsx = os.path.join(args.outdir, "import_jalur_filled.xlsx")
    out_df.to_excel(out_xlsx, index=False)

    info(f"save | import_xlsx={out_xlsx}")
    info("DONE | all finished")


class _DummyAsyncContext:
    async def __aenter__(self): return None
    async def __aexit__(self, exc_type, exc, tb): return False

if __name__ == "__main__":
    asyncio.run(main())
