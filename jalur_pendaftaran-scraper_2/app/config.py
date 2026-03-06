from dataclasses import dataclass
from dotenv import load_dotenv
import os

@dataclass
class Settings:
    gemini_api_key: str
    gemini_model: str

    google_cse_api_key: str
    google_cse_cx: str
    google_cse_num: int
    google_cse_max_results: int

    search_query: str

    http_timeout: int
    user_agent: str
    max_pages_per_domain: int
    use_playwright: bool

    univ_input_path: str
    univ_id_col: str
    univ_url_col: str

    template_xlsx: str
    output_xlsx: str
    state_dir: str

def load_settings() -> Settings:
    load_dotenv()

    def geti(k: str, default: str = "0") -> int:
        return int(os.getenv(k, default).strip())

    def getb(k: str, default: str = "0") -> bool:
        return os.getenv(k, default).strip().lower() in ("1", "true", "yes")

    gem_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gem_key:
        raise RuntimeError("GEMINI_API_KEY belum diisi di .env")

    cse_key = os.getenv("GOOGLE_CSE_API_KEY", "").strip()
    cse_cx = os.getenv("GOOGLE_CSE_CX", "").strip()
    if not cse_key or not cse_cx:
        raise RuntimeError("GOOGLE_CSE_API_KEY / GOOGLE_CSE_CX belum diisi di .env")

    return Settings(
        gemini_api_key=gem_key,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip(),

        google_cse_api_key=cse_key,
        google_cse_cx=cse_cx,
        google_cse_num=geti("GOOGLE_CSE_NUM", "10"),
        google_cse_max_results=geti("GOOGLE_CSE_MAX_RESULTS", "100"),

        search_query=os.getenv(
            "SEARCH_QUERY",
            'site:ac.id ("jalur pendaftaran" OR "jadwal pendaftaran" OR "pmb")'
        ).strip(),

        http_timeout=geti("HTTP_TIMEOUT", "25"),
        user_agent=os.getenv("USER_AGENT", "Mozilla/5.0").strip(),
        max_pages_per_domain=geti("MAX_PAGES_PER_DOMAIN", "30"),
        use_playwright=getb("USE_PLAYWRIGHT", "1"),

        univ_input_path=os.getenv("UNIV_INPUT_PATH", "input_universities.xlsx").strip(),
        univ_id_col=os.getenv("UNIV_ID_COL", "rank_id").strip(),
        univ_url_col=os.getenv("UNIV_URL_COL", "official_website_url").strip(),

        template_xlsx=os.getenv("TEMPLATE_XLSX", "(6) Import jalur pendaftaran.xlsx").strip(),
        output_xlsx=os.getenv("OUTPUT_XLSX", "importjalur_filled2.xlsx").strip(),
        state_dir=os.getenv("STATE_DIR", ".state").strip(),
    )
