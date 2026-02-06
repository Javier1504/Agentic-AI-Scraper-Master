from __future__ import annotations

import argparse
import asyncio
import json
import os
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
from extractor import extract_fee_items_from_text, extract_fee_items_from_bytes, narrow_fee_items
from extract_assets import extract_links_and_assets
from utils import is_allowed_asset_url, same_site


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

def _normalize_items(items):
    # Ensure list of dict to avoid TypeError on item assignment
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, dict):
            out.append(it)
        elif isinstance(it, str) and it.strip():
            out.append({"name": it.strip()})
    return out

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

def html_to_text(html_bytes: bytes) -> str:
    try:
        soup = BeautifulSoup(html_bytes.decode("utf-8", errors="ignore"), "lxml")
        return soup.get_text(" ", strip=True)
    except Exception:
        return html_bytes.decode("utf-8", errors="ignore")

async def main():
    args = parse_args()
    ensure_outdir(args.outdir)

    setup(log_file_path=os.path.join(args.outdir, "run.log"), level=args.log_level)

    info("start | initializing")
    info(f"config | outdir={args.outdir} max_pages={args.max_pages} concurrency={args.concurrency} no_playwright={args.no_playwright}")

    df = pd.read_excel(args.input, sheet_name=args.sheet) if args.sheet else pd.read_excel(args.input)
    required = {"kampus_name", "official_website"}
    if not required.issubset(set(df.columns)):
        raise RuntimeError(f"Kolom input wajib: {required}. Kolom kamu: {list(df.columns)}")

    gemini = GeminiClient()  # model ambil dari .env GEMINI_MODEL kalau ada
    req = RequestsFetcher(timeout_s=max(10, args.timeout_ms // 1000))

    all_candidates: List[Dict[str, Any]] = []
    all_validated: List[Dict[str, Any]] = []
    all_fee_items: List[Dict[str, Any]] = []

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
            campus_id = None
            for _col in ("id", "ID", "campus_id", "rank_id", "key"):
                if _col in row and str(row[_col]).strip() and str(row[_col]).lower() != "nan":
                    campus_id = str(row[_col]).strip()
                    break
            if not base:
                return

            async with sem:
                info(f"[{idx}/{total}] START univ='{campus}' base={base}")

                candidates = await crawl_site(
                    campus_name=campus,
                    official_website=base,
                    fetch_html_async=fetch_html_async,
                    max_pages=args.max_pages,
                    min_candidate_score=args.min_score,
                )

                info(f"[{idx}/{total}] CRAWL_DONE univ='{campus}' candidates={len(candidates)}")

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
                            # gunakan Playwright saat crawl agar konten JS/lazyload tidak hilang
                            fr = await fetch_html_async(c.url)
                            if (not fr.ok or not fr.content) and not args.no_playwright:
                                # fallback keras: coba requests
                                fr = req.fetch(c.url)
                            text = html_to_text(fr.content) if (fr.ok and fr.content) else ""

                            verdict, reason, snippet = validate_text_with_gemini(gemini, text)
                            v = to_validated(c, verdict, reason, snippet)
                            all_validated.append(v.__dict__)
                            info(f"validate_result | univ='{campus}' verdict={verdict} reason='{reason[:80]}'")

                            if verdict != "valid":
                                # Fallback: halaman HTML bisa hanya embed gambar/PDF tabel biaya.
                                # Ambil asset tertanam dan validasi asset tersebut (tetap dari official page, bukan nebak).
                                try:
                                    found2 = extract_links_and_assets(fr.final_url or c.url, (fr.content or b"").decode("utf-8", errors="ignore"))
                                    asset_links = [(u, kind, hint, sc) for (u, kind, hint, sc) in found2 if kind in ("pdf","image")]
                                    # filter asset: boleh external CDN/Drive, tapi harus allowlist atau same-site
                                    asset_links = [(u, kind, hint, sc) for (u, kind, hint, sc) in asset_links
                                                   if (same_site(u, c.official_website) or is_allowed_asset_url(u, c.official_website))]
                                    # limit agar tidak spam
                                    asset_links = sorted(asset_links, key=lambda x: x[3], reverse=True)[:6]
                                    for (au, akind, ahint, asc) in asset_links:
                                        info(f"validate_asset_fallback | univ='{campus}' kind={akind} url={au}")
                                        afr = req.fetch(au)
                                        if not afr.ok or not afr.content:
                                            continue
                                        amime = afr.content_type or ("application/pdf" if akind=="pdf" else "image/jpeg")
                                        averdict, areason, asnip = validate_bytes_with_gemini(gemini, amime, afr.content)
                                        info(f"validate_asset_result | univ='{campus}' verdict={averdict} reason='{areason[:80]}'")
                                        if averdict != "valid" or args.validate_only:
                                            continue

                                        # extract dari asset yang valid
                                        if akind == "pdf":
                                            apdf_text = read_pdf_text(afr.content)
                                            items2 = extract_fee_items_from_text(gemini, apdf_text) if apdf_text else extract_fee_items_from_bytes(gemini, "application/pdf", afr.content)
                                        else:
                                            items2 = extract_fee_items_from_bytes(gemini, amime, afr.content)

                                        items2 = _normalize_items(items2)
                                        info(f"extract_done | univ='{campus}' items={len(items2)} url={au}")

                                        for it in items2:
                                            it["_campus_name"] = campus
                                            it["_official_website"] = base
                                            it["_source_url"] = au
                                            it["_source_page"] = c.url
                                            if campus_id and not it.get("priceable_id"):
                                                it["priceable_type"] = it.get("priceable_type") or "Campus"
                                                it["priceable_id"] = campus_id
                                            all_fee_items.append(it)
                                except Exception as ee:
                                    warn(f"asset_fallback_error | univ='{campus}' err={type(ee).__name__}:{ee}")

                            if verdict != "valid" or args.validate_only:
                                continue

                            info(f"extract | univ='{campus}' kind=html url={c.url}")
                            items = extract_fee_items_from_text(gemini, text)
                            items = _normalize_items(items)
                            items = narrow_fee_items(items)
                            info(f"extract_done | univ='{campus}' items={len(items)} url={c.url}")

                            for it in items:
                                it["_campus_name"] = campus
                                it["_official_website"] = base
                                it["_source_url"] = c.url
                                it["_source_page"] = c.source_page
                                if campus_id and not it.get("priceable_id"):
                                    it["priceable_type"] = it.get("priceable_type") or "Campus"
                                    it["priceable_id"] = campus_id
                                all_fee_items.append(it)

                        elif c.kind == "pdf":
                            fr = req.fetch(c.url)
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

                            if verdict != "valid":
                                # Fallback: halaman HTML bisa hanya embed gambar/PDF tabel biaya.
                                # Ambil asset tertanam dan validasi asset tersebut (tetap dari official page, bukan nebak).
                                try:
                                    found2 = extract_links_and_assets(fr.final_url or c.url, (fr.content or b"").decode("utf-8", errors="ignore"))
                                    asset_links = [(u, kind, hint, sc) for (u, kind, hint, sc) in found2 if kind in ("pdf","image")]
                                    # filter asset: boleh external CDN/Drive, tapi harus allowlist atau same-site
                                    asset_links = [(u, kind, hint, sc) for (u, kind, hint, sc) in asset_links
                                                   if (same_site(u, c.official_website) or is_allowed_asset_url(u, c.official_website))]
                                    # limit agar tidak spam
                                    asset_links = sorted(asset_links, key=lambda x: x[3], reverse=True)[:6]
                                    for (au, akind, ahint, asc) in asset_links:
                                        info(f"validate_asset_fallback | univ='{campus}' kind={akind} url={au}")
                                        afr = req.fetch(au)
                                        if not afr.ok or not afr.content:
                                            continue
                                        amime = afr.content_type or ("application/pdf" if akind=="pdf" else "image/jpeg")
                                        averdict, areason, asnip = validate_bytes_with_gemini(gemini, amime, afr.content)
                                        info(f"validate_asset_result | univ='{campus}' verdict={averdict} reason='{areason[:80]}'")
                                        if averdict != "valid" or args.validate_only:
                                            continue

                                        # extract dari asset yang valid
                                        if akind == "pdf":
                                            apdf_text = read_pdf_text(afr.content)
                                            items2 = extract_fee_items_from_text(gemini, apdf_text) if apdf_text else extract_fee_items_from_bytes(gemini, "application/pdf", afr.content)
                                        else:
                                            items2 = extract_fee_items_from_bytes(gemini, amime, afr.content)

                                        items2 = _normalize_items(items2)
                                        info(f"extract_done | univ='{campus}' items={len(items2)} url={au}")

                                        for it in items2:
                                            it["_campus_name"] = campus
                                            it["_official_website"] = base
                                            it["_source_url"] = au
                                            it["_source_page"] = c.url
                                            if campus_id and not it.get("priceable_id"):
                                                it["priceable_type"] = it.get("priceable_type") or "Campus"
                                                it["priceable_id"] = campus_id
                                            all_fee_items.append(it)
                                except Exception as ee:
                                    warn(f"asset_fallback_error | univ='{campus}' err={type(ee).__name__}:{ee}")

                            if verdict != "valid" or args.validate_only:
                                continue

                            info(f"extract | univ='{campus}' kind=pdf url={c.url}")
                            items = extract_fee_items_from_text(gemini, pdf_text) if pdf_text else extract_fee_items_from_bytes(gemini, "application/pdf", fr.content)
                            items = _normalize_items(items)
                            items = narrow_fee_items(items)
                            info(f"extract_done | univ='{campus}' items={len(items)} url={c.url}")

                            for it in items:
                                it["_campus_name"] = campus
                                it["_official_website"] = base
                                it["_source_url"] = c.url
                                it["_source_page"] = c.source_page
                                if campus_id and not it.get("priceable_id"):
                                    it["priceable_type"] = it.get("priceable_type") or "Campus"
                                    it["priceable_id"] = campus_id
                                all_fee_items.append(it)

                        elif c.kind == "image":
                            fr = req.fetch(c.url)
                            if not fr.ok or not fr.content:
                                v = to_validated(c, "invalid", f"fetch failed status={fr.status}", "")
                                all_validated.append(v.__dict__)
                                continue

                            mime = fr.content_type or "image/jpeg"
                            verdict, reason, snippet = validate_bytes_with_gemini(gemini, mime, fr.content)

                            v = to_validated(c, verdict, reason, snippet)
                            all_validated.append(v.__dict__)
                            info(f"validate_result | univ='{campus}' verdict={verdict} reason='{reason[:80]}'")

                            if verdict != "valid":
                                # Fallback: halaman HTML bisa hanya embed gambar/PDF tabel biaya.
                                # Ambil asset tertanam dan validasi asset tersebut (tetap dari official page, bukan nebak).
                                try:
                                    found2 = extract_links_and_assets(fr.final_url or c.url, (fr.content or b"").decode("utf-8", errors="ignore"))
                                    asset_links = [(u, kind, hint, sc) for (u, kind, hint, sc) in found2 if kind in ("pdf","image")]
                                    # filter asset: boleh external CDN/Drive, tapi harus allowlist atau same-site
                                    asset_links = [(u, kind, hint, sc) for (u, kind, hint, sc) in asset_links
                                                   if (same_site(u, c.official_website) or is_allowed_asset_url(u, c.official_website))]
                                    # limit agar tidak spam
                                    asset_links = sorted(asset_links, key=lambda x: x[3], reverse=True)[:6]
                                    for (au, akind, ahint, asc) in asset_links:
                                        info(f"validate_asset_fallback | univ='{campus}' kind={akind} url={au}")
                                        afr = req.fetch(au)
                                        if not afr.ok or not afr.content:
                                            continue
                                        amime = afr.content_type or ("application/pdf" if akind=="pdf" else "image/jpeg")
                                        averdict, areason, asnip = validate_bytes_with_gemini(gemini, amime, afr.content)
                                        info(f"validate_asset_result | univ='{campus}' verdict={averdict} reason='{areason[:80]}'")
                                        if averdict != "valid" or args.validate_only:
                                            continue

                                        # extract dari asset yang valid
                                        if akind == "pdf":
                                            apdf_text = read_pdf_text(afr.content)
                                            items2 = extract_fee_items_from_text(gemini, apdf_text) if apdf_text else extract_fee_items_from_bytes(gemini, "application/pdf", afr.content)
                                        else:
                                            items2 = extract_fee_items_from_bytes(gemini, amime, afr.content)

                                        items2 = _normalize_items(items2)
                                        info(f"extract_done | univ='{campus}' items={len(items2)} url={au}")

                                        for it in items2:
                                            it["_campus_name"] = campus
                                            it["_official_website"] = base
                                            it["_source_url"] = au
                                            it["_source_page"] = c.url
                                            if campus_id and not it.get("priceable_id"):
                                                it["priceable_type"] = it.get("priceable_type") or "Campus"
                                                it["priceable_id"] = campus_id
                                            all_fee_items.append(it)
                                except Exception as ee:
                                    warn(f"asset_fallback_error | univ='{campus}' err={type(ee).__name__}:{ee}")

                            if verdict != "valid" or args.validate_only:
                                continue

                            info(f"extract | univ='{campus}' kind=image url={c.url}")
                            items = extract_fee_items_from_bytes(gemini, mime, fr.content)
                            items = _normalize_items(items)
                            items = narrow_fee_items(items)
                            info(f"extract_done | univ='{campus}' items={len(items)} url={c.url}")

                            for it in items:
                                it["_campus_name"] = campus
                                it["_official_website"] = base
                                it["_source_url"] = c.url
                                it["_source_page"] = c.source_page
                                if campus_id and not it.get("priceable_id"):
                                    it["priceable_type"] = it.get("priceable_type") or "Campus"
                                    it["priceable_id"] = campus_id
                                all_fee_items.append(it)

                    except Exception as e:
                        warn(f"validate/extract exception | univ='{campus}' kind={c.kind} url={c.url} err={type(e).__name__}:{e}")
                        v = to_validated(c, "uncertain", f"exception: {type(e).__name__}: {e}", "")
                        all_validated.append(v.__dict__)

                info(f"[{idx}/{total}] DONE univ='{campus}'")

        total = len(df)
        tasks = []
        for idx, (_, row) in enumerate(df.iterrows(), start=1):
            tasks.append(process_one(idx, total, row))
        await asyncio.gather(*tasks)

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

    fee_json = os.path.join(args.outdir, "fee_items_extracted.json")
    with open(fee_json, "w", encoding="utf-8") as f:
        json.dump(all_fee_items, f, ensure_ascii=False, indent=2)
    info(f"save | fee_items={fee_json}")

    # Build output xlsx based on template columns
    tpl = pd.read_excel(args.template)
    tpl_cols = list(tpl.columns)

    rows_out = []
    for it in all_fee_items:
        row = {c: None for c in tpl_cols}
        for k in [
            "name","slug","description","price_type","fixed_price","min_price","max_price",
            "payment_type","payment_frequency","promotion_type","discount_value","discount_unit",
            "cashback_value","cashback_unit","priceable_type","priceable_id"
        ]:
            if k in row:
                row[k] = it.get(k)
        rows_out.append(row)

    out_df = pd.DataFrame(rows_out, columns=tpl_cols)
    out_xlsx = os.path.join(args.outdir, "import_biaya_filled.xlsx")
    out_df.to_excel(out_xlsx, index=False)
    info(f"save | import_xlsx={out_xlsx}")
    info("DONE | all finished")

class _DummyAsyncContext:
    async def __aenter__(self): return None
    async def __aexit__(self, exc_type, exc, tb): return False

if __name__ == "__main__":
    asyncio.run(main())
