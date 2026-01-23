# Buscador de Pessoas — Tkinter + Roteamento de Cotas

Ferramenta para facilitar a vida de auditores e advogados: dado um **nome** (e futuramente CPF e filtros),
o sistema busca resultados **orgânicos** e **notícias** em fontes públicas, respeitando **cotas** de provedores,
com **cache** e **fallback** gratuitos (RSS/GDELT). Interface em **Tkinter** e **CLI**.

> **Status:** MVP estável e organizado. Próximos passos: resolução de identidade (CPF), filtros por localidade e acurácia.

---

## Estrutura de pastas (proposta)
```
BUSCADORPESSOAS/
├─ config/
│  ├─ .env                 
│  └─ settings.json       
│
├─ data/                   
│  └─ search_quota.sqlite
│
├─ _old/                    
│
├─ src/
│  ├─ app/                 
│  │  ├─ buscador_quota.py       
│  │  └─ tk_buscador_quota.py    
│  ├─ core/                
│  │  └─ search_router.py
│  ├─ connectors/          # integrações futuras (jusbrasil, PJe, DJe…)
│  └─ utils/               # utilidades futuras (logging, etc.)
│
├─ .gitignore
├─ requirements.txt
└─ README.md
```


---

## Requisitos

- Python 3.11+
- Windows, macOS ou Linux
- Chaves de API 

Instale dependências:
```bash
pip install -r requirements.txt
```

---

## Configuração

Crie `config/.env` (opcional; necessário para provedores pagos):

```env
# Caminho do banco (opcional; default: data/search_quota.sqlite)
SEARCH_DB_PATH=data/search_quota.sqlite

# Limites (opcionais)
GOOGLE_DAILY_LIMIT=100
SERPSTACK_MONTHLY_LIMIT=100
ZENSERP_MONTHLY_LIMIT=50
SERPER_FINITE_LIMIT=2500
CACHE_TTL_SECONDS=604800       # 7 dias
ANTI_DUP_WINDOW_SECONDS=900    # 15 min

# Provedores pagos (preencha se for usar 'general' / busca orgânica paga)
GOOGLE_API_KEY=
GOOGLE_CX=
SERPSTACK_KEY=
ZENSERP_KEY=
SERPER_KEY=
```

Edite `config/settings.json` para opções da UI Tk:
```json
{
  "out_dir": "./outputs/textos",
  "n_top": 6,
  "include_news": true,
  "include_org": false,
  "build_index_csv": false
}
```

---

## Como rodar

### 1) Interface Tkinter (modo rápido, sem alterar imports)
> Enquanto os imports ainda são diretos (`from search_router import ...`), rode com `PYTHONPATH` apontando para `src/core`.

**Windows (PowerShell):**
```powershell
$env:PYTHONPATH="src/core"
python src/app/tk_buscador_quota.py
```

**Linux/macOS:**
```bash
PYTHONPATH=src/core python src/app/tk_buscador_quota.py
```

### 2) CLI
```bash
# Notícias gratuitas por pessoa
PYTHONPATH=src/core python src/app/buscador_quota.py --mode news --query "Felipe Neto" --max 6

# Busca paga (requer chaves no .env)
PYTHONPATH=src/core python src/app/buscador_quota.py --mode general --query "site:linkedin.com/in engenheiro civil São Paulo"
```

> **Dica:** Assim que trocarmos o import em `tk_buscador_quota.py` e `buscador_quota.py` para:
> ```python
> from core.search_router import init_db, QuotaAwareRouter
> ```
> você poderá rodar sem `PYTHONPATH` extra:
> ```bash
> python src/app/tk_app.py
> ```

---

## O que cada arquivo faz

- `src/core/search_router.py`: núcleo de busca, cache, quotas, provedores e fallback de notícias.
- `src/app/tk_buscador_quota.py`: UI em Tkinter (threads, salvamento de TXT e CSV-índice).
- `src/app/buscador_quota.py`: CLI para uso rápido/automação.
- `config/settings.json`: opções padrão da UI (pasta de saída, quantidades e flags).
- `config/.env`: chaves e limites (não versionar).
- `data/search_quota.sqlite`: banco gerado com cache e logs de cota (não versionar).

---

## Boas práticas e LGPD

- Esta ferramenta processa **dados públicos**. Deixe claro ao usuário final que os resultados são automatizados e **devem ser validados** antes de decisões.
- Registre origem e finalidade dos dados, tempo de retenção e ofereça canal de **oposição/retificação**.
- Evite processar dados sensíveis (saúde, religião, etc.).

---

## Solução de problemas

- **`ModuleNotFoundError: search_router`**  
  Use `PYTHONPATH=src/core` (ver seção “Como rodar”) ou ajuste o import para `from core.search_router import ...`.

- **`requests.exceptions.*`**  
  Verifique conexão e, no caso de provedores pagos, as chaves no `.env`.

- **Sem notícias**  
  O RSS do Google pode limitar frequências. O fallback (GDELT) entra automaticamente.

- **DB bloqueado**  
  Feche execuções simultâneas ou remova `data/search_quota.sqlite` (perderá cache/logs).

---

## Próximos passos (roadmap)

- Resolução de identidade (CPF, cidade/UF) e **score de acurácia**.
- Conectores específicos (Jusbrasil, Diários, PJe/e-SAJ).
- Exportação em PDF e painel web.
