"""
player_fetch.py
Puxa as últimas N partidas Ranked Solo/Duo de um player e salva em JSON.

Uso:
    python scripts/player_fetch.py --name "NickDoJogador" --tag "BR1"
    python scripts/player_fetch.py --name "NickDoJogador" --tag "BR1" --count 200
    python scripts/player_fetch.py --name "NickDoJogador" --tag "BR1" --count 50 --output data/profiles/meu_perfil.json
"""

import argparse
import json
import os
import time
from collections import deque
from datetime import datetime

import requests
import db

# ══════════════════════════════════════════════
KEY               = "RGAPI-5e514d53-e069-40ea-9177-f962370a46ac"
ROUTING_HOST  = "americas"  # sobrescrito pelo api.py
PLATFORM_HOST = "br1"       # sobrescrito pelo api.py
OUTPUT_DIR        = "data/profiles"
QUEUE_FILTER      = 420   # 420 = Ranked Solo/Duo
BATCH_SIZE        = 100   # máximo permitido pela API por request
CACHE_MAX_AGE_H   = 1     # horas — se o cache tiver menos que isso, retorna direto sem buscar nada
CACHE_MAX_MATCHES = 200   # máximo de partidas mantidas no cache por player
# ══════════════════════════════════════════════


# ──────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────

def cache_path(game_name: str, tag_line: str) -> str:
    safe = f"{game_name}_{tag_line}".replace(" ", "_").replace("/", "_")
    return os.path.join(OUTPUT_DIR, f"{safe}.json")


def load_cache(game_name: str, tag_line: str) -> dict | None:
    """Carrega cache do player se existir."""
    path = cache_path(game_name, tag_line)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_cache_fresh(cache: dict) -> bool:
    """Retorna True se o cache foi atualizado há menos de CACHE_MAX_AGE_H horas."""
    updated_at = cache.get("updated_at", 0)
    age_hours  = (time.time() - updated_at) / 3600
    return age_hours < CACHE_MAX_AGE_H


def save_cache(data: dict, game_name: str, tag_line: str, output: str | None = None):
    """Salva o cache com timestamp de atualização."""
    data["updated_at"] = time.time()
    path = output or cache_path(game_name, tag_line)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def get_latest_cached_match_id(cache: dict) -> str | None:
    """Retorna o match_id mais recente do cache (primeira partida = mais nova)."""
    matches = cache.get("matches", [])
    if not matches:
        return None
    # Ordena pelo game_start para garantir que pega o mais recente
    sorted_m = sorted(matches, key=lambda m: m.get("meta", {}).get("game_start", 0), reverse=True)
    return sorted_m[0].get("match_id")


# ──────────────────────────────────────────────
# HTTP
# ──────────────────────────────────────────────

# Timestamps das últimas requests — usado pelo rate limiter
_req_times: deque = deque()

def request_api(url: str) -> dict | list:
    """GET com rate limit inteligente: máx 18 req/s e 95 req/2min."""
    global _req_times
    now = time.time()

    # Remove timestamps com mais de 2 minutos
    while _req_times and now - _req_times[0] > 120:
        _req_times.popleft()

    # Se chegou a 95 requests nos últimos 2min, espera o ciclo zerar
    if len(_req_times) >= 95:
        wait = 120 - (now - _req_times[0]) + 1.0
        print(f"  Rate limit (2min atingido). Aguardando {wait:.0f}s...")
        time.sleep(wait)
        now = time.time()
        while _req_times and now - _req_times[0] > 120:
            _req_times.popleft()

    # Garante no máximo 18 req/s
    if _req_times and (time.time() - _req_times[-1]) < (1 / 18):
        time.sleep((1 / 18) - (time.time() - _req_times[-1]))

    _req_times.append(time.time())

    while True:
        resp = requests.get(url, headers={"X-Riot-Token": KEY})
        if resp.status_code == 429:
            retry = float(resp.headers.get("Retry-After", 10))
            print(f"  429 recebido. Aguardando {retry:.0f}s...")
            time.sleep(retry)
            _req_times.clear()  # reseta contagem após 429
            continue
        if resp.status_code != 200:
            raise Exception(f"Erro {resp.status_code}: {resp.text}")
        return resp.json()


# ──────────────────────────────────────────────
# Dados do player
# ──────────────────────────────────────────────

def get_puuid(game_name: str, tag_line: str) -> str:
    url  = f"https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    data = request_api(url)
    return data["puuid"]


