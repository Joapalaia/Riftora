"""
player_analysis.py
Lê as partidas do banco, calcula a análise completa e salva de volta no Supabase.
Só recalcula se houver partidas novas desde a última análise.

Uso:
    python scripts/player_analysis.py --name "Gayxinho" --tag "2633"
    python scripts/player_analysis.py --name "Gayxinho" --tag "2633" --force
"""

import argparse
from collections import defaultdict
from datetime import datetime, timezone

import db


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def safe_avg(values):
    return round(sum(values) / len(values), 2) if values else 0.0

def winrate(wins, total):
    return round(wins / total * 100, 1) if total > 0 else 0.0

def kda_ratio(k, d, a):
    return round((k + a) / max(1, d), 2)


# ──────────────────────────────────────────────
# 1. Visão geral
# ──────────────────────────────────────────────

def overall_stats(matches):
    total = len(matches)
    wins  = sum(1 for m in matches if m["win"])
    avg_k = safe_avg([m["kills"]   for m in matches])
    avg_d = safe_avg([m["deaths"]  for m in matches])
    avg_a = safe_avg([m["assists"] for m in matches])
    return {
        "total_matches":         total,
        "wins":                  wins,
        "losses":                total - wins,
        "winrate":               winrate(wins, total),
        "avg_kda":               kda_ratio(avg_k, avg_d, avg_a),
        "avg_kills":             avg_k,
        "avg_deaths":            avg_d,
        "avg_assists":           avg_a,
        "avg_cs":                safe_avg([m["cs_total"]      for m in matches]),
        "avg_damage":            safe_avg([m["damage_dealt"]  for m in matches]),
        "avg_vision":            safe_avg([m["vision_score"]  for m in matches]),
        "avg_gold":              safe_avg([m["gold_earned"]   for m in matches]),
        "avg_game_duration_min": round(safe_avg([m["time_played"] for m in matches]) / 60, 1),
    }


# ──────────────────────────────────────────────
# 2. Streak atual
# ──────────────────────────────────────────────

def current_streak(matches):
    if not matches:
        return {"type": None, "count": 0}
    streak_type = "win" if matches[0]["win"] else "loss"
    count = 0
    for m in matches:
        if m["win"] == (streak_type == "win"):
            count += 1
        else:
            break
    return {"type": streak_type, "count": count}


# ──────────────────────────────────────────────
# 3. Por campeão
# ──────────────────────────────────────────────

def by_champion(matches):
    data = defaultdict(lambda: {"games": 0, "wins": 0, "kills": [], "deaths": [], "assists": [], "cs": [], "damage": []})
    for m in matches:
        c = m["champion_name"]
        data[c]["games"]   += 1
        data[c]["wins"]    += int(m["win"])
        data[c]["kills"].append(m["kills"])
        data[c]["deaths"].append(m["deaths"])
        data[c]["assists"].append(m["assists"])
        data[c]["cs"].append(m["cs_total"])
        data[c]["damage"].append(m["damage_dealt"])
    result = []
    for champ, d in data.items():
        avg_k = safe_avg(d["kills"])
        avg_d = safe_avg(d["deaths"])
        avg_a = safe_avg(d["assists"])
        result.append({
            "champion":    champ,
            "games":       d["games"],
            "wins":        d["wins"],
            "winrate":     winrate(d["wins"], d["games"]),
            "avg_kda":     kda_ratio(avg_k, avg_d, avg_a),
            "avg_kills":   avg_k,
            "avg_deaths":  avg_d,
            "avg_assists": avg_a,
            "avg_cs":      safe_avg(d["cs"]),
            "avg_damage":  safe_avg(d["damage"]),
        })
    return sorted(result, key=lambda x: x["games"], reverse=True)


# ──────────────────────────────────────────────
# 4. Por rota
# ──────────────────────────────────────────────

def by_role(matches):
    data = defaultdict(lambda: {"games": 0, "wins": 0, "cs": [], "damage": []})
    for m in matches:
        role = m.get("role") or m.get("lane") or "UNKNOWN"
        if role == "UNKNOWN":
            continue
        data[role]["games"] += 1
        data[role]["wins"]  += int(m["win"])
        data[role]["cs"].append(m["cs_total"])
        data[role]["damage"].append(m["damage_dealt"])
    result = []
    for role, d in data.items():
        result.append({
            "role":       role,
            "games":      d["games"],
            "wins":       d["wins"],
            "winrate":    winrate(d["wins"], d["games"]),
            "avg_cs":     safe_avg(d["cs"]),
            "avg_damage": safe_avg(d["damage"]),
        })
    return sorted(result, key=lambda x: x["games"], reverse=True)


# ──────────────────────────────────────────────
# 5. Evolução no tempo
# ──────────────────────────────────────────────

