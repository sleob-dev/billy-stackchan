"""Télécommande de Billy pour le réseau local (pour un widget de bureau, une page web, un script…).

Toutes les routes demandent l'en-tête « Authorization: Bearer <jeton> », sauf /audio/ (Billy y
télécharge ses sons). Le service ne garde que l'empreinte SHA-256 du jeton (BILLY_COMMANDE_JETON_SHA256,
dans /opt/billy/commande/commande.env) ; le jeton lui-même reste chez le client.

GET  /etat                         -> connecté, en conversation, silence, volume, luminosité, batterie, wifi
POST /silence    {"actif": true}   -> ne pas déranger : volume à 0 (l'ancien est remis à la sortie),
                                      et le fichier « silence » que billy-notif consulte
POST /dire       {"texte": "..."}  -> Billy dit la phrase (Piper, message « notify » du firmware 1.6.2)
POST /volume     {"valeur": 0-100}
POST /luminosite {"valeur": 0-100}
POST /danse      {"morceau": "hasard|boogie|disco|turbo", "mouvements": "tous|lumieres|aucun"}
POST /visage     {"style": "A|D|E|auto"} -> {"visage": choix, "affiche": style affiché} (501 sans l'outil du firmware)
GET  /audio/x.ogg                  -> sons préparés pour Billy

Hors conversation seulement : en pleine conversation, Billy ignore « notify », donc /dire et
/danse répondent 409. Tout passe par l'API de la passerelle MQTT (mqtt-gateway/api-billy.js).

Lancement : venv-voix/bin/python serveur.py [--port 5006]
"""

import argparse
import hashlib
import hmac
import importlib.util
import io
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

ICI = Path(__file__).resolve().parent
AUDIO_DIR = ICI / "audio"
SILENCE = ICI / "silence"  # présent = ne pas déranger (JSON : depuis, volume d'avant)
TEXTE_MAX = 300
PAS_PAR_MESSAGE = 90
STYLES = ("A", "D", "E", "auto")
# Outil du firmware maison qui change de visage (patches-stackchan/visage-style.patch). Un firmware
# qui ne l'expose pas fait répondre 501 à /visage.
OUTIL_VISAGE = "self.billy.set_style"
LISTE_OUTILS_TTL = 600  # s : la liste des outils ne change qu'avec le firmware

_args: argparse.Namespace
_empreinte = b""
_danse = None  # module billy_danse (chorégraphies), chargé au démarrage
_lock = threading.Lock()
_occupe = False  # une phrase ou une danse est en cours
_telecharge = {}  # nom du son -> threading.Event, levé quand Billy l'a téléchargé
_outils = (0.0, frozenset())  # (instant de lecture, noms des outils MCP de Billy)


class Refus(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def post_json(url: str, data: dict, timeout: float = 10) -> dict:
    """POST JSON ; une erreur de la passerelle revient comme {"erreur": ...} au lieu d'une exception."""
    request = urllib.request.Request(url, json.dumps(data).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}") or {"erreur": f"HTTP {e.code}"}
        except ValueError:
            return {"erreur": f"HTTP {e.code}"}


def appareil() -> dict | None:
    with urllib.request.urlopen(f"{_args.passerelle}/appareils", timeout=5) as response:
        robots = json.loads(response.read() or b"[]")
    return robots[0] if robots else None


def outil(nom: str, arguments: dict) -> dict:
    """Appelle un outil MCP du firmware et renvoie son résultat décodé.

    La passerelle répond parfois « Connection closed » à un premier appel (vu le 7/11/2026,
    juste après une conversation) : on réessaie alors une fois.
    """
    for essai in range(2):
        rep = post_json(f"{_args.passerelle}/mcp", {"method": "tools/call",
                                                     "params": {"name": nom, "arguments": arguments}})
        if rep.get("erreur") != "Connection closed":
            break
        time.sleep(0.3)
    if "erreur" in rep:
        raise Refus(502, rep["erreur"])
    res = rep.get("resultat", {})
    if res.get("isError"):
        raise Refus(502, "Billy a refusé la commande")
    texte = (res.get("content") or [{}])[0].get("text", "")
    try:
        return json.loads(texte)
    except ValueError:
        return {"texte": texte}


def envoyer(message: dict) -> None:
    rep = post_json(f"{_args.passerelle}/envoyer", {"message": message})
    if "erreur" in rep:
        raise Refus(502, rep["erreur"])


def silence() -> dict | None:
    try:
        return json.loads(SILENCE.read_text())
    except (OSError, ValueError):
        return None


def billy_libre() -> None:
    robot = appareil()
    if not robot:
        raise Refus(503, "Billy n'est pas connecté")
    if robot["conversation"]:
        raise Refus(409, "Billy est en conversation")
    if silence():
        raise Refus(409, "Billy est en mode silence")


def en_ogg(pcm: np.ndarray, rate: int, chemin: Path) -> None:
    AUDIO_DIR.mkdir(exist_ok=True)
    tmp = chemin.with_suffix(".tmp.ogg")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1",
                    "-i", "pipe:0", "-ar", "16000", "-c:a", "libopus", "-b:a", "32k", str(tmp)],
                   input=(pcm.clip(-1, 1) * 32767).astype(np.int16).tobytes(), check=True)
    tmp.replace(chemin)