def get_summoner_info(puuid: str) -> dict:
    """Retorna nível da conta e elo atual."""
    url       = f"https://br1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
    data      = request_api(url)

    # A API retorna o elo diretamente pelo PUUID (summonerId foi removido da resposta)
    rank_url  = f"https://br1.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    rank_data = request_api(rank_url)
    ranked_solo = next((r for r in rank_data if r["queueType"] == "RANKED_SOLO_5x5"), None)

    return {
        "summoner_level":  data.get("summonerLevel", 0),
        "profile_icon_id": data.get("profileIconId", 0),
        "ranked": {
            "tier":    ranked_solo["tier"]         if ranked_solo else None,
            "rank":    ranked_solo["rank"]         if ranked_solo else None,
            "lp":      ranked_solo["leaguePoints"] if ranked_solo else None,
            "wins":    ranked_solo["wins"]         if ranked_solo else None,
            "losses":  ranked_solo["losses"]       if ranked_solo else None,
            "winrate": round(
                ranked_solo["wins"] / max(1, ranked_solo["wins"] + ranked_solo["losses"]) * 100, 1
            ) if ranked_solo else None,
        }
    }


# ──────────────────────────────────────────────
# Maestria
# ──────────────────────────────────────────────

def get_champion_mastery(puuid: str, champ_map: dict, top: int = 999) -> list[dict]:
    """
    Retorna as top N maestrias do player com nome do campeão resolvido.
    champ_map = {int(id): "NomeDoChamp"} — vem do ids_database.json
    """
    url      = f"https://br1.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}"
    maestrias = request_api(url)

    # Ordena por pontos e pega o top N
    top_maestrias = sorted(maestrias, key=lambda x: x["championPoints"], reverse=True)[:top]

    result = []
    for m in top_maestrias:
        last_play = datetime.fromtimestamp(m["lastPlayTime"] / 1000)
        result.append({
            "champion_id":    m["championId"],
            "champion_name":  champ_map.get(str(m["championId"]), "Unknown"),
            "mastery_level":  m["championLevel"],
            "mastery_points": m["championPoints"],
            "last_played":    last_play.strftime("%d/%m/%Y %H:%M"),
            "last_played_ts": m["lastPlayTime"],   # timestamp raw — útil pra ordenação no site
            "chest_granted":  m.get("chestGranted", False),
            "tokens_earned":  m.get("tokensEarned", 0),
        })

    return result


# ──────────────────────────────────────────────
# Partidas
# ──────────────────────────────────────────────

def get_match_ids(puuid: str, start: int = 0, count: int = 100) -> list[str]:
    if QUEUE_FILTER is None:
        url = (
            f"https://{ROUTING_HOST}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
            f"?start={start}&count={count}"
        )
    else:
        url = (
            f"https://{ROUTING_HOST}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
            f"?queue={QUEUE_FILTER}&start={start}&count={count}"
        )
    return request_api(url)


def get_match_data(match_id: str, puuid: str) -> dict:
    url    = f"https://americas.api.riotgames.com/lol/match/v5/matches/{match_id}"
    data   = request_api(url)
    player = next(p for p in data["info"]["participants"] if p["puuid"] == puuid)
    return extract_player_data(player, data["info"]["gameStartTimestamp"], match_id)


def get_match_timeline(match_id: str, puuid: str, items_data: dict) -> list[dict]:
    """Ordem real de compra dos itens core via timeline."""
    url  = f"https://americas.api.riotgames.com/lol/match/v5/matches/{match_id}/timeline"
    data = request_api(url)

    participant_id = None
    for p in data.get("info", {}).get("participants", []):
        if p.get("puuid") == puuid:
            participant_id = p["participantId"]
            break
    if participant_id is None:
        return []

    purchase_order = []
    seen = set()
    for frame in data["info"]["frames"]:
        for event in frame.get("events", []):
            if (
                event.get("type") == "ITEM_PURCHASED"
                and event.get("participantId") == participant_id
            ):
                item_id = str(event["itemId"])
                item    = items_data.get(item_id, {})
                if item.get("depth", 1) >= 2 and len(item.get("from", [])) >= 1:
                    if item_id not in seen:
                        seen.add(item_id)
                        purchase_order.append({
                            "id":        int(item_id),
                            "name":      item.get("name", "Unknown"),
                            "timestamp": event.get("timestamp", 0),
                        })
    return purchase_order


