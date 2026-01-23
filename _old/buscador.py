import os, sys, time, json, textwrap, re
from datetime import datetime
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup
import trafilatura
from dotenv import load_dotenv

load_dotenv()

SERPER_KEY = os.getenv("SERPER_KEY")
SEARCH_ENDPOINT = "https://google.serper.dev/search"
HEADERS = {
    "X-API-KEY": SERPER_KEY or "",
    "Content-Type": "application/json",
}

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

def serper_search(query: str):
    """Retorna dicionário com resultados 'news' e 'organic' do Serper.dev."""
    if not SERPER_KEY:
        raise RuntimeError("Defina SERPER_KEY no .env")
    payload = {
        "q": query,
        "num": 10,             # pegamos 10 e depois filtramos os top 3
        "gl": "br",
        "hl": "pt-BR",
    }
    resp = requests.post(SEARCH_ENDPOINT, headers=HEADERS, data=json.dumps(payload), timeout=30)
    resp.raise_for_status()
    return resp.json()

def clean_text(txt: str) -> str:
    if not txt:
        return ""
    # Remove múltiplos espaços/linhas, scripts residuais, etc.
    txt = re.sub(r"\s+\n", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    return txt.strip()

def extract_main_text(url: str) -> str:
    """Tenta extrair o conteúdo principal com trafilatura; se falhar, usa BeautifulSoup."""
    try:
        downloaded = trafilatura.fetch_url(url, no_ssl=True)
        if downloaded:
            extracted = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
                favor_recall=True,  # puxa mais texto quando incerto
            )
            if extracted and len(extracted) > 400:
                return clean_text(extracted)
    except Exception:
        pass  # cai para o fallback

    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # Remove elementos não textuais
        for bad in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
            bad.decompose()
        text = soup.get_text("\n", strip=True)
        # corta lixo extremo
        # se a página for gigante, limitamos a ~50k chars
        if len(text) > 50000:
            text = text[:50000]
        return clean_text(text)
    except Exception:
        return ""

def top_n_urls(results: dict, n=3):
    # Pega top 3 de news e top 3 de orgânico, preservando ordem e deduplicando
    urls_news = []
    if "news" in results and isinstance(results["news"], list):
        for item in results["news"]:
            if "link" in item:
                urls_news.append(item["link"])
    urls_org = []
    if "organic" in results and isinstance(results["organic"], list):
        for item in results["organic"]:
            if "link" in item:
                urls_org.append(item["link"])
    # Dedup mantendo ordem
    def dedup(seq):
        seen = set()
        out = []
        for u in seq:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out
    return dedup(urls_news)[:n], dedup(urls_org)[:n]

def build_output(query: str, news_urls, org_urls):
    blocks = []
    blocks.append(f"# Buscador rápido — consulta: {query}\n")
    if news_urls:
        blocks.append("## Notícias (top 3)\n")
        for i, u in enumerate(news_urls, 1):
            blocks.append(f"### Notícia {i}\nURL: {u}\n\n" + extract_main_text(u) + "\n")
            time.sleep(0.5)  # evita estourar rate limits
    if org_urls:
        blocks.append("## Resultados orgânicos (top 3)\n")
        for i, u in enumerate(org_urls, 1):
            blocks.append(f"### Link {i}\nURL: {u}\n\n" + extract_main_text(u) + "\n")
            time.sleep(0.5)
    # Texto concatenado final
    concatenado = []
    concatenado.append(f"# Texto concatenado — {query}\n")
    for u in news_urls + org_urls:
        txt = extract_main_text(u)
        if txt:
            concatenado.append(txt)
    full_concat = "\n\n".join(concatenado).strip()
    return "\n".join(blocks).strip(), full_concat

def main():
    if len(sys.argv) < 2:
        print("Uso: python buscador.py \"Getúlio Vargas\"")
        sys.exit(1)
    query = " ".join(sys.argv[1:]).strip()
    results = serper_search(query)
    news_urls, org_urls = top_n_urls(results, n=3)
    header_text, concatenated = build_output(query, news_urls, org_urls)
    print(header_text)
    print("\n" + "="*80 + "\n")
    print(concatenated)

    # salva em arquivo
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", query)[:60]
    out_path = f"busca_{base}_{stamp}.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header_text)
        f.write("\n\n" + "="*80 + "\n\n")
        f.write(concatenated)
    print(f"\n[OK] Resultado salvo em: {out_path}")

if __name__ == "__main__":
    main()
