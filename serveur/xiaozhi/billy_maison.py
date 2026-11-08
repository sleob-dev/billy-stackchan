"""Outils « maison » de Billy : exemples d'outils locaux pour le LLM.

À copier dans main/xiaozhi-server/plugins_func/functions/ : le serveur charge tout le dossier,
et chaque outil doit aussi figurer dans « Intent > function_call > functions » de la config.
Chaque outil renvoie un court texte que le LLM reformule à l'oral.

Réglages (variables d'environnement du service billy-xiaozhi, voir .env.example) :
  BILLY_METEO_LAT, BILLY_METEO_LON   coordonnées du lieu de la météo (Open-Meteo, sans clé)
  BILLY_METEO_NOM                    nom du lieu, dit à l'oral
Sans BILLY_METEO_LAT et BILLY_METEO_LON, l'outil météo répond qu'il n'est pas réglé.

Pour ajouter vos propres outils (transports, agenda, domotique…), copiez le modèle de
maison_meteo : une description JSON, une fonction décorée par register_function, et une
réponse courte par repondre().
"""

import json
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime

from plugins_func.register import Action, ActionResponse, ToolType, register_function

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
PIPER = os.environ.get("BILLY_PIPER_URL", "http://127.0.0.1:5002")
VOIX = os.environ.get("BILLY_VOIX_URL", "http://127.0.0.1:8005")

# Codes météo WMO utilisés par Open-Meteo
CODES = {0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux", 3: "couvert",
         45: "brouillard", 48: "brouillard givrant", 51: "bruine légère", 53: "bruine",
         55: "bruine forte", 56: "bruine verglaçante", 57: "bruine verglaçante",
         61: "pluie faible", 63: "pluie", 65: "forte pluie", 66: "pluie verglaçante",
         67: "pluie verglaçante", 71: "neige faible", 73: "neige", 75: "forte neige",
         77: "grains de neige", 80: "averses", 81: "averses", 82: "fortes averses",
         85: "averses de neige", 86: "averses de neige", 95: "orage", 96: "orage avec grêle",
         99: "orage avec grêle"}


def repondre(texte: str) -> ActionResponse:
    return ActionResponse(Action.REQLLM, texte, None)


# --- Météo (Open-Meteo) -----------------------------------------------------------------

def lieu() -> dict | None:
    lat, lon = os.environ.get("BILLY_METEO_LAT"), os.environ.get("BILLY_METEO_LON")
    if not lat or not lon:
        return None
    return {"lat": float(lat), "lon": float(lon), "nom": os.environ.get("BILLY_METEO_NOM", "ici")}


def lire_meteo(position: dict) -> dict:
    """Prévisions heure par heure sur 7 jours, au format {heures, series: {source: {...}}}."""
    requete = urllib.parse.urlencode({
        "latitude": position["lat"], "longitude": position["lon"], "timezone": "auto",
        "forecast_days": 7, "timeformat": "unixtime",
        "hourly": "temperature_2m,precipitation,precipitation_probability,"
                  "wind_speed_10m,wind_gusts_10m,weather_code"})
    with urllib.request.urlopen(f"{OPEN_METEO}?{requete}", timeout=20) as reponse:
        h = json.loads(reponse.read())["hourly"]
    return {"heures": h["time"], "series": {"open-meteo": {
        "temp": h["temperature_2m"], "pluie": h["precipitation"],
        "prob": h["precipitation_probability"], "vent": h["wind_speed_10m"],
        "rafale": h["wind_gusts_10m"], "code": h["weather_code"]}}}


def mediane(valeurs):
    valeurs = [v for v in valeurs if v is not None]
    return statistics.median(valeurs) if valeurs else None


def resume_meteo(d: dict, debut: int, fin: int) -> dict:
    """Médiane des sources heure par heure, puis bilan de la période [debut, fin[."""
    series = list(d["series"].values())
    indices = [i for i, t in enumerate(d["heures"]) if debut <= t < fin]
    if not indices:
        return {}

    def med(cle, i):
        return mediane(s.get(cle, [None] * len(d["heures"]))[i] for s in series if s.get(cle))

    temps = [med("temp", i) for i in indices]
    pluie = [med("pluie", i) or 0 for i in indices]
    probs = [p for p in (med("prob", i) for i in indices) if p is not None]
    vents = [v for v in (med("vent", i) for i in indices) if v is not None]
    rafales = [v for v in (med("rafale", i) for i in indices) if v is not None]
    codes = Counter(c for i in indices for s in series
                    if s.get("code") and (c := s["code"][i]) is not None
                    and 7 <= datetime.fromtimestamp(d["heures"][i]).hour <= 20)
    pluvieuses = [datetime.fromtimestamp(d["heures"][i]).hour
                  for i, p in zip(indices, pluie) if p >= 0.3]
    temps = [t for t in temps if t is not None]
    return {
        "temperature_min": round(min(temps)) if temps else None,
        "temperature_max": round(max(temps)) if temps else None,
        "pluie_mm": round(sum(pluie), 1),
        "risque_pluie_pourcent": round(max(probs)) if probs else None,
        "heures_de_pluie": f"{pluvieuses[0]} h à {pluvieuses[-1] + 1} h" if pluvieuses else "aucune",
        "vent_max_kmh": round(max(vents)) if vents else None,
        "rafales_max_kmh": round(max(rafales)) if rafales else None,
        "temps_dominant": CODES.get(codes.most_common(1)[0][0], "inconnu") if codes else None,
    }


