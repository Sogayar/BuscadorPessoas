import os
import sys
import json
import argparse
from dotenv import load_dotenv

from src.core.search_router import (
    init_db,
    QuotaAwareRouter,
)

load_dotenv()


def print_news(response_json: dict, person: str):
    news = (response_json or {}).get("news", [])
    print(f'# Notícias relevantes — pessoa: {person}\n')
    if not news:
        print("Nenhuma notícia relevante encontrada (filtro por pessoa aplicado).")
        return
    for i, n in enumerate(news, 1):
        title = n.get("title", "").strip()
        link = n.get("link", "").strip()
        source = n.get("source", "").strip()
        pubd = n.get("pubDate", "").strip()
        print(f"### Notícia {i}")
        print(f"Título: {title}")
        if source:
            print(f"Fonte: {source}")
        if pubd:
            print(f"Data:  {pubd}")
        print(f"URL:   {link}")
        print("")


def print_general(provider_key: str, payload: dict, raw: bool = False):
    print(f"# Resultado de busca geral — via {provider_key}\n")
    if raw:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    # Mostra só chaves de topo, para não poluir
    keys = list((payload or {}).keys())
    print(f"Keys do payload: {keys[:10]}")
    # Caso específico do Google Custom Search (itens)
    items = (payload or {}).get("items") or []
    if items:
        print("\n## Itens (até 10)")
        for i, it in enumerate(items[:10], 1):
            title = (it.get("title") or "").strip()
            link = (it.get("link") or "").strip()
            snippet = (it.get("snippet") or "").strip()
            print(f"{i}. {title}\n   {link}\n   {snippet}\n")


def main():
    parser = argparse.ArgumentParser(description="Buscador com roteamento de cotas e notícias gratuitas por pessoa.")
    parser.add_argument("--mode", choices=["general", "news"], default="general",
                        help="general = busca paga; news = Somente Notícias (pessoa) gratuita")
    parser.add_argument("--query", required=True, help="Texto de busca (no modo news, pode ser usado como nome da pessoa se --person não for fornecido)")
    parser.add_argument("--person", default=None, help="Nome da pessoa (usado no modo news). Se ausente, usa --query.")
    parser.add_argument("--max", type=int, default=10, help="Máximo de itens (aplicável ao modo news)")
    parser.add_argument("--raw", action="store_true", help="Imprimir JSON bruto do provider (modo general)")

    args = parser.parse_args()

    init_db()
    router = QuotaAwareRouter()

    if args.mode == "news":
        person = (args.person or args.query).strip()
        out = router.search_news_free(person=person, user_id="cli", max_n=args.max)
        provider = out.get("provider", "news")
        resp = out.get("response", {})
        print(f"# Buscador — Somente Notícias (pessoa)\nProvider: {provider}\n")
        print_news(resp, person)
        return

    # Modo general (pago)
    out = router.search(args.query, user_id="cli")
    provider = out.get("provider", "unknown")
    payload = out.get("response", {})
    print_general(provider, payload, raw=args.raw)

    if args.mode == "news":
        person = (args.person or args.query).strip()
        out = router.search_news_free(person=person, user_id="cli", max_n=args.max)

        # === DEBUG INÍCIO ===
        print("# DEBUG raw response")
        import json
        print(json.dumps(out, indent=2, ensure_ascii=False))
        # === DEBUG FIM ===

        provider = out.get("provider", "news")
        resp = out.get("response", {})
        print(f"# Buscador — Somente Notícias (pessoa)\nProvider: {provider}\n")
        print_news(resp, person)
        return



if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)

