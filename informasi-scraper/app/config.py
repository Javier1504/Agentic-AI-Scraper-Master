from __future__ import annotations
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Input default 
DEFAULT_INPUT_XLSX = os.path.join(BASE_DIR, "input", "D:\Kehidupan\Sevima\Penugasan2\informasi_scraper_v2\input\edurank_top100_web_resmi.xlsx")

OUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUT_DIR, exist_ok=True)

STATE_DIR = os.path.join(OUT_DIR, "state")
os.makedirs(STATE_DIR, exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

HEADLESS = os.getenv("HEADLESS", "1").strip() not in ("0", "false", "False")

# Crawl limitation
MAX_PAGES_VISIT = 10              # per kampus
MAX_INTERNAL_CANDIDATES = 20      # kandidat link internal
MAX_TEXT_PER_PAGE = 20000         # char
MAX_COMBINED_TEXT = 80000         # gabungan char

# Playwright timeouts
NAV_TIMEOUT_MS = 45000
WAIT_AFTER_LOAD_MS = 1200
