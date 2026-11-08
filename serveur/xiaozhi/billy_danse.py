"""« Billy, danse ! » : musique chiptune composée ici et chorégraphie calée sur le tempo.

À copier dans main/xiaozhi-server/plugins_func/functions/, avec maison_danse dans
« Intent > function_call > functions ». Les morceaux sont générés par le code (pas de droits,
son 8 bits qui passe bien sur le haut-parleur de 1 W), au premier usage, dans data/danse/.
Comme le tempo est connu exactement, chaque pas de danse tombe sur un temps.

Déroulé : on attend que Billy ait fini sa phrase, on glisse le morceau dans la file audio de
la conversation, puis, quand les premières notes partent vers Billy, on lui envoie les pas
avec l'outil self.robot.play_moves (firmware maison), en morceaux de 90 pas au plus
(limite de 8 Ko de la passerelle MQTT). Sans cet outil, Billy joue seulement la musique.
"""

import asyncio
import json
import os
import random
import subprocess
import time
from pathlib import Path

import numpy as np

from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO
from plugins_func.register import Action, ActionResponse, ToolType, register_function

TAG = __name__
TAUX = 24000  # fréquence de sortie audio du serveur XiaoZhi
DOSSIER = Path("data/danse")
DECALAGE_MS = 350  # retard de l'audio chez Billy (réseau + tampon), à régler à l'oreille
PAS_PAR_MESSAGE = 90
LED_MAX = 168

NOTES = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
MAJEUR, MINEUR = (0, 2, 4, 5, 7, 9, 11), (0, 2, 3, 5, 7, 8, 10)

# Trois morceaux originaux : tonalité, mode, tempo, accords (degrés de la gamme), couleurs
MORCEAUX = {
    "boogie": {"titre": "Robot boogie", "tonique": "C", "mode": MAJEUR, "bpm": 118,
               "accords": (0, 5, 3, 4), "couleurs": ((168, 120, 0), (0, 140, 168), (168, 40, 120))},
    "disco": {"titre": "Disco droïde", "tonique": "A", "mode": MINEUR, "bpm": 126,
              "accords": (0, 5, 2, 6), "couleurs": ((140, 0, 168), (0, 168, 120), (168, 0, 60))},
    "turbo": {"titre": "Turbo pixel", "tonique": "E", "mode": MINEUR, "bpm": 140,
              "accords": (0, 3, 4, 0), "couleurs": ((0, 168, 60), (168, 168, 0), (0, 80, 168))},
}
MESURES = 16  # 16 mesures de 4 temps : 27 à 33 s selon le tempo


