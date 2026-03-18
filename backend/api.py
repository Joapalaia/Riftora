"""
api.py — FastAPI backend do Riftora.

Arquitetura de 2 passos para evitar timeout do Render (30s):
  1. GET /search   → resolve PUUID, inicia job em background, retorna dados do banco imediatamente
  2. GET /status/  → frontend faz polling até status=done, recebe dados completos
"""

import json
import os
import sys
import threading

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, os.path.dirname(__file__))

import db
from player_fetch    import fetch_player
from player_analysis import analyze

# ── Regiões ──────────────────────────────────────────────────────────────────
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

QUEUE_MAP = {
    "ranked_solo": 420,
    "ranked_flex": 440,
    "normal":      400,
    "aram":        450,
    "all":         None,
}

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Riftora API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Job state ─────────────────────────────────────────────────────────────────
# Guarda jobs em memória. Chave = "{puuid}:{mode}:{matches}"
_jobs: dict = {}
_jobs_lock = threading.Lock()

# ── Helpers ───────────────────────────────────────────────────────────────────
_IDS_DB: dict = {}

def get_ids_db() -> dict:
    global _IDS_DB
    if not _IDS_DB:
        path = os.path.join(os.path.dirname(__file__), "data", "ids_database.json")
        with open(path, encoding="utf-8") as f:
            _IDS_DB = json.load(f)
    return _IDS_DB

def get_riot_key() -> str:
    key = os.environ.get("RIOT_API_KEY")
    if not key:
        raise HTTPException(status_code=500, detail="RIOT_API_KEY não configurada.")
    return key

def configure_pf(mode: str, region: str, riot_key: str):
    import player_fetch as pf
    pf.KEY           = riot_key
    pf.QUEUE_FILTER  = QUEUE_MAP[mode]
    pf.ROUTING_HOST  = REGION_CONFIG[region]["routing"]
    pf.PLATFORM_HOST = region
    if mode != "ranked_solo":
        pf.CACHE_MAX_AGE_H = 0
    return pf

# ── Background worker ─────────────────────────────────────────────────────────
def run_fetch_job(jkey: str, name: str, tag: str, region: str, mode: str, matches: int, riot_key: str):
    """Roda em thread daemon — nunca bloqueia o Render."""
    try:
        pf = configure_pf(mode, region, riot_key)
        ids_db = get_ids_db()

        result = fetch_player(
            game_name  = name,
            tag_line   = tag,
            count      = matches,
            items_data = ids_db.get("items", {}),
            runes_data = ids_db.get("runes", {}),
            champ_map  = ids_db.get("champions", {}),
        )

        puuid = result["player"]["puuid"]
        try:
            db_matches = db.get_matches(puuid, limit=matches)
            if db_matches:
                analysis = analyze(db_matches)
                db.upsert_analysis(puuid, analysis, matches_analyzed=len(db_matches))
            else:
                analysis = db.get_analysis(puuid)
        except Exception:
            analysis = db.get_analysis(puuid)

        with _jobs_lock:
            _jobs[jkey] = {
                "status":   "done",
                "result":   result,
                "analysis": analysis,
                "matches":  matches,
                "region":   region,
                "mode":     mode,
            }
        print(f"[job] {jkey} → done")

    except Exception as e:
        print(f"[job] {jkey} → error: {e}")
        with _jobs_lock:
            _jobs[jkey] = {"status": "error", "error": str(e)}

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/regions")
def list_regions():
    return {"regions": [{"id": k, **v} for k, v in REGION_CONFIG.items()]}


