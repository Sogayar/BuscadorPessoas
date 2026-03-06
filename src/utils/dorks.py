from typing import Optional, Dict, List


def _build_context(profile: Optional[Dict]) -> str:
    """
    Monta o contexto adicional para reduzir homônimos.
    """

    if not profile:
        return ""

    ctx = []

    city = profile.get("city")
    uf = profile.get("uf")
    role = profile.get("role")
    party = profile.get("party")

    if city:
        ctx.append(f'"{city}"')

    if uf:
        ctx.append(f'"{uf}"')

    if role:
        ctx.append(f'"{role}"')

    if party:
        ctx.append(f'"{party}"')

    return " ".join(ctx)


def _build_aliases(profile: Optional[Dict]) -> str:
    """
    Inclui apelidos do investigado.
    """

    if not profile:
        return ""

    akas = profile.get("akas")

    if not akas:
        return ""

    parts = []

    for a in akas.split(","):
        a = a.strip()
        if a:
            parts.append(f'"{a}"')

    return " OR ".join(parts)


def get_dorks(
    name: str,
    profile: Optional[Dict] = None,
    strategy: str = "hybrid"
) -> List[Dict]:

    base = f'"{name}"'
    context = _build_context(profile)
    alias_query = _build_aliases(profile)

    person_query = base

    if strategy == "precision":
        person_query = f'{base} {context}'.strip()

    elif strategy == "hybrid":
        if alias_query:
            person_query = f'({base} OR {alias_query}) {context}'.strip()
        else:
            person_query = f'{base} {context}'.strip()

    elif strategy == "wide":
        if alias_query:
            person_query = f'({base} OR {alias_query})'.strip()

    return [

        {
            "category": "risk",
            "query": f'{person_query} (corrupção OR fraude OR investigação OR operação OR escândalo OR denúncia OR crime OR lavagem de dinheiro)'
        },

        {
            "category": "legal",
            "query": f'{person_query} (processo OR ação judicial OR réu OR acusado OR condenação OR tribunal OR justiça OR inquérito)'
        },

        {
            "category": "social",
            "query": f'{person_query} (polêmica OR controvérsia OR acusação OR crítica OR indignação OR repercussão OR debate)'
        },

        {
            "category": "news_major",
            "query": f'intitle:{base} {context} (site:g1.globo.com OR site:bbc.com OR site:folha.uol.com.br OR site:estadao.com.br OR site:metropoles.com)'
        },

        {
            "category": "official",
            "query": f'{person_query} (site:gov.br OR site:stf.jus.br OR site:tse.jus.br OR site:senado.leg.br OR site:camara.leg.br)'
        }
    ]