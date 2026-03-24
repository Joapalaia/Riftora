<div align="center">

# Riftora — LoL Stats

**Veja as estatísticas do seu Invocador direto no navegador.**

<img width="919" height="651" alt="image" src="https://github.com/user-attachments/assets/3cee6a8b-f475-4362-929f-e40cb6065662" />

<img width="917" height="729" alt="image" src="https://github.com/user-attachments/assets/6998c872-cad1-474a-a3f6-e6b692365f2e" />


</div>

---

## 📖 Sobre o projeto

O **Riftora** é uma aplicação web que permite buscar e visualizar estatísticas de jogadores de *League of Legends* utilizando a API oficial da Riot Games. Basta informar o nome do invocador para visualizar seu histórico de partidas, campeões, maestrias, runas e muito mais.

> ⚡ O backend é hospedado no Render com plano gratuito — a primeira requisição pode levar até 60 segundos para o servidor acordar. As buscas seguintes são muito mais rápidas, com dados em cache.

---

## ✨ Funcionalidades

- 🔍 Busca por nome de invocador (com suporte a Riot ID)
- 📊 Histórico de partidas detalhado
- 🏆 Maestrias de campeões
- 🧩 Runas e itens por partida
- 💾 Cache de dados para buscas mais rápidas após a primeira consulta
- 🌐 Interface disponível em **Português** e **Inglês**

---

## 🛠️ Tecnologias

### Frontend
| Tecnologia | Uso |
|---|---|
| HTML / CSS / JavaScript | Interface estática |
| Vercel | Deploy e hospedagem |

### Backend
| Tecnologia | Uso |
|---|---|
| Python | Linguagem principal |
| Flask (`api.py`) | API REST |
| Riot Games API | Dados dos jogadores |
| JSON (`ids_database.json`) | Cache local de dados |
| Render | Deploy e hospedagem |

---

## 📁 Estrutura do projeto

```
riftora/
├── backend/
│   ├── data/
│   │   └── ids_database.json     # Cache de IDs de itens/bonecos/runas, extrai do DDragon conforme a versão mais recente
│   ├── api.py                    # Endpoints da API Flask
│   ├── database_extract.py       # Extração dos IDs dos itens/bonecos/runas
│   ├── db.py                     # Gerenciamento do banco de dados
│   ├── player_analysis.py        # Análise de estatísticas
│   ├── player_fetch.py           # Integração com a Riot API
│   ├── Procfile                  # Configuração de processo (Render)
│   ├── railway.toml              # Configuração do deploy (legado)
│   ├── requirements.txt          # Dependências Python
│   └── runtime.txt               # Versão do Python
│
└── frontend/
    ├── assets/
    │   ├── champions/            # Imagens dos campeões
    │   ├── items/                # Ícones de itens
    │   └── runes/                # Ícones de runas
    ├── index.html                # Página inicial
    ├── search.html               # Página de busca
    ├── stats.html                # Estatísticas do jogador
    ├── masteries.html            # Maestrias
    ├── menu.html                 # Menu de navegação
    ├── wrapped.html              # Resumo do jogador (tipo o spotify wrapped)
    └── vercel.json               # Configuração do deploy
```

</div>
