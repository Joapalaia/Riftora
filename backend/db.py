"""
db.py
Módulo de acesso ao banco PostgreSQL (Supabase).
Importado pelo player_fetch.py e futuramente pelo site.

Não rode diretamente — é uma biblioteca.
"""

import json
import time
import psycopg2
import psycopg2.extras
import decimal

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        return super().default(obj)
    
# ============================================================
import os
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:TONELA177%40!@db.aomemhxdhhipudolpfpp.supabase.co:5432/postgres")
# ============================================================


# ──────────────────────────────────────────────
# Conexão
# ──────────────────────────────────────────────

def get_conn():
    """Retorna uma conexão com o banco."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


# ──────────────────────────────────────────────
# Players
# ──────────────────────────────────────────────

def upsert_player(player: dict) -> None:
    """Insere ou atualiza os dados do player."""
    ranked = player.get("ranked") or {}
    sql = """
        INSERT INTO players (
            puuid, game_name, tag_line,
            summoner_level, profile_icon_id,
            tier, rank, lp, ranked_wins, ranked_losses, ranked_winrate,
            updated_at
        ) VALUES (
            %(puuid)s, %(game_name)s, %(tag_line)s,
            %(summoner_level)s, %(profile_icon_id)s,
            %(tier)s, %(rank)s, %(lp)s, %(wins)s, %(losses)s, %(winrate)s,
            NOW()
        )
        ON CONFLICT (puuid) DO UPDATE SET
            game_name       = EXCLUDED.game_name,
            tag_line        = EXCLUDED.tag_line,
            summoner_level  = EXCLUDED.summoner_level,
            profile_icon_id = EXCLUDED.profile_icon_id,
            tier            = EXCLUDED.tier,
            rank            = EXCLUDED.rank,
            lp              = EXCLUDED.lp,
            ranked_wins     = EXCLUDED.ranked_wins,
            ranked_losses   = EXCLUDED.ranked_losses,
            ranked_winrate  = EXCLUDED.ranked_winrate,
            updated_at      = NOW();
    """
    params = {
        "puuid":           player["puuid"],
        "game_name":       player["game_name"],
        "tag_line":        player["tag_line"],
        "summoner_level":  player.get("summoner_level"),
        "profile_icon_id": player.get("profile_icon_id"),
        "tier":            ranked.get("tier"),
        "rank":            ranked.get("rank"),
        "lp":              ranked.get("lp"),
        "wins":            ranked.get("wins"),
        "losses":          ranked.get("losses"),
        "winrate":         ranked.get("winrate"),
    }
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def get_player(game_name: str, tag_line: str) -> dict | None:
    """Busca player por nick+tag. Retorna None se não existir."""
    sql = """
        SELECT * FROM players
        WHERE LOWER(game_name) = LOWER(%s)
          AND LOWER(tag_line)  = LOWER(%s)
        LIMIT 1;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (game_name, tag_line))
            row = cur.fetchone()
            return dict(row) if row else None


def is_player_fresh(game_name: str, tag_line: str, max_age_hours: float = 1.0) -> bool:
    """Retorna True se o player foi atualizado há menos de max_age_hours."""
    sql = """
        SELECT updated_at FROM players
        WHERE LOWER(game_name) = LOWER(%s)
          AND LOWER(tag_line)  = LOWER(%s)
        LIMIT 1;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (game_name, tag_line))
            row = cur.fetchone()
            if not row:
                return False
            age_hours = (time.time() - row["updated_at"].timestamp()) / 3600
            return age_hours < max_age_hours


# ──────────────────────────────────────────────
# Maestria
# ──────────────────────────────────────────────

def upsert_mastery(puuid: str, mastery_list: list[dict]) -> None:
    """Substitui todas as maestrias do player."""
    sql = """
        INSERT INTO champion_mastery (
            puuid, champion_id, champion_name,
            mastery_level, mastery_points,
            last_played_ts, last_played,
            chest_granted, tokens_earned
        ) VALUES (
            %(puuid)s, %(champion_id)s, %(champion_name)s,
            %(mastery_level)s, %(mastery_points)s,
            %(last_played_ts)s, %(last_played)s,
            %(chest_granted)s, %(tokens_earned)s
        )
        ON CONFLICT (puuid, champion_id) DO UPDATE SET
            champion_name  = EXCLUDED.champion_name,
            mastery_level  = EXCLUDED.mastery_level,
            mastery_points = EXCLUDED.mastery_points,
            last_played_ts = EXCLUDED.last_played_ts,
            last_played    = EXCLUDED.last_played,
            chest_granted  = EXCLUDED.chest_granted,
            tokens_earned  = EXCLUDED.tokens_earned;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            for m in mastery_list:
                cur.execute(sql, {**m, "puuid": puuid})


