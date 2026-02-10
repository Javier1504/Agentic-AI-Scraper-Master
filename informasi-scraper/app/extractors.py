from __future__ import annotations
from typing import Any, Dict, List, Tuple
import re

# skema
SCHEMA_IMPORT = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},          # key: university/institute/polytechnic/academy
        "status": {"type": "string"},        # key: state/private
        "accreditation": {"type": "string"}, # key: A/B/C/U/BA/BS/-
        "address": {"type": "string"},
        "postal_code": {"type": "string"},

        "email": {"type": "string"},
        "phone": {"type": "string"},
        "whatsapp": {"type": "string"},

        "facebook": {"type": "string"},
        "instagram": {"type": "string"},
        "twitter": {"type": "string"},
        "youtube": {"type": "string"},

        "province_name": {"type": "string"},
        "city_name": {"type": "string"},
    },
    "required": [
        "type","status","accreditation","address","postal_code",
        "email","phone","whatsapp",
        "facebook","instagram","twitter","youtube",
        "province_name","city_name"
    ]
}

RULES_INFO = """
ATURAN SUPER KETAT (ANTI HALU):
- Anda HANYA boleh mengisi email/phone/whatsapp & social link jika NILAI itu muncul di bukti.
- Bukti yang valid hanya dari: TEXT atau LINKS (daftar URL). Jika tidak ada bukti eksplisit, isi "-" .
- Dilarang menebak, dilarang membuat akun sosial/nomor telepon/WA.
- Output HARUS sesuai key berikut:
  type: salah satu ["universitas","institut","politeknik","akademi","-"]
  status: salah satu ["negeri","swasta","-"]
  accreditation: salah satu ["A","B","C","U","BA","BS","-"]
- address: alamat ringkas (jalan/kota/prov). Jika tak ada, "-"
- postal_code: kode pos angka jika ada, else "-"
- social links: harus URL (misal https://instagram.com/xxx), jika tidak ada maka "-"
- phone/whatsapp: harus nomor/URL yang ada di bukti (misal +6231..., wa.me/62...), jika tidak ada maka "-"

TUGAS:
Dari bukti TEXT dan LINKS website resmi kampus, ekstrak informasi berikut.
"""

# VISI MISI schema 
SCHEMA_VISI = {
    "type": "object",
    "properties": {
        "visi": {"type": "string"},
        "misi": {"type": "string"},
        "sejarah_deskripsi": {"type": "string"},
    },
    "required": ["visi","misi","sejarah_deskripsi"]
}

RULES_VISI = """
ATURAN KETAT:
- Jangan mengarang. Jika tidak ditemukan, isi "-" .
- visi: ringkas (boleh 1 paragraf).
- misi: jika list, tulis poin dipisah "; " (bukan bullet).
- sejarah_deskripsi: 1-3 paragraf ringkas, bukan noise.

TUGAS:
Ambil VISI, MISI, dan SEJARAH/DESKRIPSI kampus dari bukti teks.
"""

# ==========
# Normalizer
# ==========
def normalize_info_keys(d: Dict[str, Any]) -> Dict[str, str]:
    """
    Normalisasi nilai default ('-') dan trimming.
    Catatan: normalisasi type/status ke Bahasa Indonesia dilakukan di postprocess_info().
    """
    keys = ["type","status","accreditation","address","postal_code",
            "email","phone","whatsapp","facebook","instagram","twitter","youtube",
            "province_name","city_name"]
    out: Dict[str, str] = {}
    for k in keys:
        v = d.get(k, "-")
        if v is None or str(v).strip() == "":
            v = "-"
        out[k] = str(v).strip()
    return out


# =========================
# Post-process (Bahasa Indonesia + Anti Bentrok)
# =========================

TYPE_MAP = {
    # english -> indonesia
    "university": "universitas",
    "institute": "institut",
    "polytechnic": "politeknik",
    "academy": "akademi",
    # indonesia
    "universitas": "universitas",
    "universiti": "universitas",
    "institut": "institut",
    "politeknik": "politeknik",
    "akademi": "akademi",
}

STATUS_MAP = {
    # english
    "state": "negeri",
    "public": "negeri",
    "government": "negeri",
    "private": "swasta",
    # indonesia
    "negeri": "negeri",
    "ptn": "negeri",
    "swasta": "swasta",
    "pts": "swasta",
}

UNKNOWN_MARKERS = {"-", "", "n/a", "na", "none", "null", "unknown", "unk", "u"}

