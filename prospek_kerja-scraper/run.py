from __future__ import annotations

import json
import os
import signal
from typing import Any, Dict, List, Set, Tuple

from dotenv import load_dotenv
from tqdm import tqdm
import requests

from crawler import CrawlerConfig, iter_all_majors, fetch_html
from extract_prospek import extract_prospek_heuristic, extract_snippet_around_prospek
from gemini_client import build_gemini_from_env
from enrich_jobs import enrich_job_with_gemini, enrich_jobs_with_gemini_batch, slugify
from exporters import ensure_dir, write_master_xlsx


# ========= IO helpers =========
def load_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def append_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_checkpoint(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"done_urls": [], "jobs_done": [], "stats": {"majors": 0, "jobs": 0}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_checkpoint(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def build_job_titles_from_majors(majors_rows: List[Dict[str, Any]]) -> Set[str]:
    titles: Set[str] = set()
    for r in majors_rows:
        prospek = r.get("prospek") or []
        if isinstance(prospek, list):
            for jt in prospek:
                jt = str(jt).strip()
                if jt:
                    titles.add(jt)
    return titles


# ========= Ctrl+C graceful =========
STOP_REQUESTED = False

def _handle_sigint(signum, frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True

signal.signal(signal.SIGINT, _handle_sigint)


def main() -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(base_dir, ".env"))

    base_url = os.getenv("BASE_URL", "https://akupintar.id").strip()
    listing_path = os.getenv("LISTING_PATH", "/jurusan").strip()
    page_size = int(os.getenv("PAGE_SIZE", "75"))
    max_pages = int(os.getenv("MAX_PAGES", "0"))
    timeout_s = int(os.getenv("TIMEOUT_S", "25"))
    retry = int(os.getenv("RETRY", "3"))
    out_dir = os.getenv("OUT_DIR", "output").strip()

    # batching (agar hemat & cepat)
    JOB_BATCH = int(os.getenv("JOB_BATCH", "8"))          # <= 8 biasanya aman
    FLUSH_XLSX_EVERY = int(os.getenv("FLUSH_XLSX_EVERY", "300"))

    ensure_dir(out_dir)

    majors_jsonl = os.path.join(out_dir, "majors_prospek.jsonl")
    jobs_jsonl = os.path.join(out_dir, "jobs_master.jsonl")
    jobs_xlsx = os.path.join(out_dir, "jobs_master.xlsx")
    ckpt_path = os.path.join(out_dir, "checkpoint.json")

    ckpt = load_checkpoint(ckpt_path)
    done_urls: Set[str] = set(ckpt.get("done_urls", []))
    jobs_done: Set[str] = set(ckpt.get("jobs_done", []))
    stats = ckpt.get("stats") or {"majors": 0, "jobs": 0}

    # load existing outputs so we can resume safely (lebih tahan jika checkpoint "kotor")
    majors_existing = load_jsonl(majors_jsonl)
    jobs_existing = load_jsonl(jobs_jsonl)

    existing_major_urls = {str(r.get("major_url", "")).strip() for r in majors_existing if r.get("major_url")}
    existing_job_slugs = {str(r.get("slug", "")).strip() for r in jobs_existing if r.get("slug")}
    existing_source_titles = set()
    for r in jobs_existing:
        st = str(r.get("source_title", "")).strip()
        if st:
            existing_source_titles.add(st)
        else:
            # kompatibilitas file lama: tidak punya source_title
            nm = str(r.get("name", "")).strip()
            if nm:
                existing_source_titles.add(nm)

    # gabungkan: checkpoint + apa yang sudah benar-benar tersimpan
    jobs_done |= {t for t in existing_source_titles if t}

    gem = build_gemini_from_env()

    cfg = CrawlerConfig(
        base_url=base_url,
        listing_path=listing_path,
        page_size=page_size,
        max_pages=max_pages,
        timeout_s=timeout_s,
        retry=retry,
    )

    session = requests.Session()
    session.headers.update({
        "User-Agent": cfg.user_agent,
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    })

    # ======================
    # 1) Crawl majors (append + checkpoint)
    # ======================
    majors = list(iter_all_majors(cfg))
    buffer_major: List[Dict[str, Any]] = []

    pbar = tqdm(majors, desc="Crawl jurusan", unit="jurusan")
    for m in pbar:
        if STOP_REQUESTED:
            break

        url = m["url"]
        if url in done_urls or url in existing_major_urls:
            done_urls.add(url)
            continue

        html = fetch_html(session, url, timeout_s, retry)
        res = extract_prospek_heuristic(html)

        # fallback AI only if needed
        if (not res.prospek) or res.confidence < 0.6:
            if gem:
                snippet = extract_snippet_around_prospek(html)
                system = "Ekstrak daftar prospek kerja dari HTML jurusan. Fokus hanya prospek kerja."
                user = f"""Keluarkan JSON: {{\"prospek\": [\"...\"]}}

HTML:
{snippet}
"""
                try:
                    data = gem.generate_json(system=system, user=user, schema_hint={"required": ["prospek"]})
                    prospek = data.get("prospek") or []
                    if isinstance(prospek, list):
                        prospek = [str(x).strip() for x in prospek if str(x).strip()]
                        if prospek:
                            res.prospek = prospek
                            res.method = "gemini_fallback"
                            res.confidence = 0.75
                except Exception:
                    pass

        record = {
            "major_name": m.get("name", ""),
            "major_url": url,
            "prospek": res.prospek,
            "extract_method": res.method,
            "confidence": res.confidence,
        }

        buffer_major.append(record)
        existing_major_urls.add(url)
        done_urls.add(url)

        stats["majors"] = int(stats.get("majors", 0)) + 1
        ckpt["done_urls"] = sorted(done_urls)
        ckpt["stats"] = stats
        save_checkpoint(ckpt_path, ckpt)

        # flush majors periodically
        if len(buffer_major) >= 25:
            append_jsonl(majors_jsonl, buffer_major)
            buffer_major.clear()

    append_jsonl(majors_jsonl, buffer_major)
    buffer_major.clear()

    if STOP_REQUESTED:
        ckpt["done_urls"] = sorted(done_urls)
        ckpt["jobs_done"] = sorted(jobs_done)
        ckpt["stats"] = stats
        save_checkpoint(ckpt_path, ckpt)
        print("\nSTOPPED (Ctrl+C). Checkpoint saved. Run again: python run.py")
        return

    # ======================
    # 2) Build job titles from majors file (resume-safe)
    # ======================
    majors_all = load_jsonl(majors_jsonl)
    job_titles = sorted(build_job_titles_from_majors(majors_all))

    # ======================
    # 3) Enrich jobs (append + checkpoint)
    # ======================
    new_jobs_buffer: List[Dict[str, Any]] = []
    new_jobs_since_xlsx = 0

    def flush_jobs():
        nonlocal new_jobs_since_xlsx
        if new_jobs_buffer:
            append_jsonl(jobs_jsonl, new_jobs_buffer)
            new_jobs_since_xlsx += len(new_jobs_buffer)
            new_jobs_buffer.clear()

        ckpt["jobs_done"] = sorted(jobs_done)
        ckpt["done_urls"] = sorted(done_urls)
        ckpt["stats"] = stats
        save_checkpoint(ckpt_path, ckpt)

        if new_jobs_since_xlsx >= FLUSH_XLSX_EVERY:
            jobs_all2 = load_jsonl(jobs_jsonl)
            write_master_xlsx(jobs_xlsx, jobs_all2)
            new_jobs_since_xlsx = 0

    # hanya proses judul yang belum selesai
    pending = [t for t in job_titles if t not in jobs_done]

    pbar2 = tqdm(pending, desc="Enrich jobs", unit="job")
    batch: List[str] = []

    def commit_row(source_title: str, row: Dict[str, Any]) -> None:
        nonlocal stats
        # tambah source_title agar resume akurat
        row = dict(row)
        row["source_title"] = source_title

        slug = str(row.get("slug") or "").strip()
        name = str(row.get("name") or "").strip()

        if not slug and name:
            slug = slugify(name)
            row["slug"] = slug

        # dedup by slug (lebih stabil daripada name)
        if slug and slug in existing_job_slugs:
            jobs_done.add(source_title)
            return

        if slug:
            existing_job_slugs.add(slug)

        new_jobs_buffer.append(row)
        jobs_done.add(source_title)
        stats["jobs"] = int(stats.get("jobs", 0)) + 1

    for title in pbar2:
        if STOP_REQUESTED:
            break

        batch.append(title)
        if len(batch) < max(1, JOB_BATCH):
            continue

        # process current batch
        if not gem:
            for jt in batch:
                commit_row(jt, {
                    "name": jt,
                    "slug": slugify(jt),
                    "description": "",
                    "min_salary": 0,
                    "max_salary": 0,
                    "positions": [jt],
                })
        else:
            try:
                pairs = enrich_jobs_with_gemini_batch(gem, batch) if JOB_BATCH > 1 else [(jt, enrich_job_with_gemini(gem, jt)) for jt in batch]
                got = {src for (src, _) in pairs}
                # commit all results
                for (src, jobrec) in pairs:
                    commit_row(src, jobrec.to_row())

                # fallback per-item untuk yang hilang
                missing = [jt for jt in batch if jt not in got]
                for jt in missing:
                    job = enrich_job_with_gemini(gem, jt)
                    commit_row(jt, job.to_row())

            except Exception:
                # kalau batch gagal parse, fallback per-item (lebih lambat tapi aman)
                for jt in batch:
                    try:
                        job = enrich_job_with_gemini(gem, jt)
                        commit_row(jt, job.to_row())
                    except Exception:
                        # terakhir: simpan minimal
                        commit_row(jt, {
                            "name": jt,
                            "slug": slugify(jt),
                            "description": "",
                            "min_salary": 0,
                            "max_salary": 0,
                            "positions": [jt],
                        })

        batch.clear()
        flush_jobs()

    # process remaining batch
    if not STOP_REQUESTED and batch:
        # re-run same logic for sisa batch
        if not gem:
            for jt in batch:
                commit_row(jt, {
                    "name": jt,
                    "slug": slugify(jt),
                    "description": "",
                    "min_salary": 0,
                    "max_salary": 0,
                    "positions": [jt],
                })
        else:
            try:
                pairs = enrich_jobs_with_gemini_batch(gem, batch) if JOB_BATCH > 1 else [(jt, enrich_job_with_gemini(gem, jt)) for jt in batch]
                got = {src for (src, _) in pairs}
                for (src, jobrec) in pairs:
                    commit_row(src, jobrec.to_row())
                missing = [jt for jt in batch if jt not in got]
                for jt in missing:
                    job = enrich_job_with_gemini(gem, jt)
                    commit_row(jt, job.to_row())
            except Exception:
                for jt in batch:
                    try:
                        job = enrich_job_with_gemini(gem, jt)
                        commit_row(jt, job.to_row())
                    except Exception:
                        commit_row(jt, {
                            "name": jt,
                            "slug": slugify(jt),
                            "description": "",
                            "min_salary": 0,
                            "max_salary": 0,
                            "positions": [jt],
                        })

        batch.clear()
        flush_jobs()

    if STOP_REQUESTED:
        flush_jobs()
        jobs_all3 = load_jsonl(jobs_jsonl)
        write_master_xlsx(jobs_xlsx, jobs_all3)
        print("\nSTOPPED (Ctrl+C). Checkpoint saved. Run again: python run.py")
        return

    # final xlsx
    flush_jobs()
    jobs_all = load_jsonl(jobs_jsonl)
    write_master_xlsx(jobs_xlsx, jobs_all)

    print("\nDONE")
    print(f"- {majors_jsonl}")
    print(f"- {jobs_jsonl}")
    print(f"- {jobs_xlsx}")
    print(f"- checkpoint: {ckpt_path}")
    print(f"- stats: {stats}")


if __name__ == "__main__":
    main()