def get_mastery(puuid: str) -> list[dict]:
    """Retorna as maestrias do player ordenadas por pontos."""
    sql = """
        SELECT * FROM champion_mastery
        WHERE puuid = %s
        ORDER BY mastery_points DESC;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (puuid,))
            return [dict(r) for r in cur.fetchall()]


# ──────────────────────────────────────────────
# Partidas
# ──────────────────────────────────────────────

def insert_matches(puuid: str, matches: list[dict]) -> int:
    """
    Insere partidas novas. Ignora duplicatas (match_id + puuid já existe).
    Retorna quantas foram inseridas de fato.
    """
    sql = """
        INSERT INTO matches (
            match_id, puuid, game_start, game_version,
            champion_name, champion_id, champion_level,
            win, time_played,
            kills, deaths, assists, kda_ratio,
            cs_total, cs_lane, cs_jungle,
            damage_dealt, damage_taken, cc_time,
            vision_score, wards_placed, wards_killed, control_wards,
            gold_earned, gold_spent, gold_per_min, damage_per_min,
            dragons, barons, heralds, towers,
            lane, role, team_side, summoner1, summoner2,
            items, trinket, purchase_order, runes
        ) VALUES (
            %(match_id)s, %(puuid)s, %(game_start)s, %(game_version)s,
            %(champion_name)s, %(champion_id)s, %(champion_level)s,
            %(win)s, %(time_played)s,
            %(kills)s, %(deaths)s, %(assists)s, %(kda_ratio)s,
            %(cs_total)s, %(cs_lane)s, %(cs_jungle)s,
            %(damage_dealt)s, %(damage_taken)s, %(cc_time)s,
            %(vision_score)s, %(wards_placed)s, %(wards_killed)s, %(control_wards)s,
            %(gold_earned)s, %(gold_spent)s, %(gold_per_min)s, %(damage_per_min)s,
            %(dragons)s, %(barons)s, %(heralds)s, %(towers)s,
            %(lane)s, %(role)s, %(summoner1)s, %(summoner2)s,
            %(items)s, %(trinket)s, %(purchase_order)s, %(runes)s
        )
        ON CONFLICT (match_id, puuid) DO NOTHING;
    """
    inserted = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for m in matches:
                champ  = m.get("champion", {})
                result = m.get("result", {})
                kda    = m.get("kda", {})
                farm   = m.get("farm", {})
                combat = m.get("combat", {})
                vision = m.get("vision", {})
                eco    = m.get("economy", {})
                obj    = m.get("objectives", {})
                pos    = m.get("position", {})
                spells = m.get("spells", {})
                build  = m.get("build", {})
                runes  = m.get("runes", {})

                params = {
                    "match_id":       m.get("match_id", ""),
                    "puuid":          puuid,
                    "game_start":     m.get("meta", {}).get("game_start"),
                    "game_version":   m.get("game_version", ""),
                    "champion_name":  champ.get("name"),
                    "champion_id":    champ.get("id"),
                    "champion_level": champ.get("level"),
                    "win":            result.get("win"),
                    "time_played":    result.get("time_played"),
                    "kills":          kda.get("kills"),
                    "deaths":         kda.get("deaths"),
                    "assists":        kda.get("assists"),
                    "kda_ratio":      kda.get("ratio"),
                    "cs_total":       farm.get("cs_total"),
                    "cs_lane":        farm.get("cs_lane"),
                    "cs_jungle":      farm.get("cs_jungle"),
                    "damage_dealt":   combat.get("damage_dealt"),
                    "damage_taken":   combat.get("damage_taken"),
                    "cc_time":        combat.get("cc_time"),
                    "vision_score":   vision.get("vision_score"),
                    "wards_placed":   vision.get("wards_placed"),
                    "wards_killed":   vision.get("wards_killed"),
                    "control_wards":  vision.get("control_wards"),
                    "gold_earned":    build.get("gold_earned"),
                    "gold_spent":     build.get("gold_spent"),
                    "gold_per_min":   eco.get("gold_per_min"),
                    "damage_per_min": eco.get("damage_per_min"),
                    "dragons":        obj.get("dragons"),
                    "barons":         obj.get("barons"),
                    "heralds":        obj.get("heralds"),
                    "towers":         obj.get("towers"),
                    "lane":           pos.get("lane"),
                    "role":           pos.get("role"),
                    "team_side":      pos.get("team_side"),
                    "summoner1":      spells.get("summoner1"),
                    "summoner2":      spells.get("summoner2"),
                    "items":          json.dumps(build.get("items", [])),
                    "trinket":        json.dumps(build.get("trinket")),
                    "purchase_order": json.dumps(build.get("purchase_order", [])),
                    "runes":          json.dumps(runes),
                }
                cur.execute(sql, params)
                inserted += cur.rowcount

    return inserted


def get_matches(puuid: str, limit: int = 200) -> list[dict]:
    """Retorna as últimas N partidas do player."""
    sql = """
        SELECT * FROM matches
        WHERE puuid = %s
        ORDER BY game_start DESC
        LIMIT %s;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (puuid, limit))
            rows = cur.fetchall()
            result = []
            for row in rows:
                r = dict(row)
                # Desserializa os campos JSONB
                for field in ("items", "trinket", "purchase_order", "runes"):
                    if isinstance(r.get(field), str):
                        r[field] = json.loads(r[field])
                result.append(r)
            return result


