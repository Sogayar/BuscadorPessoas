def pick_news_urls(payload: dict, n=3):
    out = []
    if isinstance(payload.get("news"), list):
        for it in payload["news"]:
            link = it.get("link") or it.get("url")
            if link: out.append(link)
    for k in ("news_results", "top_stories"):
        if isinstance(payload.get(k), list):
            for it in payload[k]:
                link = it.get("url") or it.get("link")
                if link: out.append(link)
    seen, dedup = set(), []
    for u in out:
        if u not in seen:
            seen.add(u); dedup.append(u)
    return dedup[:n]

def pick_organic_urls(payload: dict, n=3):
    out = []
    if isinstance(payload.get("items"), list):
        for it in payload["items"]:
            link = it.get("link")
            if link: out.append(link)
    if isinstance(payload.get("organic"), list):
        for it in payload["organic"]:
            link = it.get("link") or it.get("url")
            if link: out.append(link)
    if isinstance(payload.get("organic_results"), list):
        for it in payload["organic_results"]:
            link = it.get("url") or it.get("link")
            if link: out.append(link)
    seen, dedup = set(), []
    for u in out:
        if u not in seen:
            seen.add(u); dedup.append(u)
    return dedup[:n]