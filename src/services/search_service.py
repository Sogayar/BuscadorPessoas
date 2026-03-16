from src.core.search_router import search
from src.domain.identity import name_variants
from src.domain.dorks import build_queries
from src.utils.extract import extract_text


def search_person(name):

    variants = name_variants(name)

    queries = build_queries(variants)

    urls = []

    for q in queries:
        urls.extend(search(q))

    urls = list(set(urls))

    texts = []

    for url in urls:
        text = extract_text(url)
        if text:
            texts.append(url)

    return texts