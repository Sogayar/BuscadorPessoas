# Buscador de Pessoas — Tkinter + Roteamento de Cotas

Ferramenta desenvolvida para **auditores, advogados e analistas**, destinada à **coleta automatizada de informações públicas sobre pessoas**.

Dado um **nome** (e opcionalmente filtros como CPF, cidade ou UF), o sistema:

- realiza **buscas orgânicas na web**
- coleta **notícias relacionadas**
- extrai automaticamente o **conteúdo das páginas**
- gera **relatórios TXT e PDF**

O sistema utiliza **múltiplos provedores de busca**, com:

- controle automático de **cotas**
- **cache de consultas**
- **fallback gratuito para notícias**
- **extração automática de conteúdo**

Interfaces disponíveis:

- **Tkinter (GUI)**
- **CLI (linha de comando)**

---

# Status do Projeto

> **Versão:** MVP funcional  
> **Estágio:** Estável para uso exploratório

Roadmap imediato:

- resolução de identidade (CPF / cidade / UF)
- melhoria de acurácia de notícias
- conectores jurídicos (Jusbrasil, tribunais)
- painel web

---

# Estrutura do Projeto
```
├── config/
│   ├── .env
│   └── settings.json
│
├── data/
│   └── search_quota.sqlite
│
├── src/
│ ├── app/
│ │    ├── buscador_quota.py
│ │    └── tk_buscador_quota.py
│ │
│ ├── core/
│ │    └── search_router.py
│ │
│ ├── connectors/
│ │    └── jusbrasil.py
│ │
│ ├── utils/
│ │   ├── extract.py
│ │   ├── pickers.py
│ │   ├── identity.py
│ │   ├── exporters.py
│ │   └── settings.py
│ │
│ └── worker/
│      └── worker.py
│
├── outputs/
│
├── requirements.txt
└── README.md
```	
