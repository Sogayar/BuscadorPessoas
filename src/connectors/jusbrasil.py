# src/connectors/jusbrasil.py
import requests, re
from bs4 import BeautifulSoup

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/122.0.0.0 Safari/537.36")}

def search_urls(query: str, max_n: int = 5) -> list[str]:
    """
    Busca simples no Jusbrasil (público). É um 'conector' bem básico:
    monta a URL de busca e faz um parse do HTML para extrair links.
    """
    q = requests.utils.quote(query)
    url = f"https://www.jusbrasil.com.br/busca?q={q}"
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    out = []
    for a in soup.select("a[href]"):
        href = a["href"]
        if href.startswith("http") and "jusbrasil.com.br" in href:
            out.append(href)
        if len(out) >= max_n:
            break

    # dedup simples
    seen, dedup = set(), []
    for u in out:
        if u not in seen:
            seen.add(u); dedup.append(u)
    return dedup[:max_n]

def fetch_page(url: str) -> str:
    """
    Faz o download da página HTML e retorna o conteúdo como texto.
    """
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text