def son_phrase(texte: str) -> str:
    """Phrase dite par Piper (même voix que les conversations), synthétisée une fois (cache)."""
    params = {"voice": _args.voix, **json.loads(_args.reglages)}
    nom = hashlib.sha1(f"{json.dumps(params, sort_keys=True)}|{texte}".encode()).hexdigest()[:16] + ".ogg"
    chemin = AUDIO_DIR / nom
    if not chemin.exists():
        request = urllib.request.Request(f"{_args.piper}/tts", json.dumps({"text": texte, **params}).encode(),
                                         {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            with wave.open(io.BytesIO(response.read())) as w:
                rate = w.getframerate()
                pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        en_ogg(pcm, rate, chemin)
    return nom


def son_danse(cle: str) -> str:
    """Musique de la danse (mp3 du serveur XiaoZhi) convertie en Ogg Opus pour « notify »."""
    nom = f"danse-{cle}-v2.ogg"
    chemin = AUDIO_DIR / nom
    if not chemin.exists():
        AUDIO_DIR.mkdir(exist_ok=True)
        tmp = chemin.with_suffix(".tmp.ogg")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(Path(_args.musiques) / f"{cle}-v2.mp3"),
                        "-ar", "16000", "-ac", "1", "-c:a", "libopus", "-b:a", "32k", str(tmp)], check=True)
        tmp.replace(chemin)
    return nom


def jouer(nom: str, texte: str) -> threading.Event:
    """Envoie le son à Billy ; l'événement se lève quand il commence à le télécharger (et à le lire)."""
    evenement = threading.Event()
    _telecharge[nom] = evenement
    envoyer({"type": "notify", "audio_url": f"{_args.public}/audio/{nom}",
             "subtitles": [{"start_ms": 0, "text": texte}] if texte else []})
    return evenement


def occuper() -> None:
    global _occupe
    with _lock:
        if _occupe:
            raise Refus(409, "Billy est déjà en train de parler ou de danser")
        _occupe = True


def liberer_apres(secondes: float) -> None:
    def fin():
        global _occupe
        time.sleep(secondes)
        with _lock:
            _occupe = False
    threading.Thread(target=fin, daemon=True).start()


def duree_ogg(chemin: Path) -> float:
    sortie = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                             str(chemin)], capture_output=True, text=True)
    try:
        return float(sortie.stdout.strip())
    except ValueError:
        return 10.0


# --- Commandes ---------------------------------------------------------------------------------

def outils_billy(relire: bool = False) -> frozenset:
    """Noms des outils MCP du firmware, relus toutes les 10 min (vide si Billy ne répond pas)."""
    global _outils
    if relire or time.monotonic() - _outils[0] > LISTE_OUTILS_TTL or not _outils[1]:
        rep = post_json(f"{_args.passerelle}/mcp", {"method": "tools/list", "params": {}})
        noms = frozenset(t.get("name") for t in rep.get("resultat", {}).get("tools", []))
        if noms:
            _outils = (time.monotonic(), noms)
    return _outils[1]


