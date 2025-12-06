"""
BetBrain IA - Backend API v2.0
FastAPI backend con Football-Data.org + The Odds API
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from datetime import datetime, timedelta
from typing import Optional
from difflib import SequenceMatcher

app = FastAPI(title="BetBrain IA API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración APIs
FOOTBALL_DATA_API = "https://api.football-data.org/v4"
FOOTBALL_DATA_TOKEN = "c8e58951f8eb4904a5bf090c681d5e62"

ODDS_API = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = "924468b00a5d3be11b7549d7741f9157"

# Mapeo ligas
LEAGUE_MAPPING = {
    2021: "soccer_epl",
    2014: "soccer_spain_la_liga",
    2019: "soccer_italy_serie_a",
    2002: "soccer_germany_bundesliga",
    2015: "soccer_france_ligue_one",
}

# Cache
cache = {}
CACHE_DURATION = 300

def get_from_cache(key: str):
    if key in cache:
        data, timestamp = cache[key]
        if datetime.now() - timestamp < timedelta(seconds=CACHE_DURATION):
            return data
    return None

def set_cache(key: str, data):
    cache[key] = (data, datetime.now())

def similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# --- WRAPPERS ESTABLES PARA APIs ---
async def get_odds_api(league_key: str):
    url = f"{ODDS_API}/sports/{league_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h,totals,btts",
        "oddsFormat": "decimal",
        "dateFormat": "iso"
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": str(e), "data": []}


# --- ENDPOINTS PRINCIPALES ---
@app.get("/")
async def root():
    return {
        "message": "BetBrain IA API",
        "status": "online",
        "version": "2.0.0",
    }


@app.get("/api/competitions")
async def get_competitions():
    cache_key = "competitions"
    cached = get_from_cache(cache_key)
    if cached: return cached

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{FOOTBALL_DATA_API}/competitions",
                headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN},
                timeout=10.0
            )
            res.raise_for_status()
            data = res.json()
            set_cache(cache_key, data)
            return data
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/odds/{league_key}")
async def get_odds(league_key: str):
    cache_key = f"odds_{league_key}"
    cached = get_from_cache(cache_key)
    if cached: return cached

    data = await get_odds_api(league_key)
    set_cache(cache_key, data)
    return data


@app.get("/api/matches/{league_id}")
async def get_matches(league_id: int, status: str = "SCHEDULED,LIVE"):
    cache_key = f"matches_{league_id}_{status}"
    cached = get_from_cache(cache_key)
    if cached: return cached

    try:
        async with httpx.AsyncClient() as client:
            fd_res = await client.get(
                f"{FOOTBALL_DATA_API}/competitions/{league_id}/matches",
                params={"status": status},
                headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN},
                timeout=10.0
            )
            fd_res.raise_for_status()
            matches = fd_res.json().get("matches", [])

            league_key = LEAGUE_MAPPING.get(league_id)
            odds_data = await get_odds_api(league_key) if league_key else []

            enriched = []

            for m in matches[:20]:
                home = m["homeTeam"]["name"]
                away = m["awayTeam"]["name"]

                match_odds = None
                for o in odds_data if isinstance(odds_data, list) else []:
                    if similar(home, o.get("home_team","")) > 0.75:
                        match_odds = o
                        break

                if match_odds and match_odds.get("bookmakers"):
                    bm = match_odds["bookmakers"][0]
                    h2h = next((mk for mk in bm["markets"] if mk["key"] == "h2h"), None)

                    if h2h:
                        out = {x["name"]: x["price"] for x in h2h["outcomes"]}
                        enriched.append({
                            **m,
                            "odds": {
                                "home": out.get(match_odds["home_team"], 2.0),
                                "away": out.get(match_odds["away_team"], 2.0),
                                "source": bm["title"]
                            }
                        })
                else:
                    enriched.append({**m, "odds": None})

            result = {
                "matches": enriched,
                "odds_available": bool(odds_data)
            }

            set_cache(cache_key, result)
            return result

    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/standings/{league_id}")
async def get_standings(league_id: int):
    cache_key = f"standings_{league_id}"
    cached = get_from_cache(cache_key)
    if cached: return cached

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{FOOTBALL_DATA_API}/competitions/{league_id}/standings",
                headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN},
                timeout=10.0
            )
            res.raise_for_status()
            data = res.json()
            set_cache(cache_key, data)
            return data
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/bookmakers")
async def get_bookmakers():
    data = await get_odds_api("soccer_epl")

    if isinstance(data, list) and len(data) > 0:
        return {
            "bookmakers": [b["title"] for b in data[0].get("bookmakers", [])],
            "count": len(data[0].get("bookmakers", []))
        }

    return {"bookmakers": [], "count": 0}


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }
