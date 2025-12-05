"""
BetBrain IA - Backend API
FastAPI backend para manejar llamadas a Football-Data.org
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from datetime import datetime, timedelta

app = FastAPI(title="BetBrain IA API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FOOTBALL_DATA_API = "https://api.football-data.org/v4"
FOOTBALL_DATA_TOKEN = "c8e58951f8eb4904a5bf090c681d5e62"

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
        "version": "1.0.0"
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

@app.get("/api/matches/{league_id}")
async def get_matches(league_id: int, status: str = "SCHEDULED,LIVE"):
    cache_key = f"matches_{league_id}_{status}"
    cached_data = get_from_cache(cache_key)
    
    if cached_data:
        return cached_data
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{FOOTBALL_DATA_API}/competitions/{league_id}/matches",
                params={"status": status},
                headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN},
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()
            set_cache(cache_key, data)
            return data
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

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}
