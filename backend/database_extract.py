"""
ddragon.py
Baixa e salva o banco de dados de IDs do Data Dragon (items, runas, campeões, feitiços).
Deve ser rodado uma vez por patch para manter os dados atualizados.

Uso:
    python ddragon.py
    python ddragon.py --version 14.10.1  # forçar versão específica
    python ddragon.py --lang en_US        # idioma alternativo
    python ddragon.py --force             # força re-download mesmo se versão for igual
"""

import argparse
import json
import logging
import os

import requests

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

OUTPUT_FILE  = "data/ids_database.json"
DEFAULT_LANG = "pt_BR"
DDRAGON_BASE = "https://ddragon.leagueoflegends.com"

# Stat perks não existem no ddragon — mapeamento manual
STAT_PERKS: dict[int, str] = {
    5001: "Vida",
    5002: "Armadura",
    5003: "Resistência Mágica",
    5005: "Velocidade de Ataque",
    5007: "Habilidade Acelerada",
    5008: "Força Adaptativa",
    5010: "Velocidade de Movimento",
    5011: "Vida Extra",
    5013: "Tenacidade e Desaceleração Lenta",
}

log = logging.getLogger("ddragon")


# ──────────────────────────────────────────────
# HTTP
# ──────────────────────────────────────────────

def _get(url: str) -> dict | list:
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ──────────────────────────────────────────────
# Versão
# ──────────────────────────────────────────────

def get_latest_version() -> str:
    versions = _get(f"{DDRAGON_BASE}/api/versions.json")
    return versions[0]


# ──────────────────────────────────────────────
# Skip se banco já está atualizado
# ──────────────────────────────────────────────

def is_up_to_date(path: str, version: str, lang: str) -> bool:
    """Retorna True se o banco já existe com a mesma versão e idioma."""
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
        return existing.get("version") == version and existing.get("lang") == lang
    except Exception:
        return False


# ──────────────────────────────────────────────
# Items
# ──────────────────────────────────────────────

def get_items(version: str, lang: str) -> dict[str, dict]:
    url  = f"{DDRAGON_BASE}/cdn/{version}/data/{lang}/item.json"
    data = _get(url)
    raw: dict = data["data"]

    items: dict[str, dict] = {}
    for item_id, item in raw.items():
        ingredients = [
            {"id": dep_id, "name": raw[dep_id]["name"]}
            for dep_id in item.get("from", [])
            if dep_id in raw
        ]
        items[item_id] = {
            "name":        item.get("name", ""),
            "from":        ingredients,
            "gold_total":  item.get("gold", {}).get("total", 0),
            "depth":       item.get("depth", 1),
            "stats":       item.get("stats", {}),
            "plaintext":   item.get("plaintext", ""),
            "description": item.get("description", ""),
        }

    log.info("Items carregados: %d", len(items))
    return items


# ──────────────────────────────────────────────
# Runas
# ──────────────────────────────────────────────

def get_runes(version: str, lang: str) -> dict[int, str]:
    url  = f"{DDRAGON_BASE}/cdn/{version}/data/{lang}/runesReforged.json"
    data: list = _get(url)

    runes: dict[int, str] = {}
    for tree in data:
        runes[tree["id"]] = tree["name"]
        for slot in tree["slots"]:
            for rune in slot["runes"]:
                runes[rune["id"]] = rune["name"]

    runes.update(STAT_PERKS)

    log.info("Runas carregadas: %d (+ %d stat perks)", len(runes) - len(STAT_PERKS), len(STAT_PERKS))
    return runes


# ──────────────────────────────────────────────
# Campeões
# ──────────────────────────────────────────────

def get_champions(version: str, lang: str) -> dict[str, str]:
    url  = f"{DDRAGON_BASE}/cdn/{version}/data/{lang}/champion.json"
    data = _get(url)

    champions = {
        champ["key"]: champ["name"]
        for champ in data["data"].values()
    }

    log.info("Campeões carregados: %d", len(champions))
    return champions


# ──────────────────────────────────────────────
# Feitiços de invocador
# ──────────────────────────────────────────────

def get_summoner_spells(version: str, lang: str) -> dict[str, str]:
    url  = f"{DDRAGON_BASE}/cdn/{version}/data/{lang}/summoner.json"
    data = _get(url)

    spells = {
        spell["key"]: spell["name"]
        for spell in data["data"].values()
    }

    log.info("Feitiços carregados: %d", len(spells))
    return spells


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def build_database(version: str | None = None, lang: str = DEFAULT_LANG) -> dict:
    if version is None:
        version = get_latest_version()
        log.info("Versão mais recente: %s", version)
    else:
        log.info("Usando versão forçada: %s", version)

    return {
        "version":         version,
        "lang":            lang,
        "items":           get_items(version, lang),
        "runes":           get_runes(version, lang),
        "champions":       get_champions(version, lang),
        "summoner_spells": get_summoner_spells(version, lang),
    }


def save_database(db: dict, path: str = OUTPUT_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
    log.info("Banco salvo em: %s", path)


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Baixa o banco de IDs do Data Dragon.")
    parser.add_argument("--version", default=None,        help="Versão específica do patch (ex: 14.10.1)")
    parser.add_argument("--lang",    default=DEFAULT_LANG, help="Idioma (padrão: pt_BR)")
    parser.add_argument("--output",  default=OUTPUT_FILE,  help="Caminho do arquivo de saída")
    parser.add_argument("--force",   action="store_true",  help="Força re-download mesmo se versão já estiver salva")
    args = parser.parse_args()

    # Resolve a versão antes de checar o cache
    target_version = args.version or get_latest_version()

    if not args.force and is_up_to_date(args.output, target_version, args.lang):
        log.info("Banco já está atualizado (versão %s, idioma %s). Use --force para re-baixar.", target_version, args.lang)
        print(f"\n  Banco já atualizado: {args.output} (versão {target_version})\n")
    else:
        db = build_database(version=target_version, lang=args.lang)
        save_database(db, path=args.output)

        print(f"\n  Versão  : {db['version']}")
        print(f"  Idioma  : {db['lang']}")
        print(f"  Items   : {len(db['items'])}")
        print(f"  Runas   : {len(db['runes'])}")
        print(f"  Campeões: {len(db['champions'])}")
        print(f"  Feitiços: {len(db['summoner_spells'])}")
        print(f"  Salvo   : {args.output}\n")