def cmd_etat(_req: dict) -> dict:
    robot = appareil()
    etat = {"connecte": bool(robot), "conversation": bool(robot and robot["conversation"]),
            "silence": bool(silence()), "occupe": _occupe, "visage_disponible": False}
    if robot:
        etat["visage_disponible"] = OUTIL_VISAGE in outils_billy()
        if "self.billy.get_style" in outils_billy():  # {"style": affiché A|D|E, "choix": A|D|E|auto}
            style = outil("self.billy.get_style", {})
            etat.update({"visage": style.get("choix"), "visage_affiche": style.get("style")})
        statut = outil("self.get_device_status", {})
        etat.update({"volume": statut.get("audio_speaker", {}).get("volume"),
                     "luminosite": statut.get("screen", {}).get("brightness"),
                     "batterie": statut.get("battery", {}).get("level"),
                     "en_charge": statut.get("battery", {}).get("charging"),
                     "wifi": statut.get("network", {}).get("signal")})
        if silence():
            etat["volume_avant_silence"] = silence().get("volume")
    return etat


def cmd_silence(req: dict) -> dict:
    actif = req.get("actif")
    if not isinstance(actif, bool):
        raise Refus(400, "actif doit valoir true ou false")
    deja = silence()
    if actif and not deja:
        volume = outil("self.get_device_status", {}).get("audio_speaker", {}).get("volume", 60)
        SILENCE.write_text(json.dumps({"depuis": time.strftime("%Y-%m-%dT%H:%M:%S"), "volume": volume}))
        outil("self.audio_speaker.set_volume", {"volume": 0})
    elif not actif and deja:
        outil("self.audio_speaker.set_volume", {"volume": int(deja.get("volume") or 60)})
        SILENCE.unlink(missing_ok=True)
    return {"silence": actif}


def cmd_reglage(req: dict, outil_nom: str, cle: str) -> dict:
    valeur = req.get("valeur")
    if not isinstance(valeur, int) or isinstance(valeur, bool) or not 0 <= valeur <= 100:
        raise Refus(400, "valeur doit être un entier de 0 à 100")
    if outil_nom.endswith("set_volume") and silence():
        # En silence, on retient le volume voulu pour la sortie, sans réveiller le haut-parleur.
        etat = silence()
        etat["volume"] = valeur
        SILENCE.write_text(json.dumps(etat))
        return {"volume": valeur, "silence": True}
    outil(outil_nom, {cle: valeur})
    return {cle.replace("brightness", "luminosite"): valeur}


def cmd_dire(req: dict) -> dict:
    texte = " ".join(str(req.get("texte", "")).split())[:TEXTE_MAX]
    if not texte:
        raise Refus(400, "texte vide")
    billy_libre()
    occuper()
    try:
        nom = son_phrase(texte)
        jouer(nom, texte)
    except Exception:
        liberer_apres(0)
        raise
    liberer_apres(duree_ogg(AUDIO_DIR / nom) + 1)
    return {"dit": texte}


def cmd_danse(req: dict) -> dict:
    morceau = req.get("morceau", "hasard")
    mouvements = req.get("mouvements", "tous")
    if morceau not in ("hasard", *_danse.MORCEAUX) or mouvements not in ("tous", "lumieres", "aucun"):
        raise Refus(400, "morceau ou mouvements inconnu")
    cle = random.choice(list(_danse.MORCEAUX)) if morceau == "hasard" else morceau
    billy_libre()
    occuper()
    try:
        nom = son_danse(cle)
        pas = _danse.filtrer(_danse.choregraphie(cle), mouvements)
        # Pendant la lecture d'un « notify », Billy ne répond plus aux appels MCP : les pas partent
        # donc avant la musique, décalés du temps qu'il met à la lancer (--decalage-danse).
        envoyer_pas([{**p, "t_ms": p["t_ms"] + _args.decalage_danse} for p in pas])
        depart = time.monotonic()
        telecharge = jouer(nom, "")
    except Exception:
        liberer_apres(0)
        raise
    threading.Thread(target=mesurer_depart, args=(telecharge, depart), daemon=True).start()
    liberer_apres(duree_ogg(AUDIO_DIR / nom) + 2)
    titre = _danse.MORCEAUX[cle]["titre"]
    print(f"[commande] danse {cle} ({mouvements}), {len(pas)} pas", flush=True)
    return {"danse": cle, "titre": titre, "mouvements": mouvements}


