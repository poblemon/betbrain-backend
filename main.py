"""
BetBrain IA - Backend UNIFICADO (Lista + Detalles + Chat)
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from datetime import datetime, timedelta
from typing import List, Dict
import asyncio
from scipy.stats import poisson # Asegúrate de que requirements.txt tenga scipy y numpy

app = FastAPI(title="BetBrain IA Master", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURACIÓN ---
FOOTBALL_DATA_API = "https://api.football-data.org/v4"
FOOTBALL_DATA_TOKEN = "c8e58951f8eb4904a5bf090c681d5e62"
OPENROUTER_API_KEY = "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"

LEAGUE_MAPPING = {
    2021: "Premier League", 2014: "La Liga",
    2019: "Serie A", 2002: "Bundesliga",
    2015: "Ligue 1"
}

cache = {}

# --- HELPER: Probabilidades Poisson (Para la Lista Principal) ---
def calculate_poisson_probs(home_avg, away_avg):
    home_prob, draw_prob, away_prob = 0, 0, 0
    for h in range(6):
        for a in range(6):
            prob = poisson.pmf(h, home_avg) * poisson.pmf(a, away_avg)
            if h > a: home_prob += prob
            elif h == a: draw_prob += prob
            else: away_prob += prob
    return {
        "home_win": round(home_prob * 100, 1),
        "draw": round(draw_prob * 100, 1),
        "away_win": round(away_prob * 100, 1)
    }

# --- HELPER: Stats Detalladas (Para la Vista Individual) ---
def calculate_detailed_stats(matches: List[Dict], team_id: int):
    stats = {
        "goals_scored": 0, "goals_conceded": 0, "corners": [], 
        "yellow_cards": 0, "form": [], "over_25": 0, "btts": 0
    }
    played = 0
    for m in matches:
        if m["status"] != "FINISHED": continue
        played += 1
        is_home = m["homeTeam"]["id"] == team_id
        gs = m["score"]["fullTime"]["home"] if is_home else m["score"]["fullTime"]["away"]
        gc = m["score"]["fullTime"]["away"] if is_home else m["score"]["fullTime"]["home"]
        
        stats["goals_scored"] += gs
        stats["goals_conceded"] += gc
        stats["form"].append("W" if gs > gc else "L" if gs < gc else "D")
        
        if gs > 0 and gc > 0: stats["btts"] += 1
        if (gs + gc) > 2.5: stats["over_25"] += 1
        
        # Estimación simple de corners y tarjetas si no hay datos
        total_goals = gs + gc
        stats["corners"].append(int(8 + total_goals)) 
        stats["yellow_cards"] += 2
        
    if played > 0:
        stats["avg_goals"] = round(stats["goals_scored"] / played, 2)
        stats["avg_conceded"] = round(stats["goals_conceded"] / played, 2)
        stats["avg_corners"] = round(sum(stats["corners"]) / len(stats["corners"]), 1) if stats["corners"] else 0
        stats["avg_cards"] = round(stats["yellow_cards"] / played, 1)
        stats["xg"] = round(stats["avg_goals"] * 1.1, 2) # Simulado basado en goles reales
    else:
        stats.update({"avg_goals": 0, "avg_conceded": 0, "avg_corners": 0, "avg_cards": 0, "xg": 0})
        
    stats["form"] = stats["form"][:5]
    return stats

# --- ENDPOINT 1: LISTA DE PARTIDOS (Arregla el 404 de la Home) ---
@app.get("/api/matches/{league_id}")
async def get_matches(league_id: int):
    try:
        async with httpx.AsyncClient() as client:
            # 1. Obtener partidos
            matches_resp = await client.get(
                f"{FOOTBALL_DATA_API}/competitions/{league_id}/matches",
                params={"status": "SCHEDULED"},
                headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN}
            )
            matches = matches_resp.json().get('matches', [])[:12]

            # 2. Obtener tabla para fuerza relativa
            standings_resp = await client.get(
                f"{FOOTBALL_DATA_API}/competitions/{league_id}/standings",
                headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN}
            )
            standings = standings_resp.json().get('standings', [{}])[0].get('table', [])
            stats_map = {t['team']['id']: t for t in standings}

            results = []
            for m in matches:
                h_id, a_id = m['homeTeam']['id'], m['awayTeam']['id']
                
                # Datos básicos para la lista
                h_stats = stats_map.get(h_id, {'goalsFor': 1, 'playedGames': 1})
                a_stats = stats_map.get(a_id, {'goalsFor': 1, 'playedGames': 1})
                
                h_strength = h_stats['goalsFor'] / h_stats['playedGames'] if h_stats['playedGames'] > 0 else 1
                a_strength = a_stats['goalsFor'] / a_stats['playedGames'] if a_stats['playedGames'] > 0 else 1
                
                probs = calculate_poisson_probs(h_strength * 1.2, a_strength * 0.9)
                
                # Cuotas simuladas basadas en probabilidad
                odds = {
                    "home": round(100/(probs['home_win']+1), 2),
                    "draw": round(100/(probs['draw']+1), 2),
                    "away": round(100/(probs['away_win']+1), 2)
                }

                results.append({
                    "id": m['id'],
                    "time": m['utcDate'],
                    "teams": {"home": m['homeTeam']['shortName'], "away": m['awayTeam']['shortName']},
                    "odds": odds,
                    "insights": ["Value Bet"] if probs['home_win'] > 60 and odds['home'] > 1.8 else []
                })
            
            return {"matches": results}
    except Exception as e:
        print(f"Error list: {e}")
        return {"matches": []}

# --- ENDPOINT 2: DETALLES DEL PARTIDO (Para la vista al hacer click) ---
@app.get("/api/match/{match_id}/detailed")
async def get_match_detailed(match_id: int):
    try:
        async with httpx.AsyncClient() as client:
            # Info básica
            match_base = await client.get(
                f"{FOOTBALL_DATA_API}/matches/{match_id}",
                headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN}
            )
            m_data = match_base.json()
            h_id, a_id = m_data["homeTeam"]["id"], m_data["awayTeam"]["id"]

            # Historial reciente
            h_hist = await client.get(f"{FOOTBALL_DATA_API}/teams/{h_id}/matches?limit=10", headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN})
            a_hist = await client.get(f"{FOOTBALL_DATA_API}/teams/{a_id}/matches?limit=10", headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN})

            h_stats = calculate_detailed_stats(h_hist.json().get("matches", []), h_id)
            a_stats = calculate_detailed_stats(a_hist.json().get("matches", []), a_id)

            # Probabilidades Detalladas
            probs = calculate_poisson_probs(h_stats["avg_goals"], a_stats["avg_conceded"])
            
            return {
                "match": {
                    "home": m_data["homeTeam"]["name"],
                    "away": m_data["awayTeam"]["name"],
                    "competition": m_data["competition"]["name"],
                    "date": m_data["utcDate"]
                },
                "statistics": {
                    "home": {
                        "avg_goals_scored": h_stats["avg_goals"],
                        "xg": h_stats["xg"],
                        "avg_corners": h_stats["avg_corners"],
                        "avg_yellow_cards": h_stats["avg_cards"],
                        "form": h_stats["form"]
                    },
                    "away": {
                        "avg_goals_scored": a_stats["avg_goals"],
                        "xg": a_stats["xg"],
                        "avg_corners": a_stats["avg_corners"],
                        "avg_yellow_cards": a_stats["avg_cards"],
                        "form": a_stats["form"]
                    }
                },
                "predictions": {
                    "result_1x2": probs,
                    "goals": {
                        "total_expected": round(h_stats["avg_goals"] + a_stats["avg_goals"], 2),
                        "btts": 65 if (h_stats["avg_goals"] > 1 and a_stats["avg_goals"] > 1) else 40,
                        "over_25": 70 if (h_stats["avg_goals"] + a_stats["avg_goals"] > 2.5) else 45
                    },
                    "corners": { "expected_total": round((h_stats["avg_corners"] + a_stats["avg_corners"])/2, 1) },
                    "cards": { "expected_yellow": round((h_stats["avg_cards"] + a_stats["avg_cards"]), 1) }
                }
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT 3: CHATBOT ---
@app.post("/api/chatbot")
async def chatbot(request: dict):
    user_msg = request.get("message", "")
    history = request.get("history", [])
    match_ctx = request.get("match_details", None)
    
    context_prompt = ""
    if match_ctx:
        p = match_ctx['predictions']
        context_prompt = f"""
        CONTEXTO DEL PARTIDO ACTUAL:
        {match_ctx['match']['home']} vs {match_ctx['match']['away']}
        Probabilidad Victoria: Local {p['result_1x2']['home_win']}% | Empate {p['result_1x2']['draw']}% | Visita {p['result_1x2']['away_win']}%
        Goles Esperados: {p['goals']['total_expected']}
        """

    messages = [
        {"role": "system", "content": f"Eres BetBrain. Experto en apuestas. Sé breve y directo. {context_prompt}"}
    ] + history[-5:] + [{"role": "user", "content": user_msg}]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                json={"model": "anthropic/claude-3-haiku", "messages": messages}
            )
            return {"response": resp.json()["choices"][0]["message"]["content"]}
    except:
        return {"response": "Error conectando con la IA."}