def get_latest_match_id(puuid: str) -> str | None:
    """Retorna o match_id mais recente do player no banco."""
    sql = """
        SELECT match_id FROM matches
        WHERE puuid = %s
        ORDER BY game_start DESC
        LIMIT 1;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (puuid,))
            row = cur.fetchone()
            return row["match_id"] if row else None

# ──────────────────────────────────────────────
# Análise
# ──────────────────────────────────────────────

def get_analysis(puuid: str) -> dict | None:
    """Retorna a análise salva do player."""
    sql = "SELECT * FROM player_analysis WHERE puuid = %s;"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (puuid,))
            row = cur.fetchone()
            if not row:
                return None
            r = dict(row)
            # Desserializa campos JSONB que vierem como string
            for field in ("overall", "streak", "most_played", "by_champion", "by_role", "evolution", "by_hour"):
                if isinstance(r.get(field), str):
                    r[field] = json.loads(r[field])
            return r


def needs_analysis_update(puuid: str) -> bool:
    """
    Retorna True se a análise precisa ser recalculada.
    Isso acontece quando:
      - Não existe análise salva, OU
      - O número de partidas no banco é maior que o analisado na última vez
    """
    sql = """
        SELECT
            (SELECT COUNT(*) FROM matches WHERE puuid = %s) AS total_matches,
            (SELECT matches_analyzed FROM player_analysis WHERE puuid = %s) AS analyzed
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (puuid, puuid))
            row = cur.fetchone()
            if not row or row["analyzed"] is None:
                return True
            return row["total_matches"] > row["analyzed"]


def upsert_analysis(puuid: str, analysis: dict, matches_analyzed: int) -> None:
    """Salva ou atualiza a análise do player."""
    sql = """
        INSERT INTO player_analysis (
            puuid, analyzed_at, matches_analyzed,
            overall, streak, most_played,
            by_champion, by_role, evolution, by_hour
        ) VALUES (
            %(puuid)s, NOW(), %(matches_analyzed)s,
            %(overall)s, %(streak)s, %(most_played)s,
            %(by_champion)s, %(by_role)s, %(evolution)s, %(by_hour)s
        )
        ON CONFLICT (puuid) DO UPDATE SET
            analyzed_at      = NOW(),
            matches_analyzed = EXCLUDED.matches_analyzed,
            overall          = EXCLUDED.overall,
            streak           = EXCLUDED.streak,
            most_played      = EXCLUDED.most_played,
            by_champion      = EXCLUDED.by_champion,
            by_role          = EXCLUDED.by_role,
            evolution        = EXCLUDED.evolution,
            by_hour          = EXCLUDED.by_hour;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {
                "puuid":            puuid,
                "matches_analyzed": matches_analyzed,
                "overall":          json.dumps(analysis["overall"],     cls=DecimalEncoder),
"streak":           json.dumps(analysis["streak"],      cls=DecimalEncoder),
"most_played":      json.dumps(analysis["most_played"], cls=DecimalEncoder),
"by_champion":      json.dumps(analysis["by_champion"], cls=DecimalEncoder),
"by_role":          json.dumps(analysis["by_role"],     cls=DecimalEncoder),
"evolution":        json.dumps(analysis["evolution"],   cls=DecimalEncoder),
"by_hour":          json.dumps(analysis["by_hour"],     cls=DecimalEncoder),
            })