def frequence(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def enveloppe(n: int, attaque: float, declin: float) -> np.ndarray:
    t = np.arange(n) / TAUX
    env = np.minimum(1, t / max(attaque, 1e-4))
    return env * np.exp(-t / max(declin, 1e-4))


def pulse(f: float, n: int, rapport: float = 0.25) -> np.ndarray:
    phase = (np.arange(n) * f / TAUX) % 1
    return np.where(phase < rapport, 1.0, -1.0)


def triangle(f: float, n: int) -> np.ndarray:
    phase = (np.arange(n) * f / TAUX) % 1
    return 4 * np.abs(phase - 0.5) - 1


def composer(cle: str) -> np.ndarray:
    """Basse, accords en arpèges, mélodie et batterie 8 bits ; même graine, même morceau."""
    m = MORCEAUX[cle]
    rng = random.Random(cle)
    bruit_blanc = np.random.default_rng(sum(map(ord, cle)))
    temps = 60 / m["bpm"]
    n_temps = MESURES * 4
    total = int((n_temps * temps + 1.0) * TAUX)
    piste = np.zeros(total)
    base = 48 + NOTES[m["tonique"]]  # do 3 environ : la basse reste audible sur un petit haut-parleur
    gamme = m["mode"]

    def note(degre: int, octave: int = 0) -> int:
        return base + 12 * (octave + degre // 7) + gamme[degre % 7]

    def ajoute(debut: float, son: np.ndarray, gain: float) -> None:
        i = int(debut * TAUX)
        piste[i:i + len(son)] += gain * son[: max(0, total - i)]

    # Phrase mélodique de 2 mesures, répétée avec variations
    motif = [rng.choice((0, 2, 4, 4, 7, 6, 2)) for _ in range(8)]
    rythme = [rng.choice((1, 1, 0.5, 0.5, 0)) for _ in range(8)]
    for mesure in range(MESURES):
        accord = m["accords"][mesure % 4]
        section = "intro" if mesure < 4 else "fin" if mesure >= 14 else "corps"
        for t in range(4):
            debut = (mesure * 4 + t) * temps
            # Batterie taillée pour un haut-parleur de 1 W, qui ne rend rien sous 150 Hz environ :
            # grosse caisse « claquante » (balayage 420 → 120 Hz et clic d'attaque) sur chaque temps,
            # caisse claire bien présente sur 2 et 4, charleston sur les contretemps.
            if t in (0, 2) or section == "corps":
                n = int(0.12 * TAUX)
                f = 300 * np.exp(-np.arange(n) / TAUX * 40) + 120
                corps_grosse = np.sin(2 * np.pi * np.cumsum(f) / TAUX) * enveloppe(n, 0.001, 0.05)
                clic = np.diff(bruit_blanc.normal(size=n), prepend=0) * enveloppe(n, 0.0005, 0.004)
                ajoute(debut, corps_grosse + 0.5 * clic, 0.75 if t in (0, 2) else 0.5)
            if t in (1, 3) and section != "intro":
                n = int(0.13 * TAUX)
                bruit = np.diff(bruit_blanc.normal(size=n), prepend=0)
                ton = np.sin(2 * np.pi * 230 * np.arange(n) / TAUX)
                ajoute(debut, (0.8 * bruit + 0.6 * ton) * enveloppe(n, 0.001, 0.05), 0.5)
            n = int(0.035 * TAUX)
            charleston = np.diff(bruit_blanc.normal(size=n), prepend=0) * enveloppe(n, 0.0005, 0.012)
            ajoute(debut + 0.5 * temps, charleston, 0.3 if t == 3 else 0.22)
            # Basse piquée en croches, une octave plus haut et en onde carrée fine pour rester audible
            for demi in (0, 0.5):
                n = int(temps * 0.3 * TAUX)
                hauteur = note(accord, 1) if demi == 0 else note(accord + 4, 0) + 12
                ajoute(debut + demi * temps, pulse(frequence(hauteur), n, 0.125) * enveloppe(n, 0.002, 0.07), 0.22)
            # Arpège de l'accord en doubles croches, discret
            if section != "fin" or mesure == 14:
                for q in range(4):
                    deg = accord + (0, 2, 4, 2)[q]
                    n = int(temps / 4 * 0.9 * TAUX)
                    ajoute(debut + q * temps / 4, pulse(frequence(note(deg, 1)), n, 0.5) * enveloppe(n, 0.002, 0.06), 0.06)
            # Mélodie (à partir de la 5e mesure)
            if section == "corps" or (section == "fin" and mesure == 14):
                i = (mesure % 2) * 4 + t
                if rythme[i]:
                    variation = 2 if mesure >= 10 and t == 3 else 0
                    hauteur = note(accord + motif[i] + variation, 2)
                    for sous in ((0,) if rythme[i] == 1 else (0, 0.5)):
                        n = int(temps * (0.9 if rythme[i] == 1 else 0.45) * TAUX)
                        ajoute(debut + sous * temps, pulse(frequence(hauteur), n, 0.25) * enveloppe(n, 0.004, 0.35), 0.13)
    # Accord final
    fin = n_temps * temps
    n = int(0.9 * TAUX)
    for deg in (0, 2, 4):
        ajoute(fin, pulse(frequence(note(deg, 1)), n, 0.5) * enveloppe(n, 0.003, 0.4), 0.1)
    return 0.8 * piste / (np.max(np.abs(piste)) or 1)


def fichier_musique(cle: str) -> str:
    chemin = DOSSIER / f"{cle}-v2.mp3"  # v2 : batterie audible sur le petit haut-parleur
    if not chemin.exists():
        DOSSIER.mkdir(parents=True, exist_ok=True)
        pcm = (composer(cle) * 32767).astype(np.int16).tobytes()
        tmp = chemin.with_suffix(".tmp.mp3")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(TAUX), "-ac", "1",
                        "-i", "pipe:0", "-b:a", "64k", str(tmp)], input=pcm, check=True)
        tmp.replace(chemin)
    return str(chemin.resolve())