def time_evolution(matches, window=20):
    chronological = list(reversed(matches))
    total         = len(chronological)
    blocks = []
    for i in range(0, total, window):
        chunk = chronological[i:i + window]
        wins  = sum(1 for m in chunk if m["win"])
        blocks.append({
            "block":      i // window + 1,
            "range":      f"Partidas {i + 1}-{min(i + window, total)}",
            "games":      len(chunk),
            "wins":       wins,
            "winrate":    winrate(wins, len(chunk)),
            "avg_kda":    safe_avg([m["kda_ratio"]    for m in chunk]),
            "avg_cs":     safe_avg([m["cs_total"]     for m in chunk]),
            "avg_damage": safe_avg([m["damage_dealt"] for m in chunk]),
        })

    def summary(chunk):
        wins = sum(1 for m in chunk if m["win"])
        return {
            "games": len(chunk), "winrate": winrate(wins, len(chunk)),
            "avg_kda": safe_avg([m["kda_ratio"] for m in chunk]),
            "avg_cs":  safe_avg([m["cs_total"]  for m in chunk]),
            "avg_damage": safe_avg([m["damage_dealt"] for m in chunk]),
        }

    first = chronological[:window]
    last  = chronological[-window:]
    trend = None
    if first and last:
        diff  = winrate(sum(1 for m in last  if m["win"]), len(last)) - \
                winrate(sum(1 for m in first if m["win"]), len(first))
        trend = "improving" if diff > 3 else "declining" if diff < -3 else "stable"

    return {
        "blocks":      blocks,
        "first_games": summary(first),
        "last_games":  summary(last),
        "trend":       trend,
    }


# ──────────────────────────────────────────────
# 6. Por horário (BRT)
# ──────────────────────────────────────────────

def by_hour(matches):
    data = defaultdict(lambda: {"games": 0, "wins": 0})
    for m in matches:
        ts_ms = m.get("game_start")
        if not ts_ms:
            continue
        dt   = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        hour = (dt.hour - 3) % 24
        data[hour]["games"] += 1
        data[hour]["wins"]  += int(m["win"])
    return [
        {"hour": h, "label": f"{h:02d}:00", "games": d["games"],
         "wins": d["wins"], "winrate": winrate(d["wins"], d["games"])}
        for h, d in sorted(data.items())
    ]


# ──────────────────────────────────────────────
# 7. Mais jogado
# ──────────────────────────────────────────────

def most_played(matches):
    champ_counts = defaultdict(int)
    role_counts  = defaultdict(int)
    for m in matches:
        champ_counts[m["champion_name"]] += 1
        role = m.get("role") or m.get("lane") or "UNKNOWN"
        if role != "UNKNOWN":
            role_counts[role] += 1
    top_champ = max(champ_counts, key=champ_counts.get) if champ_counts else None
    top_role  = max(role_counts,  key=role_counts.get)  if role_counts  else None
    return {
        "champion": {"name": top_champ, "games": champ_counts[top_champ]} if top_champ else None,
        "role":     {"name": top_role,  "games": role_counts[top_role]}   if top_role  else None,
    }


# ──────────────────────────────────────────────
# Análise completa
# ──────────────────────────────────────────────

def analyze(matches):
    return {
        "overall":     overall_stats(matches),
        "streak":      current_streak(matches),
        "most_played": most_played(matches),
        "by_champion": by_champion(matches),
        "by_role":     by_role(matches),
        "evolution":   time_evolution(matches, window=20),
        "by_hour":     by_hour(matches),
    }


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calcula e salva a análise do player no banco.")
    parser.add_argument("--name",  required=True,       help="Game name")
    parser.add_argument("--tag",   required=True,       help="Tag line")
    parser.add_argument("--force", action="store_true", help="Força recalcular mesmo sem partidas novas")
    args = parser.parse_args()

    player = db.get_player(args.name, args.tag)
    if not player:
        print(f"Player {args.name}#{args.tag} não encontrado no banco.")
        print("Rode primeiro: python scripts/main.py --name ... --tag ...")
        exit(1)

    puuid = player["puuid"]

    if not args.force and not db.needs_analysis_update(puuid):
        print(f"{args.name}#{args.tag} — análise já está atualizada.")
        analysis = db.get_analysis(puuid)
        o = analysis["overall"]
        print(f"  {o['total_matches']} partidas | {o['winrate']}% WR | KDA {o['avg_kda']}")
        exit(0)

    matches = db.get_matches(puuid, limit=200)
    if not matches:
        print("Nenhuma partida encontrada no banco.")
        exit(1)

    print(f"{args.name}#{args.tag} — analisando {len(matches)} partidas...")
    analysis = analyze(matches)
    db.upsert_analysis(puuid, analysis, matches_analyzed=len(matches))

    o = analysis["overall"]
    s = analysis["streak"]
    m = analysis["most_played"]
    e = analysis["evolution"]
    trend_label = {"improving": "Melhorando", "declining": "Piorando", "stable": "Estavel"}

    print(f"\n{'='*52}")
    print(f"  {args.name}#{args.tag}")
    print(f"  {o['total_matches']} partidas | {o['winrate']}% WR | KDA {o['avg_kda']}")
    print(f"  CS medio: {o['avg_cs']} | Dano medio: {o['avg_damage']}")
    if s["count"] > 1:
        print(f"  Streak: {s['count']}x {'vitorias' if s['type'] == 'win' else 'derrotas'} seguidas")
    if m["champion"]:
        print(f"  Mais jogado: {m['champion']['name']} ({m['champion']['games']} jogos)")
    if e["trend"]:
        print(f"  Tendencia: {trend_label.get(e['trend'], e['trend'])}")
    print(f"{'='*52}")
    print(f"  Analise salva no banco.\n")