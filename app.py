"""
OPTI-BET — Backend Flask / Render
──────────────────────────────────
• Proxy sécurisé pour football-data.org (clé cachée côté serveur)
• Enrichit les données : cotes générées, forme, analyse textuelle
• Route principale : GET /api/matches
• Route santé    : GET /api/health
• CORS activé pour tout appel front-end

Déploiement Render (free) :
  1. Push ce dossier sur GitHub
  2. New Web Service → connecter le repo
  3. Build : pip install -r requirements.txt
  4. Start : gunicorn app:app
  5. Ajouter la variable d'env FOOTBALL_API_KEY dans Render
"""

import os
import random
import time
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, jsonify
from flask_cors import CORS

# ──────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────
app = Flask(__name__)
CORS(app)  # Autorise tous les origines (nécessaire pour appel depuis HTML statique)

FOOTBALL_API_KEY  = os.environ.get("FOOTBALL_API_KEY", "e692da5b77664c579c31cfcc9e30c4e7")
FOOTBALL_API_BASE = "https://api.football-data.org/v4"
REQUEST_HEADERS   = {"X-Auth-Token": FOOTBALL_API_KEY}

COMPETITIONS = [
    {"code": "PL",  "label": "Angleterre - Premier League", "logo": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    {"code": "FL1", "label": "France - Ligue 1",            "logo": "🇫🇷"},
    {"code": "BL1", "label": "Allemagne - Bundesliga",      "logo": "🇩🇪"},
    {"code": "PD",  "label": "Espagne - La Liga",           "logo": "🇪🇸"},
    {"code": "SA",  "label": "Italie - Serie A",            "logo": "🇮🇹"},
]

# ──────────────────────────────────────────────
#  HELPERS — GÉNÉRATION DE DONNÉES
# ──────────────────────────────────────────────

def random_form() -> list:
    """Génère 5 résultats aléatoires V(4) / N(3) / D(1)."""
    return [
        4 if r < 0.45 else (3 if r < 0.70 else 1)
        for r in [random.random() for _ in range(5)]
    ]


def generate_odds(home_rank: int, away_rank: int, total: int = 20) -> dict:
    """Calcule des cotes cohérentes à partir du classement."""
    h   = (total - home_rank + 1) / total
    a   = (total - away_rank + 1) / total
    tot = h + a
    p1  = h / tot
    p2  = a / tot
    return {
        "win1":   round(max(1.20, min(8.00, 1 / (p1 * 0.88))), 2),
        "draw":   round(random.uniform(3.00, 4.20), 2),
        "win2":   round(max(1.20, min(8.00, 1 / (p2 * 0.88))), 2),
        "over25": round(random.uniform(1.45, 2.25), 2),
        "btts":   round(random.uniform(1.40, 2.00), 2),
    }


def generate_analysis(team1: str, team2: str, hr: int, ar: int) -> str:
    if hr <= 3:
        return f"{team1} est dans le top 3 — favori logique à domicile."
    if ar <= 3:
        return f"{team2} performe excellemment cette saison malgré le déplacement."
    if hr <= ar:
        return f"{team1} part légèrement favori grâce à l'avantage du terrain."
    return f"{team2} a une meilleure position au classement — match ouvert en perspective."


# ──────────────────────────────────────────────
#  HELPERS — FORMATAGE DE DATE (fuseau Abidjan UTC+0)
# ──────────────────────────────────────────────

def get_raw_date(utc_str: str, status: str) -> str:
    if status in ("IN_PLAY", "PAUSED"):
        return "today"
    d   = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = (d.date() - now.date()).days
    if diff == 0:
        return "today"
    if diff > 0:
        return "tomorrow"
    return "past"


def format_date(utc_str: str, status: str) -> str:
    d   = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
    hh  = d.strftime("%H:%M")
    if status in ("IN_PLAY", "PAUSED"):
        return f"🔴 LIVE — {hh}"
    if status == "FINISHED":
        return "Terminé"
    raw = get_raw_date(utc_str, status)
    if raw == "today":
        return f"Aujourd'hui — {hh}"
    if raw == "tomorrow":
        return f"Demain — {hh}"
    days_fr   = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
    months_fr = ["jan.", "fév.", "mar.", "avr.", "mai", "juin",
                 "juil.", "août", "sep.", "oct.", "nov.", "déc."]
    return f"{days_fr[d.weekday()]} {d.day} {months_fr[d.month - 1]} — {hh}"


# ──────────────────────────────────────────────
#  HELPERS — APPELS football-data.org
# ──────────────────────────────────────────────

def fetch_standings(comp_code: str) -> dict:
    """Retourne {team_name: position} pour une compétition."""
    try:
        r = requests.get(
            f"{FOOTBALL_API_BASE}/competitions/{comp_code}/standings",
            headers=REQUEST_HEADERS, timeout=12
        )
        if not r.ok:
            return {}
        data = r.json()
        standings = {}
        for table in data.get("standings", []):
            if table.get("type") == "TOTAL":
                for row in table.get("table", []):
                    standings[row["team"]["name"]] = row["position"]
        return standings
    except Exception as e:
        print(f"[standings/{comp_code}] {e}")
        return {}


def fetch_comp_matches(comp: dict, standings: dict) -> list:
    """Récupère et enrichit les matchs d'une compétition."""
    today    = datetime.now(timezone.utc)
    date_from = today.strftime("%Y-%m-%d")
    date_to   = (today + timedelta(days=2)).strftime("%Y-%m-%d")
    url = (
        f"{FOOTBALL_API_BASE}/competitions/{comp['code']}/matches"
        f"?dateFrom={date_from}&dateTo={date_to}"
        f"&status=SCHEDULED,LIVE,IN_PLAY,PAUSED,FINISHED"
    )
    try:
        r = requests.get(url, headers=REQUEST_HEADERS, timeout=12)
        # Respect rate-limit header (plan gratuit = 10 req/min)
        remaining = r.headers.get("X-Requests-Available-Minute")
        if remaining and int(remaining) < 3:
            time.sleep(12)
        if not r.ok:
            return []
        matches = r.json().get("matches", [])
    except Exception as e:
        print(f"[matches/{comp['code']}] {e}")
        return []

    result = []
    for idx, m in enumerate(matches):
        home = m["homeTeam"]
        away = m["awayTeam"]
        status   = m.get("status", "SCHEDULED")
        home_name = home.get("name", "Équipe A")
        away_name = away.get("name", "Équipe B")
        home_short = home.get("shortName") or home_name
        away_short = away.get("shortName") or away_name

        hr = standings.get(home_name, random.randint(3, 17))
        ar = standings.get(away_name, random.randint(3, 17))

        is_live     = status in ("IN_PLAY", "PAUSED")
        is_finished = status == "FINISHED"
        score_txt   = ""
        if is_live or is_finished:
            ft = m.get("score", {}).get("fullTime", {})
            score_txt = f" ({ft.get('home', '?')} - {ft.get('away', '?')})"

        probs_win1 = max(10, round(100 / (20 - hr + 2)))
        probs_win2 = max(10, round(100 / (20 - ar + 2)))

        result.append({
            "id":         m.get("id", (idx + 1) * 100),
            "league":     comp["label"],
            "logo":       comp["logo"],
            "team1":      home_short + (score_txt if is_live or is_finished else ""),
            "team2":      away_short,
            "odds":       generate_odds(hr, ar),
            "probs":      {"win1": probs_win1, "draw": 28, "win2": probs_win2},
            "form1":      random_form(),
            "form2":      random_form(),
            "h2h":        f"Classement : {home_short} #{hr} vs {away_short} #{ar}.",
            "attack":     {"t1": max(50, 98 - hr * 3), "t2": max(50, 98 - ar * 3)},
            "analysis":   generate_analysis(home_short, away_short, hr, ar),
            "date":       format_date(m["utcDate"], status),
            "rawDate":    get_raw_date(m["utcDate"], status),
            "isLive":     is_live,
            "isFinished": is_finished,
        })
    return result


# ──────────────────────────────────────────────
#  ROUTES
# ──────────────────────────────────────────────

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "message": "OPTI-BET API is running ✅"})


@app.route("/api/matches")
def get_matches():
    """
    Retourne tous les matchs (aujourd'hui + demain) pour les 5 grands championnats.
    Format JSON compatible avec le front-end OPTI-BET.
    """
    all_matches = []
    standings_map = {}

    # 1) Récupérer les classements en parallèle (facultatif selon rate-limit)
    for comp in COMPETITIONS:
        standings_map[comp["code"]] = fetch_standings(comp["code"])
        time.sleep(0.5)  # anti rate-limit plan gratuit

    # 2) Récupérer les matchs compétition par compétition
    match_id = 1
    for comp in COMPETITIONS:
        matches = fetch_comp_matches(comp, standings_map.get(comp["code"], {}))
        for m in matches:
            m["id"] = match_id
            match_id += 1
        all_matches.extend(matches)
        time.sleep(0.7)  # anti rate-limit

    return jsonify({
        "success": True,
        "count":   len(all_matches),
        "matches": all_matches,
    })


# ──────────────────────────────────────────────
#  ENTRY POINT
# ──────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
