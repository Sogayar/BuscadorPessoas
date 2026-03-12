import re
from urllib.parse import urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

BLOCKED_DOMAINS = {
    "linktr.ee", "beacons.ai", "bit.ly", "tinyurl.com",
}


def safe_filename(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
    return base or "resultado"


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def is_low_signal_url(url: str) -> bool:
    try:
        host = (urlparse(url).netloc or "").lower()
        if host in BLOCKED_DOMAINS:
            return True
        if host.endswith("instagram.com") and "/p/" in url:
            return True
        return False
    except Exception:
        return True


def extract_main_text(url: str, limit_chars: int = 15000) -> str:
    if is_low_signal_url(url):
        return ""

    try:
        downloaded = trafilatura.fetch_url(url, no_ssl=True)
        if downloaded:
            extracted = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
                output_format="txt",
            )
            if extracted and len(extracted) > 300:
                return clean_text(extracted[:limit_chars])
    except Exception:
        pass

    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for bad in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
            bad.decompose()
        text = soup.get_text("\n", strip=True)
        return clean_text(text[:limit_chars])
    except Exception:
        return ""