# Override PTN yang tidak selalu menyebut kata "Negeri" di website resmi
# (Silakan tambah jika ada yang belum masuk)
KNOWN_PTN_NAMES = {
    "Universitas Indonesia",
    "Universitas Gadjah Mada",
    "Institut Teknologi Bandung",
    "Institut Teknologi Sepuluh Nopember",
    "Institut Pertanian Bogor",
    "IPB University",
    "Universitas Airlangga",
    "Universitas Diponegoro",
    "Universitas Padjadjaran",
    "Universitas Brawijaya",
    "Universitas Sebelas Maret",
    "Universitas Hasanuddin",
    "Universitas Andalas",
    "Universitas Sriwijaya",
    "Universitas Sumatera Utara",
    "Universitas Negeri Malang",
    "Universitas Negeri Yogyakarta",
    "Universitas Negeri Surabaya",
    "Universitas Negeri Semarang",
    "Universitas Negeri Jakarta",
    "Universitas Pendidikan Indonesia",
    "Universitas Udayana",
    "Universitas Syiah Kuala",
    "Universitas Lambung Mangkurat",
    "Universitas Jember",
    "Universitas Mataram",
    "Universitas Negeri Padang",
    "Universitas Negeri Makassar",
    "Universitas Negeri Medan",
    "Universitas Negeri Manado",
    "Universitas Negeri Gorontalo",
    "Universitas Riau",
    "Universitas Lampung",
    "Universitas Jenderal Soedirman",
    "Universitas Tanjungpura",
    "Universitas Mulawarman",
    "Universitas Sam Ratulangi",
    "Universitas Pattimura",
    "Universitas Cenderawasih",
    "Universitas Bengkulu",
    "Universitas Borneo Tarakan",
    "Universitas Papua",
    "Universitas Negeri Papua",
    "Universitas Halu Oleo",
    "Universitas Tadulako",
    "Universitas Nusa Cendana",
    "Universitas Negeri Bali",
    "Universitas Islam Negeri Sunan Kalijaga",
    "Universitas Islam Negeri Syarif Hidayatullah Jakarta",
    "Universitas Islam Negeri Maulana Malik Ibrahim Malang",
    "UIN Sunan Kalijaga",
    "UIN Syarif Hidayatullah Jakarta",
    "UIN Maulana Malik Ibrahim Malang",
    "Politeknik Negeri Bandung",
    "Politeknik Negeri Jakarta",
    "Politeknik Negeri Semarang",
    "Politeknik Negeri Malang",
    "Politeknik Negeri Sriwijaya",
    "Politeknik Negeri Bali",
}

KNOWN_PTN_DOMAINS = {
    "ui.ac.id",
    "ugm.ac.id",
    "itb.ac.id",
    "its.ac.id",
    "ipb.ac.id",
    "ipb.ac.id",
    "unair.ac.id",
    "undip.ac.id",
    "unpad.ac.id",
    "ub.ac.id",
    "uns.ac.id",
    "unhas.ac.id",
    "usu.ac.id",
    "unand.ac.id",
    "unsri.ac.id",
}

def _norm_token(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def infer_type_from_name(name: str) -> str:
    n = (name or "").strip()
    nl = n.lower()
    if not n:
        return "-"
    # Prioritas kata Indonesia
    if nl.startswith("institut ") or " institut " in f" {nl} ":
        return "institut"
    if nl.startswith("politeknik ") or " politeknik " in f" {nl} ":
        return "politeknik"
    if nl.startswith("akademi ") or " akademi " in f" {nl} ":
        return "akademi"
    if nl.startswith("universitas ") or " universitas " in f" {nl} ":
        return "universitas"
    # English fallback
    if "institute" in nl:
        return "institut"
    if "polytechnic" in nl:
        return "politeknik"
    if "academy" in nl:
        return "akademi"
    if "university" in nl:
        return "universitas"
    return "-"

def infer_status_from_signals(name: str, website: str, raw_status: str, text_blob: str = "") -> str:
    """
    Aturan:
    1) Kalau kampus ada di override PTN (nama/domain) => negeri.
    2) Kalau ada sinyal kuat "Negeri"/PTN/UIN/IAIN/Politeknik Negeri => negeri.
    3) Kalau ada sinyal kuat "Swasta"/PTS/Yayasan => swasta.
    4) Kalau raw_status sudah jelas (state/private/negeri/swasta) => map.
    5) Selain itu => '-'
    """
    name = (name or "").strip()
    raw = _norm_token(raw_status)
    web = (website or "").strip().lower()
    domain = web
    # website bisa berupa URL lengkap
    m = re.search(r"^(?:https?://)?([^/]+)", web)
    if m:
        domain = m.group(1)

    if name in KNOWN_PTN_NAMES or domain in KNOWN_PTN_DOMAINS:
        return "negeri"

    nl = name.lower()
    blob = (text_blob or "").lower()

    # Sinyal kuat negeri
    strong_negeri = [
        "universitas negeri",
        "politeknik negeri",
        "ptn",
        "perguruan tinggi negeri",
        "kementerian",
        "uin ",
        "iain ",
        "stain ",
    ]
    if any(s in nl for s in ["universitas negeri", "politeknik negeri"]) or nl.startswith(("uin ", "iain ", "stain ")):
        return "negeri"
    if any(s in blob for s in strong_negeri):
        return "negeri"

    # Sinyal kuat swasta
    strong_swasta = [
        "universitas swasta",
        "perguruan tinggi swasta",
        "pts",
        "yayasan",
        "foundation",
    ]
    if any(s in blob for s in strong_swasta):
        return "swasta"

    # raw_status mapping
    if raw in UNKNOWN_MARKERS:
        return "-"
    if raw in STATUS_MAP:
        return STATUS_MAP[raw]

    # kadang model ngisi "U" (unknown) — khusus status harus jadi '-'
    if raw == "u":
        return "-"

    return "-"

def postprocess_info(name: str, website: str, info: Dict[str, str], text_blob: str = "") -> Dict[str, str]:
    """
    - Ubah nilai type/status ke Bahasa Indonesia (universitas/institut/... dan negeri/swasta/-)
    - Pastikan status unknown TIDAK memakai 'U' (bentrok dengan akreditasi unggul)
    - Koreksi type dari nama kampus (mis. ITS => institut)
    - Koreksi status PTN untuk kampus-kampus yang tidak menyebut 'Negeri' di website (UI, UGM, ITB, ITS, dll)
    """
    out = dict(info or {})

    # type: ambil dari nama (lebih akurat daripada output model)
    inferred_type = infer_type_from_name(name)
    if inferred_type != "-":
        out["type"] = inferred_type
    else:
        # map jika ada
        t = _norm_token(out.get("type", "-"))
        out["type"] = TYPE_MAP.get(t, "-") if t not in UNKNOWN_MARKERS else "-"

    # status: override + mapping
    out["status"] = infer_status_from_signals(name, website, out.get("status", "-"), text_blob=text_blob)

    # final trim + empty => '-'
    for k,v in list(out.items()):
        if v is None or str(v).strip() == "":
            out[k] = "-"
        else:
            out[k] = str(v).strip()

    return out

def normalize_visi(d: Dict[str, Any]) -> Dict[str, str]:
    keys = ["visi","misi","sejarah_deskripsi"]
    out = {}
    for k in keys:
        v = d.get(k, "-")
        if v is None or str(v).strip() == "":
            v = "-"
        out[k] = str(v).strip()
    return out


# =========================
# Evidence Gate (ANTI HALU)
# =========================

RE_EMAIL = re.compile(r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})")
RE_PHONE = re.compile(r"(\+?\d[\d\-\s\(\)]{7,}\d)")
RE_WA_URL = re.compile(r"(wa\.me\/\d+|whatsapp\.com\/|api\.whatsapp\.com\/send\?phone=\d+)", re.I)
RE_POSTAL_CTX = re.compile(r"(?:kode\s*pos|postal\s*code|postcode|zip)\D{0,25}(\d{5})", re.I)
RE_POSTAL = re.compile(r"\b\d{5}\b")

def _sanitize_postal(u: str) -> str:
    u = (u or "").strip()
    if not u or u == "-":
        return "-"
    # keep only first 5-digit token
    m = RE_POSTAL.search(u)
    return m.group(0) if m else "-"

def _extract_postal_from_blob(blob: str) -> str:
    blob = blob or ""
    # 1) prefer explicit context (kode pos / postal code)
    m = RE_POSTAL_CTX.search(blob)
    if m:
        return m.group(1)
    # 2) fallback: first 5-digit token not near phone/fax labels
    for m2 in RE_POSTAL.finditer(blob):
        pc = m2.group(0)
        # context window
        left = blob[max(0, m2.start()-20):m2.start()].lower()
        right = blob[m2.end():min(len(blob), m2.end()+20)].lower()
        ctx = left + " " + right
        if any(k in ctx for k in ["tel", "telepon", "phone", "fax", "hp", "wa", "whatsapp"]):
            continue
        return pc
    return "-"

def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def _clean_phone(s: str) -> str:
    # keep + and digits
    s = (s or "").strip()
    s = re.sub(r"[^\d+]", "", s)
    return s

def _in_blob(value: str, blob: str) -> bool:
    if not value or value == "-":
        return False
    v = value.strip().lower()
    return v in (blob or "").lower()

