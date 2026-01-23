import re, requests, trafilatura
from bs4 import BeautifulSoup

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36")
}

def clean_text(txt: str) -> str:
    if not txt:
        return ""
    txt = re.sub(r"\s+\n", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    return txt.strip()

def extract_main_text(url: str) -> str:
    try:
        downloaded = trafilatura.fetch_url(url, no_ssl=True)
        if downloaded:
            extracted = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
                favor_recall=True,
            )
            if extracted and len(extracted) > 400:
                return clean_text(extracted)
    except Exception:
        pass
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for bad in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
            bad.decompose()
        text = soup.get_text("\n", strip=True)
        if len(text) > 50000:
            text = text[:50000]
        return clean_text(text)
    except Exception:
        return ""

def safe_filename(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
    return base or "resultado"