def choregraphie(cle: str) -> list:
    """Un pas par temps : la tête se balance, hoche et tourne, les LEDs changent avec la musique."""
    m = MORCEAUX[cle]
    temps_ms = 60000 / m["bpm"]
    couleurs = m["couleurs"]
    pas = []
    for b in range(MESURES * 4):
        t = round(b * temps_ms)
        mesure, temps = divmod(b, 4)
        c = couleurs[mesure % len(couleurs)]
        if mesure < 4:  # intro : la tête hoche en rythme, LEDs qui pulsent
            p = {"t_ms": t, "yaw": 0, "pitch": 25 if temps % 2 else 5, "speed": 400}
            p.update(zip("rgb", c if temps == 0 else (c[0] // 4, c[1] // 4, c[2] // 4)))
        elif mesure >= 14:  # fin : grands tours, puis retour au centre
            p = {"t_ms": t, "yaw": (60, -60, 60, 0)[temps] if mesure == 14 else 0,
                 "pitch": 10 if mesure == 14 else 0, "speed": 600}
            p.update(zip("rgb", couleurs[temps % len(couleurs)]))
        else:  # corps : balancement gauche-droite, hochement, figure toutes les 4 mesures
            figure = mesure % 4 == 3
            p = {"t_ms": t, "yaw": (35, -35, 50, -50)[temps] if figure else (25, -25)[temps % 2],
                 "pitch": 30 if temps in (1, 3) else 8, "speed": 700 if figure else 500}
            p.update(zip("rgb", c if temps % 2 == 0 else couleurs[(mesure + 1) % len(couleurs)]))
        pas.append(p)
    fin = round(MESURES * 4 * temps_ms)
    pas.append({"t_ms": fin, "yaw": 0, "pitch": 0, "speed": 300, "r": LED_MAX, "g": LED_MAX, "b": LED_MAX})
    pas.append({"t_ms": fin + 1200, "r": 0, "g": 0, "b": 0})
    return pas


async def file_audio_vide(conn, delai_max: float = 15) -> None:
    """Attend que Billy ait fini de dire sa phrase (files de synthèse et d'envoi vides)."""
    fin = time.monotonic() + delai_max
    calme = 0
    while time.monotonic() < fin and calme < 3:
        rc = getattr(conn, "audio_rate_controller", None)
        vide = conn.tts.tts_text_queue.empty() and conn.tts.tts_audio_queue.empty() and (not rc or not rc.queue)
        calme = calme + 1 if vide else 0
        await asyncio.sleep(0.1)


async def envoyer_pas(conn, pas: list) -> None:
    """Attend que la musique parte vers Billy, puis lui confie les pas, par morceaux."""
    rc = getattr(conn, "audio_rate_controller", None)
    fin = time.monotonic() + 10
    while rc is not None and not rc.queue and time.monotonic() < fin:
        await asyncio.sleep(0.02)
    await asyncio.sleep(DECALAGE_MS / 1000)
    try:
        # Le serveur range les outils de Billy sous un nom « nettoyé » (self_robot_play_moves)
        nom = next((n for n, vrai in conn.mcp_client.name_mapping.items() if vrai == "self.robot.play_moves"),
                   "self_robot_play_moves")
        for i in range(0, len(pas), PAS_PAR_MESSAGE):
            args = {"moves": json.dumps(pas[i:i + PAS_PAR_MESSAGE], separators=(",", ":")), "append": i > 0}
            await call_mcp_tool(conn, conn.mcp_client, nom, json.dumps(args), timeout=5)
    except Exception as e:  # outil absent (firmware d'usine) ou Billy injoignable : la musique suffit
        conn.logger.bind(tag=TAG).warning(f"danse sans mouvements : {e}")


danse_desc = {"type": "function", "function": {
    "name": "maison_danse",
    "description": ("Billy danse : il joue un morceau chiptune et bouge la tête et ses lumières en rythme "
                    "(une trentaine de secondes). Morceaux : boogie (Robot boogie, joyeux), disco (Disco "
                    "droïde), turbo (Turbo pixel, rapide). Avant l'appel, dis juste une très courte phrase, "
                    "comme « C'est parti ! »."),
    "parameters": {"type": "object", "properties": {
        "morceau": {"type": "string", "enum": ["hasard", "boogie", "disco", "turbo"]},
        # Option de diagnostic (7/11/2026) : la musique saccade-t-elle à cause des servos ?
        "mouvements": {"type": "string", "enum": ["tous", "lumieres", "aucun"],
                       "description": "tous par défaut ; lumieres si on dit « danse sans bouger » ou "
                                      "« danse sans la tête » ; aucun si on dit « juste la musique »."}},
        "required": []}}}


def filtrer(pas: list, mouvements: str) -> list:
    """lumieres : sans tête ; aucun : rien (la musique seule)."""
    if mouvements == "aucun":
        return []
    if mouvements == "lumieres":
        return [{k: v for k, v in p.items() if k not in ("yaw", "pitch", "speed")} for p in pas
                if any(c in p for c in "rgb")]
    return pas


@register_function("maison_danse", danse_desc, ToolType.SYSTEM_CTL)
async def maison_danse(conn, morceau: str = "hasard", mouvements: str = "tous"):
    cle = morceau if morceau in MORCEAUX else random.choice(list(MORCEAUX))
    try:
        musique = await asyncio.to_thread(fichier_musique, cle)
        await file_audio_vide(conn)
        conn.tts.tts_text_queue.put(TTSMessageDTO(sentence_id=conn.sentence_id, sentence_type=SentenceType.MIDDLE,
                                                  content_type=ContentType.FILE, content_file=musique))
        pas = filtrer(choregraphie(cle), mouvements)
        conn.logger.bind(tag=TAG).info(f"danse {cle}, mouvements {mouvements} : {len(pas)} pas")
        if pas:
            asyncio.create_task(envoyer_pas(conn, pas))
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"danse impossible : {e}")
        return ActionResponse(Action.RESPONSE, result=str(e), response="Oups, mes servos ont le trac.")
    titre = MORCEAUX[cle]["titre"]
    return ActionResponse(Action.RECORD, result=f"Billy danse sur « {titre} »", response=f"Je danse sur {titre} !")