def envoyer_pas(pas: list) -> None:
    try:
        for i in range(0, len(pas), PAS_PAR_MESSAGE):
            outil("self.robot.play_moves", {"moves": json.dumps(pas[i:i + PAS_PAR_MESSAGE], separators=(",", ":")),
                                            "append": i > 0})
    except (OSError, Refus) as e:  # la musique part quand même
        print(f"[commande] danse sans mouvements : {e}", flush=True)


def mesurer_depart(telecharge: threading.Event, depart: float) -> None:
    """Journalise le délai entre l'envoi de la musique et son téléchargement, pour régler le décalage."""
    if telecharge.wait(10):
        print(f"[commande] musique demandée par Billy {1000 * (time.monotonic() - depart):.0f} ms après l'envoi "
              f"(pas décalés de {_args.decalage_danse} ms)", flush=True)
    else:
        print("[commande] danse : Billy n'a pas téléchargé la musique", flush=True)


def cmd_visage(req: dict) -> dict:
    if req.get("style") not in STYLES:
        raise Refus(400, "style : A, D, E ou auto")
    if not appareil():
        raise Refus(503, "Billy n'est pas connecté")
    if OUTIL_VISAGE not in outils_billy() and OUTIL_VISAGE not in outils_billy(relire=True):  # flash récent ?
        raise Refus(501, "le firmware de Billy ne sait pas encore changer de visage à la demande")
    rep = outil(OUTIL_VISAGE, {"style": req["style"]})  # {"style": affiché A|D|E, "choix": demandé}
    return {"visage": rep.get("choix", req["style"]), "affiche": rep.get("style")}


ROUTES = {
    ("GET", "/etat"): cmd_etat,
    ("POST", "/silence"): cmd_silence,
    ("POST", "/dire"): cmd_dire,
    ("POST", "/volume"): lambda r: cmd_reglage(r, "self.audio_speaker.set_volume", "volume"),
    ("POST", "/luminosite"): lambda r: cmd_reglage(r, "self.screen.set_brightness", "brightness"),
    ("POST", "/danse"): cmd_danse,
    ("POST", "/visage"): cmd_visage,
}


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, data) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _autorise(self) -> bool:
        entete = self.headers.get("Authorization", "")
        jeton = entete[7:].strip() if entete.startswith("Bearer ") else ""
        return bool(jeton) and hmac.compare_digest(hashlib.sha256(jeton.encode()).hexdigest().encode(), _empreinte)

    def _audio(self) -> bool:
        match = re.fullmatch(r"/audio/([0-9a-z-]{1,40}\.ogg)", self.path)
        if not match or not (AUDIO_DIR / match.group(1)).exists():
            return False
        body = (AUDIO_DIR / match.group(1)).read_bytes()
        # Billy lit le son au fil du téléchargement : la lecture commence avec la requête, et
        # l'envoi ne se termine qu'à la fin du morceau.
        evenement = _telecharge.pop(match.group(1), None)
        if evenement:
            evenement.set()
        self.send_response(200)
        self.send_header("Content-Type", "audio/ogg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _route(self, methode: str) -> None:
        if methode == "GET" and self.path.startswith("/audio/"):
            print(f"[commande] {self.client_address[0]} télécharge {self.path}", flush=True)
            if not self._audio():
                self._json(404, {"erreur": "introuvable"})
            return
        commande = ROUTES.get((methode, self.path))
        if not commande:
            return self._json(404, {"erreur": "introuvable"})
        if not self._autorise():
            return self._json(401, {"erreur": "jeton manquant ou faux"})
        try:
            req = {}
            if methode == "POST":
                longueur = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(min(longueur, 10_000)) or b"{}")
                if not isinstance(req, dict):
                    raise ValueError
        except ValueError:
            return self._json(400, {"erreur": "JSON invalide"})
        try:
            self._json(200, commande(req))
        except Refus as e:
            self._json(e.status, {"erreur": str(e)})
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"[commande] {self.path} : {e}", flush=True)
            self._json(502, {"erreur": "Billy ou un service du serveur ne répond pas"})

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def log_message(self, fmt, *args) -> None:
        pass