@app.get("/search")
def search_player(
    name:    str = Query(...),
    tag:     str = Query(...),
    region:  str = Query("br1"),
    matches: int = Query(20, ge=1, le=200),
    mode:    str = Query("ranked_solo"),
):
    """
    Passo 1 — responde em <3s:
    - Resolve o PUUID via Riot API (rápido)
    - Se já houver job rodando para este player, não duplica
    - Inicia fetch em background thread
    - Retorna o que já existe no banco (pode estar incompleto)
    - Retorna job_key para o frontend fazer polling
    """
    if region not in REGION_CONFIG:
        raise HTTPException(status_code=400, detail="Região inválida.")
    if mode not in QUEUE_MAP:
        raise HTTPException(status_code=400, detail="Modo inválido.")

    riot_key = get_riot_key()
    pf = configure_pf(mode, region, riot_key)

    # Resolve PUUID — única chamada rápida à Riot API aqui
    try:
        puuid = pf.get_puuid(name, tag)
        game_name, tag_line = name, tag
    except Exception as e:
        err = str(e)
        if "404" in err:
            raise HTTPException(status_code=404, detail=f"Player '{name}#{tag}' não encontrado na região {region.upper()}.")
        raise HTTPException(status_code=502, detail=f"Erro Riot API: {err}")

    jkey = f"{puuid}:{mode}:{matches}"

    with _jobs_lock:
        current_job = _jobs.get(jkey)
        already_running = current_job and current_job["status"] == "running"

    if not already_running:
        with _jobs_lock:
            _jobs[jkey] = {"status": "running"}
        thread = threading.Thread(
            target=run_fetch_job,
            args=(jkey, name, tag, region, mode, matches, riot_key),
            daemon=True,
        )
        thread.start()
        print(f"[job] {jkey} → started")
    else:
        print(f"[job] {jkey} → already running, skipping duplicate")

    # Retorna dados do banco imediatamente
    player_db   = db.get_player(puuid) or {
        "game_name": game_name, "tag_line": tag_line,
        "puuid": puuid, "summoner_level": None,
        "profile_icon_id": None, "ranked": {}
    }
    mastery_db  = db.get_mastery(puuid) or []
    matches_db  = db.get_matches(puuid, limit=matches) or []
    analysis_db = db.get_analysis(puuid)

    return {
        "puuid":    puuid,
        "job_key":  jkey,
        "status":   "running",
        "player":   player_db,
        "mastery":  mastery_db,
        "matches":  matches_db,
        "analysis": analysis_db,
        "meta": {
            "region":      region,
            "mode":        mode,
            "requested":   matches,
            "total_in_db": len(matches_db),
        }
    }


@app.get("/status/{job_key:path}")
def job_status(
    job_key: str,
    matches: int = Query(20),
    region:  str = Query("br1"),
    mode:    str = Query("all"),
):
    """
    Passo 2 — frontend chama isso a cada 3s até status=done.
    """
    with _jobs_lock:
        job = dict(_jobs.get(job_key) or {})

    if not job:
        return {"status": "unknown"}

    if job["status"] == "running":
        return {"status": "running"}

    if job["status"] == "error":
        return {"status": "error", "error": job.get("error", "Erro desconhecido")}

    # done
    result   = job["result"]
    analysis = job["analysis"]
    m        = job.get("matches", matches)
    r        = job.get("region", region)
    md       = job.get("mode", mode)

    return {
        "status":   "done",
        "player":   result["player"],
        "mastery":  result["mastery"],
        "matches":  result["matches"][:m],
        "analysis": analysis,
        "meta": {
            "region":      r,
            "mode":        md,
            "requested":   m,
            "total_in_db": result.get("total_matches", 0),
        }
    }


@app.get("/player/{puuid}/analysis")
def get_analysis(puuid: str):
    analysis = db.get_analysis(puuid)
    if not analysis:
        raise HTTPException(status_code=404, detail="Análise não encontrada.")
    return analysis


@app.get("/player/{puuid}/matches")
def get_matches(puuid: str, limit: int = Query(20, ge=1, le=200)):
    matches = db.get_matches(puuid, limit=limit)
    if not matches:
        raise HTTPException(status_code=404, detail="Nenhuma partida encontrada.")
    return {"matches": matches, "total": len(matches)}