def extract_player_data(player: dict, game_start: int, match_id: str = "") -> dict:
    return {
        "match_id": match_id,
        "meta": {
            "game_start": game_start,   # epoch ms — usado para análise por horário
        },
        "champion": {
            "name":  player["championName"],
            "id":    player["championId"],
            "level": player["champLevel"],
        },
        "result": {
            "win":         player["win"],
            "time_played": player["timePlayed"],
        },
        "kda": {
            "kills":   player["kills"],
            "deaths":  player["deaths"],
            "assists": player["assists"],
            "ratio":   round((player["kills"] + player["assists"]) / max(1, player["deaths"]), 2),
        },
        "build": {
            "items": [
                player["item0"], player["item1"], player["item2"],
                player["item3"], player["item4"], player["item5"],
            ],
            "trinket":        player["item6"],
            "gold_earned":    player["goldEarned"],
            "gold_spent":     player["goldSpent"],
            "purchase_order": [],
        },
        "runes": {
            "primary_style":   player["perks"]["styles"][0]["style"],
            "primary_perks":   [p["perk"] for p in player["perks"]["styles"][0]["selections"]],
            "secondary_style": player["perks"]["styles"][1]["style"],
            "secondary_perks": [p["perk"] for p in player["perks"]["styles"][1]["selections"]],
            "stat_perks":      player["perks"]["statPerks"],
        },
        "farm": {
            "cs_total":  player["totalMinionsKilled"] + player["neutralMinionsKilled"],
            "cs_lane":   player["totalMinionsKilled"],
            "cs_jungle": player["neutralMinionsKilled"],
        },
        "vision": {
            "vision_score":  player["visionScore"],
            "wards_placed":  player["wardsPlaced"],
            "wards_killed":  player["wardsKilled"],
            "control_wards": player["detectorWardsPlaced"],
        },
        "combat": {
            "damage_dealt": player["totalDamageDealtToChampions"],
            "damage_taken": player["totalDamageTaken"],
            "cc_time":      player["totalTimeCCDealt"],
        },
        "economy": {
            "gold_per_min":   player.get("challenges", {}).get("goldPerMinute", 0),
            "damage_per_min": player.get("challenges", {}).get("damagePerMinute", 0),
        },
        "spells": {
            "summoner1": player["summoner1Id"],
            "summoner2": player["summoner2Id"],
        },
        "position": {
            "lane": player["lane"],
            "role": player["teamPosition"],
        },
        "objectives": {
            "dragons": player["dragonKills"],
            "barons":  player["baronKills"],
            "heralds": player.get("challenges", {}).get("riftHeraldTakedowns", 0),
            "towers":  player["turretTakedowns"],
        },
    }


def translate_data(match_data: dict, items_data: dict, runes_data: dict) -> dict:
    translated_items = []
    for i in match_data["build"]["items"]:
        if i == 0:
            translated_items.append(None)
            continue
        item = items_data.get(str(i))
        if item:
            translated_items.append({
                "id":          i,
                "name":        item["name"],
                "from":        item.get("from", []),
                "gold":        item.get("gold_total", 0),
                "stats":       item.get("stats", {}),
                "description": item.get("description", ""),
                "depth":       item.get("depth", 1),
            })
        else:
            translated_items.append({"id": i, "name": "Unknown", "depth": 1})
    match_data["build"]["items"] = translated_items

    trinket_id = match_data["build"]["trinket"]
    if trinket_id != 0:
        t = items_data.get(str(trinket_id), {})
        match_data["build"]["trinket"] = {
            "id":    trinket_id,
            "name":  t.get("name", "Unknown"),
            "depth": t.get("depth", 1),
        }

    match_data["runes"]["primary_style"]   = runes_data.get(str(match_data["runes"]["primary_style"]),   "Unknown")
    match_data["runes"]["secondary_style"] = runes_data.get(str(match_data["runes"]["secondary_style"]), "Unknown")
    match_data["runes"]["primary_perks"]   = [runes_data.get(str(p), "Unknown") for p in match_data["runes"]["primary_perks"]]
    match_data["runes"]["secondary_perks"] = [runes_data.get(str(p), "Unknown") for p in match_data["runes"]["secondary_perks"]]
    for key, val in match_data["runes"]["stat_perks"].items():
        match_data["runes"]["stat_perks"][key] = runes_data.get(str(val), "Unknown")

    return match_data


# ──────────────────────────────────────────────
# Pipeline principal
# ──────────────────────────────────────────────

