"""
BetBrain IA - Backend API v4.0 (Groq Integration)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
from datetime import datetime
from scipy.stats import poisson
import os

app = FastAPI(title="BetBrain IA API", version="4.0.0")

# Permitir conexiones desde cualquier lugar (Vercel, Localhost, etc)
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
ODDS_API = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = "924468b00a5d3be11b7549d7741f9157"

# TU API KEY DE GROQ
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

LEAGUE_MAPPING = {
    2021: "soccer_epl", 2014: "soccer_spain_la_liga",
    2019: "soccer_italy_serie_a", 2002: "soccer_germany_bundesliga",
    2015: "soccer_france_ligue_one"
}

cache = {}

# --- MOTOR MATEMÁTICO ---
def calculate_poisson_probs(home_avg, away_avg):
    home_prob, draw_prob, away_prob = 0, 0, 0
    over_2_5_prob, btts_prob = 0, 0
    
    for h in range(6):
        for a in range(6):
            prob = poisson.pmf(h, home_avg) * poisson.pmf(a, away_avg)
            if h > a: home_prob += prob
            elif h == a: draw_prob += prob
            else: away_prob += prob
            if (h + a) > 2.5: over_2_5_prob += prob
            if h > 0 and a > 0: btts_prob += prob

    return {
        "home_win": round(home_prob * 100, 1),
        "draw": round(draw_prob * 100, 1),
        "away_win": round(away_prob * 100, 1),
        "over_2_5": round(over_2_5_prob * 100, 1),
        "btts": round(btts_prob * 100, 1),
        "xg_home": round(home_avg, 2),
        "xg_away": round(away_avg, 2)
    }

def detect_value_bet(probs, odds):
    insights = []
    # Valor Local
    implied_home = (1 / odds['home']) * 100
    if probs['home_win'] > implied_home + 5:
        ev = (probs['home_win']/100 * odds['home']) - 1
        insights.append(f"💎 VALOR: Local (EV {ev:.2f})")
    
    # Alta probabilidad de goles
    if probs['over_2_5'] > 60:
        insights.append(f"🔥 GOLES: +60% probabilidad de Over 2.5")
        
    return insights

@app.get("/")
def read_root():
    return {"status": "online", "service": "BetBrain Backend"}

@app.get("/api/matches/{league_id}")
async def get_matches(league_id: int):
    cache_key = f"matches_{league_id}"
    # Cache simple de 5 minutos
    if cache_key in cache and (datetime.now() - cache[cache_key][1]).seconds < 300:
        return cache[cache_key][0]

    try:
        async with httpx.AsyncClient() as client:
            # 1. Datos del partido
            matches_resp = await client.get(
                f"{FOOTBALL_DATA_API}/competitions/{league_id}/matches",
                params={"status": "SCHEDULED"},
                headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN}
            )
            matches = matches_resp.json().get('matches', [])[:10]

            # 2. Datos de la tabla (para calcular fuerza)
            standings_resp = await client.get(
                f"{FOOTBALL_DATA_API}/competitions/{league_id}/standings",
                headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN}
            )
            standings = standings_resp.json().get('standings', [{}])[0].get('table', [])
            stats_map = {t['team']['id']: t for t in standings}

            analyzed_matches = []
            for m in matches:
                h_id, a_id = m['homeTeam']['id'], m['awayTeam']['id']
                h_stats = stats_map.get(h_id, {'goalsFor': 1, 'playedGames': 1})
                a_stats = stats_map.get(a_id, {'goalsFor': 1, 'playedGames': 1})

                # Cálculo de xG básico
                h_strength = h_stats['goalsFor'] / h_stats['playedGames'] if h_stats['playedGames'] > 0 else 1.2
                a_strength = a_stats['goalsFor'] / a_stats['playedGames'] if a_stats['playedGames'] > 0 else 1.0
                
                # Ajuste factor localía
                probs = calculate_poisson_probs(h_strength * 1.15, a_strength * 0.85)
                
                # Cuotas simuladas (Idealmente conectarías The Odds API aquí)
                # Usamos estimadas basadas en probabilidad para que la UI no se vea vacía
                odds = {
                    "home": round(100 / (probs['home_win'] + 5), 2),
                    "draw": round(100 / (probs['draw'] + 5), 2),
                    "away": round(100 / (probs['away_win'] + 5), 2)
                }

                analyzed_matches.append({
                    "id": m['id'],
                    "teams": {"home": m['homeTeam']['shortName'], "away": m['awayTeam']['shortName']},
                    "time": m['utcDate'],
                    "odds": odds,
                    "probabilities": probs,
                    "insights": detect_value_bet(probs, odds)
                })

            result = {"matches": analyzed_matches}
            cache[cache_key] = (result, datetime.now())
            return result
    except Exception as e:
        print(f"Error: {e}")
        return {"matches": []}

@app.post("/api/chatbot")
async def chatbot(request: dict):
    user_msg = request.get("message", "")
    matches_ctx = request.get("matches", [])
    history = request.get("history", [])

    # Crear contexto resumido para la IA
    context = "DATOS EN TIEMPO REAL:\n"
    for m in matches_ctx[:5]: # Solo los primeros 5 para no saturar
        context += f"- {m['teams']['home']} vs {m['teams']['away']}: Local {m['probabilities']['home_win']}%, Over2.5 {m['probabilities']['over_2_5']}%\n"

    system_prompt = f"""Eres BetBrain, un analista de apuestas experto y sarcástico. 
    Usa estos datos matemáticos para responder: {context}
    Si te piden predicciones, usa los porcentajes. Si piden combinadas, elige las 2 más altas.
    Sé breve y directo."""

    messages = [{"role": "system", "content": system_prompt}]
    # Agregar últimos 3 mensajes del historial
    for msg in history[-3:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_msg})

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama3-70b-8192", # Modelo muy potente y rápido
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 300
                },
                timeout=10.0
            )
            data = response.json()
            return {"response": data['choices'][0]['message']['content']}
    except Exception as e:
        print(f"Error Groq: {e}")
        return {"response": "Mi cerebro de IA está recalentando. Intenta de nuevo en unos segundos."}
