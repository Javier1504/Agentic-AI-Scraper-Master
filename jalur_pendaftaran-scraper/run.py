from __future__ import annotations

import argparse
import asyncio
import json
import os
from io import BytesIO
from typing import Dict, Any, List

from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd
from bs4 import BeautifulSoup
from pypdf import PdfReader
from dotenv import load_dotenv

from logger import setup, info, warn, error
from fetcher import RequestsFetcher, PlaywrightFetcher
from crawler import crawl_site
from gemini_client import GeminiClient
from validator import validate_text_with_gemini, validate_bytes_with_gemini, fast_local_gate
from config import JALUR_WORD_RE
from extractor import extract_jalur_items_from_text, extract_jalur_items_from_bytes
from utils import CandidateLink, slugify
from checkpoint import (
    make_campus_id,
    checkpoint_path,
    read_json,
    atomic_write_json,
    init_checkpoint,
    touch_stats,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Input xlsx (kolom: kampus_name, official_website)")
    ap.add_argument("--sheet", default=None, help="Sheet name (optional)")
    ap.add_argument("--template", required=True, help="Template xlsx Import jalur (kolom DB)")
    ap.add_argument("--outdir", default="out", help="Folder output")
    # Banyak situs kampus butuh eksplor lebih dalam untuk menemukan halaman UKT per-prodi.
    ap.add_argument("--max-pages", type=int, default=150)
    ap.add_argument("--min-score", type=float, default=2.0)
    ap.add_argument("--timeout-ms", type=int, default=25000)
    ap.add_argument("--wait-after-ms", type=int, default=500)
    ap.add_argument("--no-playwright", action="store_true", help="Disable Playwright (requests only)")
    ap.add_argument("--validate-only", action="store_true", help="Hanya validasi link, tanpa ekstraksi jalur")
    ap.add_argument("--concurrency", type=int, default=2, help="Parallel kampus (hati-hati rate limit)")
    ap.add_argument("--log-level", default=None, help="DEBUG/INFO/WARN/ERROR")
    ap.add_argument("--checkpoint-dir", default=None, help="Folder checkpoint (default: <outdir>/checkpoints)")
    ap.add_argument("--no-resume", action="store_true", help="Jalankan tanpa resume checkpoint")
    ap.add_argument("--force", action="store_true", help="Paksa reprocess walaupun sudah ada checkpoint DONE")
    ap.add_argument("--checkpoint-every", type=int, default=1, help="Tulis checkpoint tiap N kandidat (default 1)")
    return ap.parse_args()

def ensure_outdir(path: str):
    os.makedirs(path, exist_ok=True)
    
def now_wib_str():
    return datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M:%S")

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

def enrich_jalur_item_with_campus(it: Dict[str, Any], campus_id: str, campus_name: str, official_website: str) -> Dict[str, Any]:
    """Pastikan setiap item punya identitas kampus pada field import (name/slug/description).

    Catatan:
    - Kolom `id` pada template import DB memang sebaiknya KOSONG (auto-increment / dibuat oleh DB).
    - Identitas kampus kita sematkan di `name`/`slug`/`description` supaya kamu selalu tahu ini milik univ mana.
    """
    campus_name = (campus_name or '').strip()
    campus_slug = slugify(campus_name)[:50] or (campus_id or 'campus')

    name = (it.get('name') or '').strip()
    if name and campus_name and campus_name.lower() not in name.lower():
        it['name'] = f"{campus_name} - {name}"

    # slug: prefix dengan kampus agar tidak tabrakan antar kampus
    slug = (it.get('slug') or '').strip() or slugify(it.get('name') or '')
    if slug and campus_slug and not slug.startswith(campus_slug + '-'): 
        it['slug'] = f"{campus_slug}-{slug}"

    desc = (it.get('description') or '').strip()
    if campus_name and (campus_name.lower() not in desc.lower()):
        # jangan terlalu panjang (hemat token & enak dibaca)
        prefix = f"Sumber: {campus_name} | "
        it['description'] = (prefix + desc) if desc else prefix.rstrip()

    # simpan metadata internal untuk debug / audit (tidak dipakai kolom template kecuali kamu mau)
    it.setdefault('_campus_id', campus_id)
    it.setdefault('_campus_name', campus_name)
    it.setdefault('_official_website', official_website)
    return it

def compute_is_active(start_date, end_date):
    try:
        today = date.today()

        if not end_date:
            return True  # kalau tidak ada end_date, anggap aktif

        end = pd.to_datetime(end_date).date()
        return end >= today
    except Exception:
        return True  # fallback aman


async def main():
    args = parse_args()
    ensure_outdir(args.outdir)

    checkpoint_dir = args.checkpoint_dir or os.path.join(args.outdir, "checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)

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
        async def fetch_best_html_text(url: str, hint: str = "", score: float = 0.0) -> tuple[str, str]:
            """Ambil teks HTML sebaik mungkin.

            - Requests dulu (cepat).
            - Jika gate gagal / konten pendek / indikasi tabel JS / link fee-ish, fallback Playwright.

            Return: (text, mode_used)
            """
            fr = req.fetch(url)
            text = html_to_text(fr.content) if (fr.ok and fr.content) else ""
            mode = fr.mode

            def _looks_dynamic(t: str) -> bool:
                lt = (t or "").lower()
                return any(x in lt for x in [
                    "wpdatatable", "wpdatatables", "tablepress", "datatable", "datatables",
                    "react", "vue", "__next_data__", "nuxt"
                ])

            if args.no_playwright or pw is None:
                return text, mode

            feeish = bool(JALUR_WORD_RE.search(url) or JALUR_WORD_RE.search(hint) or score >= max(2.0, args.min_score))
            too_short = len(text) < 900
            js_block = "enable javascript" in (text or "").lower() or "requires javascript" in (text or "").lower()
            needs_pw = (not fast_local_gate(text)) and (feeish or too_short or js_block or _looks_dynamic(text))

            if needs_pw:
                frp = await pw.fetch_html(url, wait_after_ms=max(args.wait_after_ms, 1500))
                if frp.ok and frp.content:
                    text2 = html_to_text(frp.content)
                    # pilih yang lebih informatif
                    if fast_local_gate(text2) or (len(text2) > len(text) * 1.2):
                        return text2, frp.mode

            return text, mode

        async def process_one(idx: int, total: int, row) -> None:
            campus = str(row["kampus_name"]).strip()
            base = str(row["official_website"]).strip()
            if not base:
                return

            campus_id = make_campus_id(campus, base)
            cp_path = checkpoint_path(checkpoint_dir, campus_id)

            # Resume logic: if checkpoint DONE and not --force, skip heavy work.
            if not args.no_resume and not args.force:
                cp = read_json(cp_path)
                if cp and cp.get("status") == "done":
                    info(f"[{idx}/{total}] SKIP (checkpoint DONE) univ='{campus}' id={campus_id}")
                    for c in cp.get("candidates", []) or []:
                        all_candidates.append(c)
                    for v in cp.get("validated", []) or []:
                        all_validated.append(v)
                    for it in cp.get("jalur_items", []) or []:
                        all_jalur_items.append(it)
                    return

            async with sem:
                info(f"[{idx}/{total}] START univ='{campus}' id={campus_id} base={base}")

                # Load or init checkpoint state
                cp_state = None
                if not args.no_resume and not args.force:
                    cp_state = read_json(cp_path)

                if not cp_state or args.force:
                    cp_state = init_checkpoint(campus_id, campus, base)
                    atomic_write_json(cp_path, cp_state)

                # If we already crawled candidates in checkpoint, reuse them
                cached_candidates = cp_state.get("candidates") or []
                candidates = []
                if cached_candidates:
                    # Rebuild CandidateLink objects is optional; we only need dicts for all_candidates,
                    # but crawl_site returns CandidateLink. We'll keep dicts and process with dict interface.
                    candidates = cached_candidates
                    info(f"[{idx}/{total}] RESUME_CANDIDATES univ='{campus}' cached={len(candidates)}")
                else:
                    found_links = await crawl_site(
                        campus_name=campus,
                        official_website=base,
                        fetch_html_async=fetch_html_async,
                        max_pages=args.max_pages,
                        min_candidate_score=args.min_score,
                    )

                    info(f"[{idx}/{total}] CRAWL_DONE univ='{campus}' candidates={len(found_links)}")

                    candidates = []
                    for c in found_links:
                        candidates.append({
                            "_campus_id": campus_id,
                            "campus_name": c.campus_name,
                            "official_website": c.official_website,
                            "url": c.url,
                            "kind": c.kind,
                            "source_page": c.source_page,
                            "context_hint": c.context_hint,
                            "score": c.score,
                        })

                    # Save candidates to checkpoint immediately
                    cp_state["candidates"] = candidates
                    cp_state["status"] = "crawled"
                    touch_stats(cp_state)
                    atomic_write_json(cp_path, cp_state)

                # Push candidates to global output list (dedup is optional)
                for c in candidates:
                    all_candidates.append(c)

                # Build resume sets
                validated_set = set()
                for v in (cp_state.get("validated") or []):
                    key = f"{v.get('kind')}::{v.get('url')}"
                    validated_set.add(key)

                extracted_set = set()
                for it in (cp_state.get("jalur_items") or []):
                    su = it.get("_source_url")
                    if su:
                        extracted_set.add(su)

                # validate + extract
                writes_since_flush = 0
                for j, c in enumerate(candidates, start=1):
                    # Rebuild CandidateLink object for safe attribute access + reuse existing helper functions
                    c_obj = CandidateLink(
                        campus_name=c.get("campus_name") or campus,
                        official_website=c.get("official_website") or base,
                        url=c.get("url") or "",
                        kind=c.get("kind") or "",
                        source_page=c.get("source_page") or "",
                        context_hint=c.get("context_hint") or "",
                        score=float(c.get("score") or 0.0),
                    )

                    kind = c_obj.kind
                    url = c_obj.url
                    key = f"{kind}::{url}"

                    if (not args.no_resume) and (not args.force) and key in validated_set:
                        info(f"validate | univ='{campus}' {j}/{len(candidates)} SKIP already-validated kind={kind} url={url}")
                        continue

                    info(f"validate | univ='{campus}' {j}/{len(candidates)} kind={kind} url={url}")

                    try:
                        if kind == "html":
                            # ⚡ Banyak tabel UKT/prodi dimuat via JS. Ambil versi HTML terbaik.
                            text, mode_used = await fetch_best_html_text(
                                url,
                                hint=c_obj.context_hint,
                                score=c_obj.score,
                            )

                            verdict, _reason_unused, snippet = validate_text_with_gemini(gemini, text)
                            # Simpan hasil validasi tanpa "reason" agar output ringkas (hemat token).
                            v = {
                                "_campus_id": campus_id,
                                "campus_name": campus,
                                "official_website": base,
                                "url": url,
                                "kind": kind,
                                "source_page": c.get("source_page"),
                                "verdict": verdict,
                                "extracted_hint": snippet,
                                "_fetch_mode": mode_used,
                            }
                            all_validated.append(v)
                            cp_state["validated"].append(v)
                            validated_set.add(key)
                            info(f"validate_result | univ='{campus}' verdict={verdict}")

                            writes_since_flush += 1
                            if args.checkpoint_every > 0 and writes_since_flush >= args.checkpoint_every:
                                touch_stats(cp_state)
                                atomic_write_json(cp_path, cp_state)
                                writes_since_flush = 0

                            if verdict != "valid" or args.validate_only:
                                continue

                            if (not args.no_resume) and (not args.force) and url in extracted_set:
                                info(f"extract | univ='{campus}' SKIP already-extracted kind=html url={url}")
                                continue

                            info(f"extract | univ='{campus}' kind=html url={url}")
                            items = extract_jalur_items_from_text(gemini, text)
                            info(f"extract_done | univ='{campus}' items={len(items)} url={url}")

                            for it in items:
                                it["_source_url"] = url
                                it["_source_page"] = c.get("source_page")
                                enrich_jalur_item_with_campus(it, campus_id, campus, base)
                                all_jalur_items.append(it)
                                cp_state["jalur_items"].append(it)

                            extracted_set.add(url)
                            writes_since_flush += 1
                            if args.checkpoint_every > 0 and writes_since_flush >= args.checkpoint_every:
                                touch_stats(cp_state)
                                atomic_write_json(cp_path, cp_state)
                                writes_since_flush = 0

                        elif kind == "pdf":
                            fr = req.fetch(url)
                            if not fr.ok or not fr.content:
                                v = {
                                    "_campus_id": campus_id,
                                    "campus_name": campus,
                                    "official_website": base,
                                    "url": url,
                                    "kind": kind,
                                    "source_page": c.get("source_page"),
                                    "verdict": "invalid",
                                    "extracted_hint": "",
                                }
                                all_validated.append(v)
                                cp_state["validated"].append(v)
                                validated_set.add(key)
                                continue

                            pdf_text = read_pdf_text(fr.content)

                            if pdf_text:
                                verdict, _reason_unused, snippet = validate_text_with_gemini(gemini, pdf_text)
                            else:
                                verdict, _reason_unused, snippet = validate_bytes_with_gemini(gemini, "application/pdf", fr.content)

                            v = {
                                "_campus_id": campus_id,
                                "campus_name": campus,
                                "official_website": base,
                                "url": url,
                                "kind": kind,
                                "source_page": c_obj.source_page,
                                "verdict": verdict,
                                "extracted_hint": snippet,
                            }
                            all_validated.append(v)
                            cp_state["validated"].append(v)
                            validated_set.add(key)
                            info(f"validate_result | univ='{campus}' verdict={verdict}")

                            writes_since_flush += 1
                            if args.checkpoint_every > 0 and writes_since_flush >= args.checkpoint_every:
                                touch_stats(cp_state)
                                atomic_write_json(cp_path, cp_state)
                                writes_since_flush = 0

                            if verdict != "valid" or args.validate_only:
                                continue

                            if (not args.no_resume) and (not args.force) and url in extracted_set:
                                info(f"extract | univ='{campus}' SKIP already-extracted kind=pdf url={url}")
                                continue

                            info(f"extract | univ='{campus}' kind=pdf url={url}")
                            items = extract_jalur_items_from_text(gemini, pdf_text) if pdf_text else extract_jalur_items_from_bytes(gemini, "application/pdf", fr.content)
                            info(f"extract_done | univ='{campus}' items={len(items)} url={url}")

                            for it in items:
                                it["_source_url"] = url
                                it["_source_page"] = c.get("source_page")
                                enrich_jalur_item_with_campus(it, campus_id, campus, base)
                                all_jalur_items.append(it)
                                cp_state["jalur_items"].append(it)

                            extracted_set.add(url)
                            writes_since_flush += 1
                            if args.checkpoint_every > 0 and writes_since_flush >= args.checkpoint_every:
                                touch_stats(cp_state)
                                atomic_write_json(cp_path, cp_state)
                                writes_since_flush = 0

                        elif kind == "image":
                            fr = req.fetch(url)
                            if not fr.ok or not fr.content:
                                v = {
                                    "_campus_id": campus_id,
                                    "campus_name": campus,
                                    "official_website": base,
                                    "url": url,
                                    "kind": kind,
                                    "source_page": c.get("source_page"),
                                    "verdict": "invalid",
                                    "extracted_hint": "",
                                }
                                all_validated.append(v)
                                cp_state["validated"].append(v)
                                validated_set.add(key)
                                continue

                            mime = fr.content_type or "image/jpeg"
                            verdict, _reason_unused, snippet = validate_bytes_with_gemini(gemini, mime, fr.content)

                            v = {
                                "_campus_id": campus_id,
                                "campus_name": campus,
                                "official_website": base,
                                "url": url,
                                "kind": kind,
                                "source_page": c_obj.source_page,
                                "verdict": verdict,
                                "extracted_hint": snippet,
                            }
                            all_validated.append(v)
                            cp_state["validated"].append(v)
                            validated_set.add(key)
                            info(f"validate_result | univ='{campus}' verdict={verdict}")

                            writes_since_flush += 1
                            if args.checkpoint_every > 0 and writes_since_flush >= args.checkpoint_every:
                                touch_stats(cp_state)
                                atomic_write_json(cp_path, cp_state)
                                writes_since_flush = 0

                            if verdict != "valid" or args.validate_only:
                                continue

                            if (not args.no_resume) and (not args.force) and url in extracted_set:
                                info(f"extract | univ='{campus}' SKIP already-extracted kind=image url={url}")
                                continue

                            info(f"extract | univ='{campus}' kind=image url={url}")
                            items = extract_jalur_items_from_bytes(gemini, mime, fr.content)
                            info(f"extract_done | univ='{campus}' items={len(items)} url={url}")

                            for it in items:
                                it["_source_url"] = url
                                it["_source_page"] = c.get("source_page")
                                enrich_jalur_item_with_campus(it, campus_id, campus, base)
                                all_jalur_items.append(it)
                                cp_state["jalur_items"].append(it)

                            extracted_set.add(url)
                            writes_since_flush += 1
                            if args.checkpoint_every > 0 and writes_since_flush >= args.checkpoint_every:
                                touch_stats(cp_state)
                                atomic_write_json(cp_path, cp_state)
                                writes_since_flush = 0

                    except Exception as e:
                        warn(f"validate/extract exception | univ='{campus}' kind={kind} url={url} err={type(e).__name__}:{e}")
                        v = {
                            "_campus_id": campus_id,
                            "campus_name": campus,
                            "official_website": base,
                            "url": url,
                            "kind": kind,
                            "source_page": c_obj.source_page,
                            "verdict": "uncertain",
                            "extracted_hint": "",
                            "_error_type": type(e).__name__,
                        }
                        all_validated.append(v)
                        cp_state["validated"].append(v)
                        validated_set.add(key)
                        cp_state["errors"].append(type(e).__name__)

                        writes_since_flush += 1
                        if args.checkpoint_every > 0 and writes_since_flush >= args.checkpoint_every:
                            touch_stats(cp_state)
                            atomic_write_json(cp_path, cp_state)
                            writes_since_flush = 0

                # Final flush for this campus
                touch_stats(cp_state)
                cp_state["status"] = "done"
                atomic_write_json(cp_path, cp_state)

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

    jalur_json = os.path.join(args.outdir, "jalur_items_extracted.json")
    with open(jalur_json, "w", encoding="utf-8") as f:
        json.dump(all_jalur_items, f, ensure_ascii=False, indent=2)
    info(f"save | jalur_items={jalur_json}")

    # Build output xlsx based on template columns
    tpl = pd.read_excel(args.template)
    tpl_cols = list(tpl.columns)
    
    # mapping university_name -> university_id dari template
    univ_map = {}
    if "university_id" in tpl.columns and "name" in tpl.columns:
        for _, r in tpl.iterrows():
            uname = str(r.get("name") or "").strip().lower()
            uid = r.get("university_id")
            if uname and uid:
                univ_map[uname] = uid

    rows_out = []
    now_wib = now_wib_str()

    for idx, it in enumerate(all_jalur_items, start=1):

        row = {c: None for c in tpl_cols}

        campus_name = (it.get("_campus_name") or "").strip().lower()
        university_id = univ_map.get(campus_name)

        # =========================
        # REQUIRED DB FIELDS
        # =========================
        row["id"] = idx
        row["university_id"] = university_id
        row["name"] = it.get("name")
        row["slug"] = it.get("slug")
        row["description"] = it.get("description")
        row["start_date"] = it.get("start_date")
        row["end_date"] = it.get("end_date")

        # selalu pakai URL sumber
        row["url"] = it.get("_source_url") or it.get("url")

        # selalu aktif
        row["is_active"] = compute_is_active(
            it.get("start_date"),
            it.get("end_date")
        )

        # timestamp WIB
        row["created_at"] = now_wib
        row["updated_at"] = now_wib

        # deleted fields default null
        row["deleted_at"] = None
        row["created_by"] = None
        row["updated_by"] = None
        row["deleted_by"] = None

        rows_out.append(row)

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
