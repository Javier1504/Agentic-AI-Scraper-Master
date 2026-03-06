from app.config import load_settings
from app.domain_map import build_domain_map
from app.search_google_cse import google_cse_links
from app.fetcher import PageFetcher
from app.extract import extract_jalur_from_url
from app.writer_xlsx import PricesWorkbookWriter
from app.utils import load_json, save_json, sha1

import os
import traceback

def main():
    s = load_settings()
    os.makedirs(s.state_dir, exist_ok=True)

    domain_to_id = build_domain_map(
        path=s.univ_input_path,
        id_col=s.univ_id_col,
        url_col=s.univ_url_col,
        state_dir=s.state_dir
    )
    print(f"[INFO] domain_map size = {len(domain_to_id)}")

    all_links = []

    for dom in domain_to_id.keys():

        query = f"site:{dom} (pmb OR 'jalur pendaftaran' OR 'jadwal pendaftaran')"

        links = google_cse_links(
            query=query,
            api_key=s.google_cse_api_key,
            cx=s.google_cse_cx,
            num=s.google_cse_num,
            max_results=30,
            state_dir=s.state_dir,
            timeout=s.http_timeout,
            user_agent=s.user_agent,
        )

        all_links.extend(links)

    links = all_links
    print(f"[INFO] search produced {len(links)} URLs")

    # Resume output: kalau file output sudah ada, jangan hapus isinya
    output_exists = os.path.exists(s.output_xlsx)
    writer = PricesWorkbookWriter(template_path=s.template_xlsx, out_path=s.output_xlsx)
    writer.prepare_fresh_sheet(fresh=not output_exists)

    fetcher = PageFetcher(timeout=s.http_timeout, user_agent=s.user_agent, use_playwright=s.use_playwright)

    state_path = os.path.join(s.state_dir, "progress.json")
    err_path = os.path.join(s.state_dir, "errors.log")
    progress = load_json(state_path, default={"done": [], "per_domain": {}, "errors": []})

    mapped = 0
    unmapped = 0
    total_items = 0

    for i, url in enumerate(links, start=1):
        uhash = sha1(url)
        if uhash in progress["done"]:
            continue

        dom = fetcher.get_registrable_domain(url)
        univ_id = domain_to_id.get(dom)

        if univ_id is None:
            unmapped += 1
        else:
            mapped += 1

        per = progress["per_domain"].get(dom, 0)
        if s.max_pages_per_domain > 0 and per >= s.max_pages_per_domain:
            progress["done"].append(uhash)
            save_json(state_path, progress)
            continue

        print(f"[{i}/{len(links)}] dom={dom} univ_id={univ_id} url={url}")

        try:
            items = extract_jalur_from_url(
                url=url,
                fetcher=fetcher,
                gemini_api_key=s.gemini_api_key,
                model=s.gemini_model,
                university_id=univ_id,
            )

            if items:
                writer.append_items(items)
                total_items += len(items)
                print(f"[OK] extracted {len(items)} item(s)")
            else:
                print("[NO] no ukt extracted")

        except KeyboardInterrupt:
            # kalau kamu stop manual, tetap save dulu
            print("[INTERRUPT] Saving progress and output...")
            writer.save()
            raise

        except Exception as e:
            # jangan berhenti: simpan log dan lanjut URL berikutnya
            msg = f"[ERROR] url={url} dom={dom} err={repr(e)}"
            print(msg)
            with open(err_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
                f.write(traceback.format_exc() + "\n\n")

            progress["errors"].append({"url": url, "domain": dom, "error": repr(e)})

        # checkpoint + per-domain + mark done
        progress["done"].append(uhash)
        progress["per_domain"][dom] = per + 1
        save_json(state_path, progress)

        # autosave output setiap URL (atau tiap 10 detik)
        writer.autosave(min_interval_s=5)

    writer.save()
    print(f"[SUMMARY] links={len(links)} mapped={mapped} unmapped={unmapped} items={total_items}")
    print(f"[DONE] Output saved to: {s.output_xlsx}")
    print(f"[DONE] Error log (if any): {err_path}")

if __name__ == "__main__":
    main()