def fetch_new_matches(puuid: str, stop_at_id: str | None, count: int, items_data: dict, runes_data: dict) -> list[dict]:
    """
    Busca partidas novas até encontrar stop_at_id (última do cache) ou atingir count.
    Retorna lista de partidas novas, da mais recente para a mais antiga.
    """
    new_matches = []
    start       = 0

    while len(new_matches) < count:
        batch = min(BATCH_SIZE, count - len(new_matches))
        ids   = get_match_ids(puuid, start=start, count=batch)
        if not ids:
            break

        for match_id in ids:
            # Para quando encontra uma partida que já está no cache
            if match_id == stop_at_id:
                return new_matches

            try:
                match_data = get_match_data(match_id, puuid)
                match_data = translate_data(match_data, items_data, runes_data)
                try:
                    match_data["build"]["purchase_order"] = get_match_timeline(match_id, puuid, items_data)
                except Exception:
                    match_data["build"]["purchase_order"] = []

                new_matches.append(match_data)
                result_str = "WIN " if match_data["result"]["win"] else "LOSS"
                print(f"  [+{len(new_matches):>3}] {result_str} | {match_data['champion']['name']:15s} | {match_data['position']['role']}")
            except Exception as e:
                print(f"  Erro na partida {match_id}: {e}")

        start += len(ids)
        if len(ids) < batch:
            break

    return new_matches


def fetch_player(game_name: str, tag_line: str, count: int, items_data: dict, runes_data: dict, champ_map: dict = {}, output: str | None = None) -> dict:
    print(f"\nBuscando player: {game_name}#{tag_line}")

    # ── Verifica se está fresco no banco ─────────────────────
    if db.is_player_fresh(game_name, tag_line, max_age_hours=CACHE_MAX_AGE_H):
        print(f"  Dados frescos no banco. Atualizando elo e maestria...")
        player_row = db.get_player(game_name, tag_line)
        puuid      = player_row["puuid"]

        summoner = get_summoner_info(puuid)
        mastery  = get_champion_mastery(puuid, champ_map)
        player_data = {"game_name": game_name, "tag_line": tag_line, "puuid": puuid, **summoner}
        db.upsert_player(player_data)
        db.upsert_mastery(puuid, mastery)

        matches = db.get_matches(puuid, limit=count)
        return {
            "player":        player_data,
            "mastery":       mastery,
            "matches":       matches,
            "total_matches": len(matches),
        }

    # ── Busca PUUID e info da conta ───────────────────────────
    puuid    = get_puuid(game_name, tag_line)
    print(f"  PUUID encontrado.")
    summoner = get_summoner_info(puuid)
    mastery  = get_champion_mastery(puuid, champ_map)
    print(f"  Maestrias carregadas.")

    player_data = {"game_name": game_name, "tag_line": tag_line, "puuid": puuid, **summoner}
    db.upsert_player(player_data)
    db.upsert_mastery(puuid, mastery)

    # ── Descobre partidas novas ───────────────────────────────
    latest_id = db.get_latest_match_id(puuid)
    if latest_id:
        print(f"  Player já existe no banco. Buscando partidas novas...")
    else:
        print(f"  Primeira busca. Coletando {count} partidas (isso pode demorar)...")

    new_matches = fetch_new_matches(
        puuid, stop_at_id=latest_id, count=count,
        items_data=items_data, runes_data=runes_data
    )

    if new_matches:
        inserted = db.insert_matches(puuid, new_matches)
        print(f"  +{inserted} partidas novas salvas no banco.")
    else:
        print(f"  Nenhuma partida nova encontrada.")

    matches = db.get_matches(puuid, limit=count)
    return {
        "player":        player_data,
        "mastery":       mastery,
        "matches":       matches,
        "total_matches": len(matches),
    }


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Puxa partidas ranked de um player específico.")
    parser.add_argument("--name",   required=True,         help="Game name (ex: Faker)")
    parser.add_argument("--tag",    required=True,         help="Tag line (ex: BR1)")
    parser.add_argument("--count",  type=int, default=200, help="Número de partidas (padrão: 200)")
    parser.add_argument("--output", default=None,          help="Caminho do arquivo de saída")
    args = parser.parse_args()

    with open("data/ids_database.json", encoding="utf-8") as f:
        ddragon = json.load(f)

    result = fetch_player(args.name, args.tag, args.count, ddragon["items"], ddragon["runes"], champ_map=ddragon.get("champions", {}))

    r = result["player"]["ranked"]
    print(f"\n  Player  : {args.name}#{args.tag}")
    if r and r["tier"]:
        print(f"  Elo     : {r['tier']} {r['rank']} — {r['lp']}LP ({r['wins']}W/{r['losses']}L — {r['winrate']}%)")
    else:
        print(f"  Elo     : Sem ranked")
    print(f"  Partidas: {result['total_matches']}")