meteo_desc = {"type": "function", "function": {
    "name": "maison_meteo",
    "description": ("Météo du lieu réglé sur le serveur (prévisions Open-Meteo). À utiliser pour toute "
                    "question de météo, de pluie, de température, de vent, de parapluie ou de linge à "
                    "étendre, aujourd'hui, ce soir, demain ou cette semaine."),
    "parameters": {"type": "object", "properties": {
        "periode": {"type": "string", "enum": ["maintenant", "aujourd'hui", "demain", "semaine"],
                    "description": "Période voulue ; « aujourd'hui » couvre le reste de la journée."}},
        "required": []}}}


@register_function("maison_meteo", meteo_desc, ToolType.WAIT)
def maison_meteo(periode: str = "aujourd'hui"):
    position = lieu()
    if position is None:
        return repondre("La météo n'est pas réglée sur le serveur (BILLY_METEO_LAT et BILLY_METEO_LON).")
    try:
        d = lire_meteo(position)
    except (OSError, ValueError, KeyError) as e:
        return repondre(f"Météo indisponible : {e}")
    maintenant = int(datetime.now().timestamp())
    minuit = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    jour = 86400
    resultat = {"lieu": position["nom"]}
    if periode == "maintenant":
        resultat["maintenant"] = resume_meteo(d, maintenant - 1800, maintenant + 1800)
        resultat["prochaines_3_heures"] = resume_meteo(d, maintenant, maintenant + 3 * 3600)
    elif periode == "demain":
        resultat["demain"] = resume_meteo(d, minuit + jour, minuit + 2 * jour)
    elif periode == "semaine":
        noms = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        for n in range(7):
            debut = minuit + n * jour
            nom = noms[datetime.fromtimestamp(debut).weekday()]
            resultat[nom if n else "aujourd'hui"] = resume_meteo(d, debut, debut + jour)
    else:
        resultat["reste_de_la_journee"] = resume_meteo(d, maintenant, minuit + jour)
    return repondre("Météo (à résumer en une ou deux phrases, sans tout lire) : "
                    + json.dumps(resultat, ensure_ascii=False))


# --- Prononciation (serveur Piper) ------------------------------------------------------

prononciation_desc = {"type": "function", "function": {
    "name": "maison_prononciation",
    "description": ("Apprend au robot à prononcer un mot ou un sigle. À utiliser seulement quand on "
                    "le demande explicitement, par exemple « prononce Wi-Fi comme oui-fi » ou "
                    "« on dit USB u-esse-bé ». « dit » est écrit comme il se prononce en français."),
    "parameters": {"type": "object", "properties": {
        "ecrit": {"type": "string", "description": "Le mot tel qu'il est écrit."},
        "dit": {"type": "string", "description": "Sa prononciation, écrite phonétiquement en français."}},
        "required": ["ecrit", "dit"]}}}


@register_function("maison_prononciation", prononciation_desc, ToolType.WAIT)
def maison_prononciation(ecrit: str, dit: str):
    corps = json.dumps({"ecrit": ecrit, "dit": dit}).encode()
    requete = urllib.request.Request(PIPER + "/prononciation", corps, {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(requete, timeout=5).close()
    except OSError as e:
        return repondre(f"Je n'ai pas pu l'enregistrer : {e}")
    return repondre(f"C'est noté : « {ecrit} » se dira désormais « {dit} ». Confirme-le en une phrase.")


# --- Empreintes vocales (service billy-voix) --------------------------------------------

voix_desc = {"type": "function", "function": {
    "name": "maison_voix",
    "description": ("Apprend ou oublie la voix d'une personne, pour que le robot sache qui lui parle. "
                    "« retenir » utilise la phrase que la personne vient de dire : à appeler quand "
                    "quelqu'un dit par exemple « retiens ma voix, je suis Alex » ou « c'est moi, Sam, "
                    "apprends ma voix ». « oublier » efface la voix. « liste » dit qui le robot connaît."),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["retenir", "oublier", "liste"]},
        "nom": {"type": "string", "description": "Prénom de la personne (pas pour « liste »)."}},
        "required": ["action"]}}}


@register_function("maison_voix", voix_desc, ToolType.WAIT)
def maison_voix(action: str, nom: str = ""):
    try:
        if action == "liste":
            with urllib.request.urlopen(VOIX + "/personnes", timeout=5) as r:
                personnes = json.loads(r.read())
            if not personnes:
                return repondre("Le robot ne connaît encore aucune voix.")
            return repondre("Voix connues : " + ", ".join(f"{n} ({k} échantillon{'s' if k > 1 else ''})"
                                                          for n, k in personnes.items()))
        corps = json.dumps({"nom": nom}).encode()
        requete = urllib.request.Request(VOIX + ("/apprendre" if action == "retenir" else "/oublier"), corps,
                                         {"Content-Type": "application/json"})
        with urllib.request.urlopen(requete, timeout=5) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return repondre("La phrase était trop courte pour retenir la voix : demande de redire une phrase "
                            "plus longue, puis réessaie.")
        return repondre(f"Impossible : {e}")
    except (OSError, ValueError) as e:
        return repondre(f"Service des voix indisponible : {e}")
    if action == "oublier":
        return repondre(f"La voix de {nom} est oubliée.")
    n = res.get("echantillons", 1)
    conseil = (" Pour bien la reconnaître, demande-lui de te parler encore et de redire « retiens ma voix » "
               "deux ou trois fois, à d'autres moments.") if n < 3 else ""
    return repondre(f"Voix de {nom} retenue ({n} échantillon{'s' if n > 1 else ''}).{conseil}")
