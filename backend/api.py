"""
api.py
FastAPI — backend do LoL Stats.
Expõe endpoints REST chamados pelo frontend.

Rodar local:
    uvicorn api:app --reload --port 8000

Deploy Railway:
    Procfile: web: uvicorn api:app --host 0.0.0.0 --port $PORT
"""

import json
import os
import sys

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Scripts ficam na raiz (mesmo nível que api.py no backend/)
sys.path.insert(0, os.path.dirname(__file__))

import db
from player_fetch    import fetch_player
from player_analysis import analyze

# ══════════════════════════════════════════════
# Configuração de regiões
# ══════════════════════════════════════════════

REGION_CONFIG = {
    "br1":  {"routing": "americas", "label": "BR"},
    "na1":  {"routing": "americas", "label": "NA"},
    "euw1": {"routing": "europe",   "label": "EUW"},
    "eune1":{"routing": "europe",   "label": "EUNE"},
    "kr":   {"routing": "asia",     "label": "KR"},
    "jp1":  {"routing": "asia",     "label": "JP"},
    "lan1": {"routing": "americas", "label": "LAN"},
    "las1": {"routing": "americas", "label": "LAS"},
    "oc1":  {"routing": "sea",      "label": "OCE"},
    "tr1":  {"routing": "europe",   "label": "TR"},
    "ru":   {"routing": "europe",   "label": "RU"},
}

# Modos de jogo → queue IDs da Riot
QUEUE_MAP = {
    "ranked_solo": 420,
    "ranked_flex": 440,
    "normal":      400,   # Draft Pick
    "aram":        450,
    "all":         None,  # sem filtro
}

# ══════════════════════════════════════════════
# App
# ══════════════════════════════════════════════

app = FastAPI(
    title="LoL Stats API",
    version="1.0.0",
    description="Backend do LoL Stats — extrai e analisa partidas via Riot API.",
)

# CORS — permite o frontend (Vercel) chamar o backend (Railway)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Em produção: substitua por ["https://seu-site.vercel.app"]
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ══════════════════════════════════════════════
# Carrega ids_database.json uma vez na inicialização
# ══════════════════════════════════════════════

_IDS_DB: dict = {}

def get_ids_db() -> dict:
    global _IDS_DB
    if not _IDS_DB:
        db_path = os.path.join(os.path.dirname(__file__), "data", "ids_database.json")
        with open(db_path, encoding="utf-8") as f:
            _IDS_DB = json.load(f)
    return _IDS_DB


# ══════════════════════════════════════════════
# Supabase Vault — busca a Riot API Key em runtime
# ══════════════════════════════════════════════

_RIOT_KEY_CACHE: dict = {"key": None, "fetched_at": 0}

def get_riot_api_key() -> str:
    """
    Busca a Riot API Key do Supabase Vault.

    Para configurar no Supabase:
      1. Acesse seu projeto → Database → Vault
      2. Crie um secret com o nome: riot_api_key
      3. Cole sua chave RGAPI-... como valor

    A query abaixo usa a view decrypted_secrets (disponível no Supabase por padrão).
    """
    import time

    # Cache de 5 minutos pra não bater no banco a cada request
    if _RIOT_KEY_CACHE["key"] and (time.time() - _RIOT_KEY_CACHE["fetched_at"]) < 300:
        return _RIOT_KEY_CACHE["key"]

    sql = """
        SELECT decrypted_secret
        FROM vault.decrypted_secrets
        WHERE name = 'riot_api_key'
        LIMIT 1;
    """
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                if not row:
                    raise HTTPException(
                        status_code=500,
                        detail="Riot API Key não encontrada no Vault. Configure o secret 'riot_api_key' no Supabase Vault."
                    )
                key = row["decrypted_secret"]
                _RIOT_KEY_CACHE["key"] = key
                _RIOT_KEY_CACHE["fetched_at"] = time.time()
                return key
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar API Key do Vault: {e}")


# ══════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════

@app.get("/health")
def health():
    """Healthcheck — Railway usa isso pra saber se o serviço tá vivo."""
    return {"status": "ok"}


