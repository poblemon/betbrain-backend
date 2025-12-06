"""
BetBrain IA - Backend API
FastAPI backend con Football-Data.org + The Odds API
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from datetime import datetime, timedelta
from typing import Optional
import asyncio

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

# Mapeo de ligas Football-Data -> The Odds API
LEAGUE_MAPPING = {
    2021: "soccer_epl",           # Premier League
    2014: "soccer_spain_la_liga",  # La Liga
    2019: "soccer_italy_serie_a",  # Serie A
    2002: "soccer_germany_bundesliga", # Bundesliga
    2015: "soccer_france_ligue_one",   # Ligue 1
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

@app.get("/")
async def root():
    return {
        "message": "BetBrain IA API",
        "status": "online",
        "version": "2.0.0",
        "features": ["Football-Data.org", "The Odds API", "Real-time odds"]
    }

@app.get("/api/competitions")
async def get_competitions():
    cache_key = "competitions"
    cached_data = get_from_cache(cache_key)
    
    if cached_data:
        return cached_data
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{FOOTBALL_DATA_API}/competitions",
                headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN},
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()
            set_cache(cache_key, data)
            return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/odds/{league_key}")
async def get_odds(league_key: str):
    """Obtener cuotas reales de The Odds API"""
    cache_key = f"odds_{league_key}"
    cached_data = get_from_cache(cache_key)
    
    if cached_data:
        return cached_data
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{ODDS_API}/sports/{league_key}/odds",
                params={
                    "apiKey": ODDS_API_KEY,
                    "regions": "eu,uk",
                    "markets": "h2h",
                    "oddsFormat": "decimal"
                },
                timeout=15.0
            )
            response.raise_for_status()
            data = response.json()
            set_cache(cache_key, data)
            return data
    except Exception as e:
        return {"error": str(e), "data": []}

@app.get("/api/matches/{league_id}")
async def get_matches(league_id: int, status: str = "SCHEDULED,LIVE"):
    cache_key = f"matches_{league_id}_{status}"
    cached_data = get_from_cache(cache_key)
    
    if cached_data:
        return cached_data
    
    try:
        async with httpx.AsyncClient() as client:
            # Obtener partidos de Football-Data
            fd_response = await client.get(
                f"{FOOTBALL_DATA_API}/competitions/{league_id}/matches",
                params={"status": status},
                headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN},
                timeout=10.0
            )
            fd_response.raise_for_status()
            matches_data = fd_response.json()
            
            # Obtener cuotas reales si la liga está mapeada
            odds_data = []
            league_key = LEAGUE_MAPPING.get(league_id)
            if league_key:
                try:
                    odds_response = await client.get(
                        f"{ODDS_API}/sports/{league_key}/odds",
                        params={
                            "apiKey": ODDS_API_KEY,
                            "regions": "eu,uk",
                            "markets": "h2h,totals,btts",
                            "oddsFormat": "decimal"
                        },
                        timeout=15.0
                    )
                    if odds_response.status_code == 200:
                        odds_data = odds_response.json()
                except:
                    pass
            
            # Enriquecer partidos con cuotas reales
            enriched_matches = []
            for match in matches_data.get("matches", [])[:20]:
                home_team = match["homeTeam"]["name"]
                away_team = match["awayTeam"]["name"]
                
                # Buscar cuotas reales para este partido
                match_odds = None
                for odds_match in odds_data:
                    if (home_team.lower() in odds_match.get("home_team", "").lower() or 
                        away_team.lower() in odds_match.get("away_team", "").lower()):
                        match_odds = odds_match
                        break
                
                # Procesar cuotas
                if match_odds and match_odds.get("bookmakers"):
                    # Usar el primer bookmaker disponible
                    bookmaker = match_odds["bookmakers"][0]
                    h2h_market = next((m for m in bookmaker["markets"] if m["key"] == "h2h"), None)
                    
                    if h2h_market:
                        outcomes = {o["name"]: o["price"] for o in h2h_market["outcomes"]}
                        home_odds = outcomes.get(match_odds["home_team"], 2.0)
                        away_odds = outcomes.get(match_odds["away_team"], 2.0)
                        
                        # Calcular cuota de empate (estimado)
                        draw_odds = round(1 / (1 - (1/home_odds + 1/away_odds)), 2)
                        
                        enriched_match = {
                            **match,
                            "odds": {
                                "home": round(home_odds, 2),
                                "draw": round(draw_odds, 2),
                                "away": round(away_odds, 2),
                                "source": bookmaker["title"]
                            }
                        }
                    else:
                        enriched_match = {**match, "odds": None}
                else:
                    # Sin cuotas reales, usar estimación básica
                    enriched_match = {
                        **match,
                        "odds": {
                            "home": 2.10,
                            "draw": 3.40,
                            "away": 3.50,
                            "source": "Estimated"
                        }
                    }
                
                enriched_matches.append(enriched_match)
            
            result = {
                "matches": enriched_matches,
                "odds_available": len(odds_data) > 0
            }
            set_cache(cache_key, result)
            return result
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/standings/{league_id}")
async def get_standings(league_id: int):
    cache_key = f"standings_{league_id}"
    cached_data = get_from_cache(cache_key)
    
    if cached_data:
        return cached_data
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{FOOTBALL_DATA_API}/competitions/{league_id}/standings",
                headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN},
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()
            set_cache(cache_key, data)
            return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/bookmakers")
async def get_bookmakers():
    """Listar bookmakers disponibles para una liga"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{ODDS_API}/sports/soccer_epl/odds",
                params={
                    "apiKey": ODDS_API_KEY,
                    "regions": "eu,uk",
                    "markets": "h2h"
                },
                timeout=10.0
            )
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    bookmakers = [b["title"] for b in data[0].get("bookmakers", [])]
                    return {"bookmakers": bookmakers, "count": len(bookmakers)}
            return {"bookmakers": [], "count": 0}
    except Exception as e:
        return {"error": str(e), "bookmakers": []}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "apis": {
            "football_data": "active",
            "odds_api": "active"
        }
    }