def _any_domain_in_links(domains: List[str], links: List[str]) -> str:
    for u in links or []:
        ul = (u or "").lower()
        for d in domains:
            if d in ul:
                return u
    return ""

def _find_first_regex(pattern: re.Pattern, blob: str) -> str:
    m = pattern.search(blob or "")
    return m.group(0) if m else ""

def _sanitize_url(u: str) -> str:
    u = (u or "").strip()
    if not u or u == "-":
        return "-"
    # accept only http(s)
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return "-"

def _sanitize_email(u: str) -> str:
    u = (u or "").strip()
    if not u or u == "-":
        return "-"
    # allow mailto:xxx@yyy
    if u.lower().startswith("mailto:"):
        u = u.split(":", 1)[-1].strip()
    if RE_EMAIL.fullmatch(u):
        return u
    return "-"

def _sanitize_phone(u: str) -> str:
    u = _clean_phone(u)
    digits = _digits_only(u)
    # Indonesia & umum: 9-15 digit
    if 9 <= len(digits) <= 15:
        return u
    return "-"

def _sanitize_whatsapp(u: str) -> str:
    u = (u or "").strip()
    if not u or u == "-":
        return "-"
    # allow wa.me, whatsapp api, or a phone number
    if RE_WA_URL.search(u):
        return u
    # else treat as phone
    ph = _sanitize_phone(u)
    if ph != "-":
        return ph
    return "-"

def enforce_evidence_info(info: Dict[str, str], text: str, links: List[str]) -> Dict[str, str]:
    """
    Kalau model ngisi IG/WA/Phone/Email tapi tidak ada buktinya di text/links => set '-'.
    Postal code harus 5 digit, prefer yang ada konteks 'kode pos' / 'postal code'.
    """
    blob = (text or "") + "\n" + "\n".join(links or [])

    out = dict(info)

    # --- EMAIL: harus muncul di blob (atau bisa kita ambil langsung dari blob)
    email = _sanitize_email(out.get("email", "-"))
    if email != "-" and not _in_blob(email, blob):
        found = _find_first_regex(RE_EMAIL, blob)
        out["email"] = found if found else "-"
    else:
        out["email"] = email

    # --- PHONE: harus muncul di blob
    phone = _sanitize_phone(out.get("phone", "-"))
    if phone != "-" and not _in_blob(_digits_only(phone), _digits_only(blob)):
        raws = RE_PHONE.findall(blob or "")
        picked = "-"
        for r in raws:
            cand = _sanitize_phone(r)
            if cand != "-":
                picked = cand
                break
        out["phone"] = picked
    else:
        out["phone"] = phone

    # --- WHATSAPP: harus ada bukti whatsapp URL / kata WA di blob
    wa = _sanitize_whatsapp(out.get("whatsapp", "-"))
    b = (blob or "").lower()
    wa_has_evidence = bool(RE_WA_URL.search(blob or "")) or ("whatsapp" in b) or (" wa " in f" {b} ")
    if wa != "-" and not wa_has_evidence:
        out["whatsapp"] = "-"
    else:
        wa_url = _find_first_regex(RE_WA_URL, blob)
        out["whatsapp"] = wa_url if wa_url else wa

    # --- POSTAL CODE: harus 5 digit. Prefer yang ada konteks "kode pos" di blob.
    pc = _sanitize_postal(out.get("postal_code", "-"))
    # kalau model ngasih pc tapi tidak muncul di bukti → buang
    if pc != "-" and not _in_blob(pc, blob):
        pc = "-"
    # kalau kosong → cari dari blob
    if pc == "-":
        pc = _extract_postal_from_blob(blob)
    out["postal_code"] = pc

    # --- SOCIALS: wajib URL domain yang benar & muncul di links/blob
    social_domains = {
        "instagram": ["instagram.com"],
        "facebook": ["facebook.com", "fb.com"],
        "twitter": ["twitter.com", "x.com"],
        "youtube": ["youtube.com", "youtu.be"],
    }

    for k, domains in social_domains.items():
        val = _sanitize_url(out.get(k, "-"))

        # kalau model kasih URL tapi domain salah => drop
        if val != "-":
            vl = val.lower()
            if not any(d in vl for d in domains):
                val = "-"

        # evidence check: harus muncul di links/blob, kalau tidak -> cari dari links
        if val != "-" and not _in_blob(val, blob):
            val = "-"

        if val == "-":
            found = _any_domain_in_links(domains, links or [])
            out[k] = found if found else "-"
        else:
            out[k] = val

    # final trim empty
    for k in list(out.keys()):
        if out[k] is None or str(out[k]).strip() == "":
            out[k] = "-"
        out[k] = str(out[k]).strip()

    return out
