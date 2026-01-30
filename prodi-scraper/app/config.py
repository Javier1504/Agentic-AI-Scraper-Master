from __future__ import annotations
import os
from dotenv import load_dotenv
 # root project
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) 
load_dotenv(os.path.join(BASE_DIR, ".env"))
#set model dan konfig
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL   = (os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()
HEADLESS = True
NAV_TIMEOUT_MS = 60_000
WAIT_AFTER_LOAD_MS = 1200
# Prodi crawlerr
MAX_INTERNAL_CANDIDATES = 25     
MAX_PAGES_VISIT = 15             
# maksimal teks yang diambil tiap halaman 
MAX_TEXT_PER_PAGE = 20_000
MAX_COMBINED_TEXT = 80_000
# run script behavior, kalo text halaman kurang dari 600 ini, skip ga valid
MIN_TEXT_TO_EXTRACT = 600   
#save cekpoin    
SAVE_EVERY_UNIV = 1           
OUT_DIR = os.path.join(BASE_DIR, "output")
STATE_DIR = os.path.join(OUT_DIR, "state")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)
