from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

KEYWORDS = [
    "pmb",
    "jalur",
    "daftar",
    "pendaftaran",
    "admission",
    "snbp",
    "snbt",
    "mandiri"
]

def extract_candidate_links(html, base_url, same_domain=True, max_links=10):

    soup = BeautifulSoup(html, "html.parser")

    links = []
    base_domain = urlparse(base_url).netloc

    for a in soup.find_all("a", href=True):

        href = a["href"].strip()
        url = urljoin(base_url, href)

        parsed = urlparse(url)

        if same_domain and parsed.netloc != base_domain:
            continue

        text = (a.get_text() or "").lower()
        href_l = href.lower()

        if any(k in text or k in href_l for k in KEYWORDS):
            links.append(url)

        if len(links) >= max_links:
            break

    return list(set(links))