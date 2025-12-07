"""
BetBrain IA - Backend API v3.0
Logica: Cálculo de Poisson para probabilidades + Contexto enriquecido para LLM
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from datetime import datetime, timedelta
import math
from scipy.stats import poisson # Importante para cálculos reales
from typing import List, Dict

app = FastAPI(title="BetBrain IA API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración
FOOTBALL_DATA_API = "https://api.football-data.org/v4"
FOOTBALL_DATA_TOKEN = "c8e58951f8eb4904a5bf090c681d5e62"
ODDS_API = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = "924468b00a5d3be11b7549d7741f9157"

LEAGUE_MAPPING = {
    2021: "soccer_epl", 2014: "soccer_spain_la_liga",
    2019: "soccer_italy_serie_a", 2002: "soccer_germany_bundesliga",
    2015: "soccer_france_ligue_one"
}

cache = {}

# --- MOTOR DE PROBABILIDADES ---
def calculate_poisson_probs(home_avg_goals, away_avg_goals):
    """Calcula probabilidades reales usando distribución de Poisson"""
    max_goals = 6
    home_prob = 0
    draw_prob = 0
    away_prob = 0
    over_2_5_prob = 0
    btts_prob = 0
    
    # Matriz de probabilidades exactas
    for h in range(max_goals):
        for a in range(max_goals):
            prob = poisson.pmf(h, home_avg_goals) * poisson.pmf(a, away_avg_goals)
            
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
        "xg_home": round(home_avg_goals, 2),
        "xg_away": round(away_avg_goals, 2)
    }

def detect_value_bet(probs, odds):
    """Detecta si hay valor matemático (Probabilidad implícita < Probabilidad real)"""
    recommendations = []
    
    # Valor en Ganador Local
    implied_prob_home = (1 / odds['home']) * 100
    if probs['home_win'] > implied_prob_home + 5: # 5% de margen de seguridad
        ev = (probs['home_win']/100 * odds['home']) - 1
        recommendations.append(f"💎 VALUE BET: Gana Local (EV: {ev:.2f})")
        
    # Valor en Over 2.5 (asumiendo cuota media 1.90 si no hay dato real)
    if probs['over_2_5'] > 65:
        recommendations.append("🔥 ALTA PROBABILIDAD: Más de 2.5 Goles")
        
    return recommendations

# --- ENDPOINTS ---

@app.get("/api/matches/{league_id}")
async def get_analyzed_matches(league_id: int):
    cache_key = f"analyzed_{league_id}"
    if cache_key in cache and (datetime.now() - cache[cache_key][1]).seconds < 300:
        return cache[cache_key][0]

    async with httpx.AsyncClient() as client:
        # 1. Obtener Partidos
        matches_resp = await client.get(
            f"{FOOTBALL_DATA_API}/competitions/{league_id}/matches",
            params={"status": "SCHEDULED"},
            headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN}
        )
        matches_data = matches_resp.json()

        # 2. Obtener Tabla (para fuerza de ataque/defensa)
        standings_resp = await client.get(
            f"{FOOTBALL_DATA_API}/competitions/{league_id}/standings",
            headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN}
        )
        standings_data = standings_resp.json()
        table = {t['team']['id']: t for t in standings_data['standings'][0]['table']}

        # 3. Obtener Cuotas
        odds_map = {}
        league_key = LEAGUE_MAPPING.get(league_id)
        if league_key:
            odds_resp = await client.get(
                f"{ODDS_API}/sports/{league_key}/odds",
                params={"apiKey": ODDS_API_KEY, "regions": "eu", "markets": "h2h"}
            )
            if odds_resp.status_code == 200:
                for game in odds_resp.json():
                    # Mapeo simple por nombre (se puede mejorar con fuzzy match)
                    odds_map[game['home_team']] = game['bookmakers'][0]['markets'][0]['outcomes']

        processed_matches = []
        for m in matches_data['matches'][:10]: # Analizar prx 10 partidos
            home_id = m['homeTeam']['id']
            away_id = m['awayTeam']['id']
            
            # Datos estadísticos base
            h_stats = table.get(home_id, {'goalsFor': 1, 'playedGames': 1})
            a_stats = table.get(away_id, {'goalsFor': 1, 'playedGames': 1})
            
            # Calcular fuerza de ataque (goles promedio)
            h_attack = h_stats['goalsFor'] / h_stats['playedGames']
            a_attack = a_stats['goalsFor'] / a_stats['playedGames']
            
            # Ajuste local/visita (factor simple)
            lambda_home = h_attack * 1.15
            lambda_away = a_attack * 0.85
            
            # Calcular Probabilidades
            probs = calculate_poisson_probs(lambda_home, lambda_away)
            
            # Asignar cuotas (o default)
            match_odds = {"home": 2.0, "draw": 3.2, "away": 3.5} 
            # (Aquí iría la lógica de mapeo real con odds_map, simplificado para el ejemplo)
            
            # Detectar Valor
            insights = detect_value_bet(probs, match_odds)
            
            processed_matches.append({
                "id": m['id'],
                "teams": {"home": m['homeTeam']['shortName'], "away": m['awayTeam']['shortName']},
                "time": m['utcDate'],
                "odds": match_odds,
                "probabilities": probs,
                "insights": insights,
                "stats": {
                    "home_form": h_stats.get('form', '?????'),
                    "away_form": a_stats.get('form', '?????'),
                    "goals_avg": round((h_attack + a_attack)/2, 2)
                }
            })

        result = {"matches": processed_matches}
        cache[cache_key] = (result, datetime.now())
        return result

@app.post("/api/chatbot")
async def chatbot(request: dict):
    user_msg = request.get("message", "")
    history = request.get("history", [])
    matches = request.get("matches", []) # Contexto completo analizado
    
    # Filtrar solo las mejores oportunidades para el contexto del sistema
    best_bets = []
    for m in matches:
        if m['insights']:
            best_bets.append(f"Partido: {m['teams']['home']} vs {m['teams']['away']} -> {', '.join(m['insights'])}")
    
    context_str = "\n".join(best_bets)
    
    system_prompt = f"""Eres BetBrain, un Handicapper Profesional de Apuestas Deportivas.
    
    TUS DATOS ANALIZADOS (Úsalos como verdad absoluta):
    {context_str}
    
    TU OBJETIVO:
    1. Si piden "seguras" o "fijas", busca en los DATOS ANALIZADOS las que tengan Win% > 60% o Over 2.5 > 65%.
    2. Si piden "combinada", elige las 2 o 3 mejores value bets.
    3. Si piden ganar X dinero, calcula la cuota necesaria (Dinero deseado / Apuesta base).
    4. Sé breve, usa emojis, y justifica con los % calculados (ej: "Recomiendo X porque mi modelo le da un 65% de probabilidad").
    5. NO inventes partidos. Si no hay datos claros, dilo.
    """
    
    # Integración con Anthropic (o tu LLM de preferencia)
    # ... (código httpx similar al anterior pero con este prompt mejorado)
    # Simularemos la respuesta para este ejemplo si no hay API Key real configurada
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": "TU_API_KEY_AQUI", # Pon tu key o usa variable de entorno
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-3-sonnet-20240229",
                    "max_tokens": 1000,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_msg}]
                },
                timeout=20.0
            )
            if response.status_code == 200:
                return {"response": response.json()["content"][0]["text"]}
    except:
        pass

    # Fallback inteligente si falla la LLM o no hay Key
    return {
        "response": f"Basado en mi análisis matemático:\n\n" + 
                    ("\n".join(best_bets[:3]) if best_bets else "No veo ventajas estadísticas claras hoy. Mejor esperar.") +
                    "\n\n¿Quieres que arme una combinada con esto?"
    }
