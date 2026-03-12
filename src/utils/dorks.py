from dataclasses import dataclass
from typing import Dict, List, Sequence

from src.utils.identity import IdentityProfile


@dataclass(frozen=True)
class ResearchReason:
    key: str
    label: str
    keywords: Sequence[str]
    domains: Sequence[str] = ()
    use_news: bool = True


REASONS: Dict[str, ResearchReason] = {
    "condenacoes": ResearchReason(
        key="condenacoes",
        label="Condenações e decisões",
        keywords=("condenação", "condenado", "sentença", "acórdão", "réu", "tribunal", "justiça", "improbidade"),
        domains=("stf.jus.br", "stj.jus.br", "tjsp.jus.br", "trf1.jus.br", "jusbrasil.com.br", "cnj.jus.br"),
    ),
    "fraudes": ResearchReason(
        key="fraudes",
        label="Fraudes e esquemas",
        keywords=("fraude", "desvio", "superfaturamento", "laranja", "empresa de fachada", "cartel", "lavagem de dinheiro"),
        domains=("gov.br", "tcu.gov.br", "mpf.mp.br", "pf.gov.br", "mpt.mp.br", "jusbrasil.com.br"),
    ),
    "investigacoes": ResearchReason(
        key="investigacoes",
        label="Investigações e operações",
        keywords=("investigação", "inquérito", "operação", "denúncia", "alvo", "mandado", "busca e apreensão"),
        domains=("gov.br", "pf.gov.br", "mpf.mp.br", "cnj.jus.br", "migalhas.com.br"),
    ),
    "licitacoes": ResearchReason(
        key="licitacoes",
        label="Licitações e contratos públicos",
        keywords=("licitação", "pregão", "contrato", "aditivo", "dispensa", "inexigibilidade", "portal da transparência"),
        domains=("gov.br", "tcu.gov.br", "compras.gov.br", "transparencia.gov.br", "pncp.gov.br"),
    ),
    "midia_social": ResearchReason(
        key="midia_social",
        label="Presença pública e redes sociais",
        keywords=("perfil", "biografia", "empresa", "sócio", "cargo"),
        domains=("linkedin.com", "facebook.com", "instagram.com", "x.com", "youtube.com"),
        use_news=False,
    ),
}


def _quoted(value: str) -> str:
    value = " ".join((value or "").split()).strip()
    return f'"{value}"' if value else ""


def _identity_constraints(profile: IdentityProfile) -> str:
    extra = []
    for value in [profile.city, profile.state, profile.party, profile.role, profile.organization, profile.company]:
        if value:
            extra.append(_quoted(value))
    if profile.cpf:
        extra.append(_quoted(profile.cpf))
    return " ".join(extra)


def get_reasons() -> List[Dict[str, str]]:
    return [{"key": r.key, "label": r.label} for r in REASONS.values()]


def build_dorks(profile: IdentityProfile, reason_key: str, include_social: bool = True) -> List[Dict[str, str]]:
    reason = REASONS[reason_key]
    base_name = _quoted(profile.full_name)
    aliases = [a for a in profile.aliases if a.strip()]
    identity = _identity_constraints(profile)
    or_keywords = " OR ".join(reason.keywords)
    dorks: List[Dict[str, str]] = []

    dorks.append({
        "category": reason.key,
        "query": f"{base_name} ({or_keywords}) {identity}".strip(),
        "kind": "broad"
    })

    if reason.domains:
        domains_block = " OR ".join(f"site:{d}" for d in reason.domains)
        dorks.append({
            "category": f"{reason.key}_dominios",
            "query": f"{base_name} ({or_keywords}) ({domains_block}) {identity}".strip(),
            "kind": "official_or_targeted"
        })

    if aliases:
        alias_block = " OR ".join(_quoted(a) for a in aliases)
        dorks.append({
            "category": f"{reason.key}_alias",
            "query": f"({base_name} OR {alias_block}) ({or_keywords}) {identity}".strip(),
            "kind": "alias"
        })

    if include_social:
        dorks.extend(build_social_dorks(profile))

    return dorks


def build_social_dorks(profile: IdentityProfile) -> List[Dict[str, str]]:
    base_name = _quoted(profile.full_name)
    identity = _identity_constraints(profile)
    social_sites = [
        "site:linkedin.com/in",
        "site:linkedin.com/company",
        "site:facebook.com",
        "site:instagram.com",
        "site:x.com",
        "site:youtube.com",
    ]
    return [{
        "category": "midia_social",
        "query": f"{base_name} ({' OR '.join(social_sites)}) {identity}".strip(),
        "kind": "social"
    }]