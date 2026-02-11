
# section_extractor.py

from typing import List, Tuple
from bs4 import BeautifulSoup

from config import JALUR_WORD_RE

def extract_candidate_sections(
    base_url: str,
    html: str,
) -> List[Tuple[str, str]]:
    """
    Return list of (url, context_hint) dari section-level
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    headings = soup.find_all(["h1", "h2", "h3", "h4"])

    for h in headings:
        title = h.get_text(" ", strip=True)
        section_text = [title]

        # ambil konten setelah heading
        for sib in h.find_next_siblings():
            if sib.name in ["h1", "h2", "h3", "h4"]:
                break
            text = sib.get_text(" ", strip=True)
            if text:
                section_text.append(text)

        blob = " ".join(section_text).lower()

        if (
            "jadwal" in blob
            or JALUR_WORD_RE.search(blob)
        ):
            results.append(
                (
                    base_url,
                    " ".join(section_text)[:500],
                )
            )

    return results
