# def build_risk_dork(name: str) -> str:
#     return (
#         f'"{name}" '
#         '(corrupção OR investigação OR escândalo OR fraude OR denúncia '
#         'OR operação OR crime OR processo OR lavagem de dinheiro)'
#     )

# def build_legal_dork(name: str) -> str:
#     return f'"{name}" (processo OR ação OR réu OR acusado OR tribunal OR justiça)'

# def build_social_dork(name: str) -> str:
#     return (
#         f'"{name}" '
#         '(polêmica OR escândalo OR acusação OR irregularidades OR controvérsia '
#         'OR indignação OR revolta OR crítica OR opinião OR debate OR repercussão)'
#     )

# def build_news_dork(name: str) -> str:
#     return (
#         f'intitle:"{name}" '
#         '(site:g1.globo.com OR site:bbc.com OR site:metropoles.com '
#         'OR site:folha.uol.com.br)'
#     )

# def build_oficial_dork(name: str) -> str:
#     return (
#         f'"{name}" '
#         '(site:gov.br OR site:brasil.io OR site:transparencia.gov.br'
#         'OR site:portaltransparencia.gov.br OR site:receita.fazenda.gov.br '
#         'OR site:senado.leg.br OR site:camara.leg.br OR site:stf.jus.br OR site:tse.jus.br)'
#     )

def get_dorks(name: str) -> list[dict]:
    base = f'"{name}"'

    return [

        {
            "category": "risk",
            "query": f'{base} (corrupção OR fraude OR investigação OR operação OR escândalo OR denúncia OR crime OR lavagem de dinheiro)'
        },

        {
            "category": "legal",
            "query": f'{base} (processo OR ação judicial OR réu OR acusado OR condenação OR tribunal OR justiça OR inquérito)'
        },

        {
            "category": "social",
            "query": f'{base} (polêmica OR controvérsia OR acusação OR crítica OR indignação OR repercussão OR debate)'
        },

        {
            "category": "news_major",
            "query": f'intitle:{base} (site:g1.globo.com OR site:bbc.com OR site:folha.uol.com.br OR site:estadao.com.br OR site:metropoles.com)'
        },

        {
            "category": "official",
            "query": f'{base} (site:gov.br OR site:stf.jus.br OR site:tse.jus.br OR site:senado.leg.br OR site:camara.leg.br)'
        }
    ]