def charger_danse(chemin: str):
    """Charge billy_danse.py (le plugin de danse du serveur XiaoZhi) pour ses chorégraphies.

    Le plugin importe des modules du serveur XiaoZhi dont seules les conversations ont besoin : on
    les remplace par des coquilles vides, ce qui évite de recopier les chorégraphies ici.
    """
    for nom in ("core", "core.providers", "core.providers.tools", "core.providers.tools.device_mcp",
                "core.providers.tools.device_mcp.mcp_handler", "core.providers.tts", "core.providers.tts.dto",
                "core.providers.tts.dto.dto", "plugins_func", "plugins_func.register"):
        sys.modules.setdefault(nom, types.ModuleType(nom))
    vide = lambda *a, **k: None  # noqa: E731
    sys.modules["core.providers.tools.device_mcp.mcp_handler"].call_mcp_tool = vide
    dto = sys.modules["core.providers.tts.dto.dto"]
    dto.ContentType = dto.SentenceType = dto.TTSMessageDTO = object
    reg = sys.modules["plugins_func.register"]
    reg.Action = reg.ActionResponse = reg.ToolType = types.SimpleNamespace(SYSTEM_CTL=None, RESPONSE=None,
                                                                         RECORD=None)
    reg.register_function = lambda *a, **k: (lambda f: f)
    spec = importlib.util.spec_from_file_location("billy_danse", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    global _args, _empreinte, _danse
    xiaozhi = "/opt/billy/xiaozhi-esp32-server/main/xiaozhi-server"
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5006)
    parser.add_argument("--public", default=f"http://{os.environ.get('BILLY_SERVEUR', '127.0.0.1')}:5006",
                        help="adresse vue par Billy (par défaut BILLY_SERVEUR)")
    parser.add_argument("--passerelle", default="http://127.0.0.1:8885")
    parser.add_argument("--piper", default="http://127.0.0.1:5002")
    parser.add_argument("--voix", default=os.environ.get("BILLY_PIPER_VOIX", "fr_FR-siwis-medium"))
    parser.add_argument("--reglages", default=json.dumps({
                            "speed": float(os.environ.get("BILLY_PIPER_VITESSE", 1.0)),
                            "noise": float(os.environ.get("BILLY_PIPER_EXPRESSIVITE", 0.667)),
                            "noise_w": float(os.environ.get("BILLY_PIPER_RYTHME", 0.8)),
                            "pitch": float(os.environ.get("BILLY_PIPER_HAUTEUR", 1.0))}),
                        help="réglages Piper en JSON (par défaut ceux du .env, comme le TTS de la config XiaoZhi)")
    parser.add_argument("--danse", default=f"{xiaozhi}/plugins_func/functions/billy_danse.py")
    parser.add_argument("--musiques", default=f"{xiaozhi}/data/danse")
    parser.add_argument("--decalage-danse", type=int, default=800,
                        help="ms d'avance des pas sur la musique (temps que met Billy à lancer le son)")
    _args = parser.parse_args()
    _empreinte = os.environ.get("BILLY_COMMANDE_JETON_SHA256", "").strip().lower().encode()
    if len(_empreinte) != 64:
        sys.exit("BILLY_COMMANDE_JETON_SHA256 manquant (empreinte SHA-256 du jeton de la télécommande)")
    _danse = charger_danse(_args.danse)
    print(f"Télécommande de Billy sur http://{_args.host}:{_args.port}", flush=True)
    ThreadingHTTPServer((_args.host, _args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
