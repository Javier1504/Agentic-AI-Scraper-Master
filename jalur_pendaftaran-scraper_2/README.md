# AGENTIC AI BIAYA (Google CSE + Gemini) - Plug & Play

## Fungsi
1) Ambil URL hasil Google untuk query: `site:ac.id ukt` menggunakan **Google Custom Search JSON API**.
2) Fetch tiap URL (requests; fallback Playwright jika perlu render JS).
3) **Gemini 2.5 Flash** mengekstrak UKT menjadi JSON (nama item + nominal Rupiah) dari teks halaman.
4) Hasil ditulis ke template `(5) Import biaya.xlsx` sheet **Format Excel** menjadi `importbiaya_filled.xlsx`.
5) Ada checkpoint `.state/` untuk resume.

## Setup cepat (Windows PowerShell)
```powershell
cd AGENTIC_AI_BIAYA_GOOGLECSE
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
notepad .env
python run.py
```

## Isi .env (WAJIB)
- GEMINI_API_KEY=...
- GOOGLE_CSE_API_KEY=...
- GOOGLE_CSE_CX=...

## Mapping kampus (WAJIB)
File `input_universities.xlsx` minimal kolom:
- `rank_id`
- `official_website_url`

## Output
- `importbiaya_filled.xlsx`