@app.get("/regions")
def list_regions():
    """Retorna as regiões suportadas."""
    return {"regions": [{"id": k, **v} for k, v in REGION_CONFIG.items()]}


@app.get("/search")
def search_player(
    name:    str = Query(..., description="Game name (ex: Faker)"),
    tag:     str = Query(..., description="Tag line (ex: BR1)"),
    region:  str = Query("br1", description="Região (ex: br1, na1, euw1)"),
    matches: int = Query(20,  ge=1, le=200, description="Qtd de partidas a buscar"),
    mode:    str = Query("ranked_solo", description="Modo: ranked_solo | ranked_flex | normal | aram | all"),
):
    """
    Busca e processa um player.
    1. Chama player_fetch para puxar partidas da Riot e salvar no Supabase.
    2. Chama player_analysis para calcular stats.
    3. Retorna tudo pro frontend.
    """
    if region not in REGION_CONFIG:
        raise HTTPException(status_code=400, detail=f"Região inválida. Use: {list(REGION_CONFIG.keys())}")

    if mode not in QUEUE_MAP:
        raise HTTPException(status_code=400, detail=f"Modo inválido. Use: {list(QUEUE_MAP.keys())}")

    # Pega a key do Vault e injeta no módulo player_fetch dinamicamente
    riot_key = get_riot_api_key()
    import player_fetch as pf
    pf.KEY          = riot_key
    pf.QUEUE_FILTER = QUEUE_MAP[mode]  # None = sem filtro de queue

    # Ajusta endpoints de acordo com a região
    region_cfg = REGION_CONFIG[region]
    pf.ROUTING_HOST = region_cfg["routing"]   # americas / europe / asia / sea
    pf.PLATFORM_HOST = region                  # br1 / na1 / euw1 …

    ids_db = get_ids_db()

    try:
        result = fetch_player(
            game_name  = name,
            tag_line   = tag,
            count      = matches,
            items_data = ids_db.get("items", {}),
            runes_data = ids_db.get("runes", {}),
            champ_map  = ids_db.get("champions", {}),
        )
    except Exception as e:
        err = str(e)
        if "404" in err or "não encontrado" in err.lower():
            raise HTTPException(status_code=404, detail=f"Player '{name}#{tag}' não encontrado na região {region.upper()}.")
        raise HTTPException(status_code=502, detail=f"Erro ao buscar dados da Riot API: {err}")

    # Roda análise (só recalcula se tiver partidas novas)
    puuid = result["player"]["puuid"]
    try:
        db_matches = db.get_matches(puuid, limit=matches)
        if db_matches and db.needs_analysis_update(puuid):
            analysis = analyze(db_matches)
            db.upsert_analysis(puuid, analysis, matches_analyzed=len(db_matches))
        else:
            analysis = db.get_analysis(puuid)
    except Exception as e:
        analysis = None  # análise não é crítica, não quebra o retorno

    return {
        "player":   result["player"],
        "mastery":  result["mastery"][:6],  # top 6
        "matches":  result["matches"][:matches],
        "analysis": analysis,
        "meta": {
            "region":      region,
            "mode":        mode,
            "requested":   matches,
            "total_in_db": result["total_matches"],
        }
    }


@app.get("/player/{puuid}/analysis")
def get_analysis(puuid: str):
    """Retorna apenas a análise salva de um player (sem rebuscar na Riot)."""
    analysis = db.get_analysis(puuid)
    if not analysis:
        raise HTTPException(status_code=404, detail="Análise não encontrada. Faça uma busca primeiro.")
    return analysis


@app.get("/player/{puuid}/matches")
def get_matches(
    puuid:   str,
    limit:   int = Query(20, ge=1, le=200),
    mode:    str = Query("all"),
):
    """Retorna partidas salvas de um player (sem rebuscar na Riot)."""
    matches = db.get_matches(puuid, limit=limit)
    if not matches:
        raise HTTPException(status_code=404, detail="Nenhuma partida encontrada.")
    return {"matches": matches, "total": len(matches)}