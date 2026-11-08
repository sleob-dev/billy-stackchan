"""Alertes de notifications pour Billy : un autre programme (démon de notifications, domotique…) les envoie ici.

POST /notif        -> {"alerte": "minuteur", "couleur": "00FF40", "intensite": 1.0}
                      même format que le serveur Piper du simulateur (piper/serveur.py)
GET  /audio/x.ogg  -> phrase d'alerte, que Billy télécharge pour la jouer
GET  /salut.ogg    -> salut dit par Piper (BILLY_SALUT, par défaut « Salut ! ») : le firmware de Billy le joue quand quelqu'un
                      s'approche (« Il te regarde », au plus une fois par heure)

Billy allume ses LEDs de la couleur de l'appli pendant 10 s et regarde devant lui. Si
l'intensité envoyée est d'au moins 0,5, il prévient aussi :
- mode « bulle » (défaut) : message « alert », soit une bulle de parole, un visage surpris et
  un petit son, effacés au bout de 10 s (les rappels, eux, attendaient qu'on touche OK) ;
- mode « voix » : la phrase dite par Piper (en Ogg Opus), par le message « notify », que le
  firmware ne connaît qu'à partir de la version 1.6.2 ;
- mode « bips » : un son R2-D2 court (moins d'une seconde, propre à chaque appli) par le message
  « notify », avec le texte de l'alerte en bulle pendant la lecture (firmware maison 1.6.2).
En silence (fichier /opt/billy/commande/silence posé par billy-commande, un JSON {"depuis", "volume"}),
les alertes ne font rien du tout, ni LEDs, ni son, ni bulle, et /salut.ogg répond 503 : juste une ligne
« silence » au journal.
Tout passe par l'API de la passerelle MQTT (mqtt-gateway/api-billy.js), seule à garder une
connexion ouverte avec le robot.

Lancement : venv/bin/python serveur.py [--port 5004] [--public http://192.0.2.10:5004]
"""

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

AUDIO_DIR = Path(__file__).resolve().parent / "audio"
# Exemples : ajoutez vos propres alertes (nom envoyé par POST /notif -> phrase dite)
ALERTES = {"minuteur": "Le minuteur a sonné !", "sonnette": "On sonne à la porte !"}
BIPS = {"minuteur": "happy", "sonnette": "surprised"}  # humeur des bips R2-D2 de chaque alerte
# Mêmes humeurs que le simulateur (MOODS dans simulateur/index.html)
MOODS = {
    "happy": {"freq": (900, 2200), "shapes": ("up", "up", "warble", "beep"), "count": (4, 7)},
    "surprised": {"freq": (600, 1400), "shapes": ("up",), "count": (1, 2), "long": True},
    "sad": {"freq": (350, 900), "shapes": ("down",), "count": (2, 3), "long": True, "wave": "sine"},
    "angry": {"freq": (140, 420), "shapes": ("buzz", "beep", "down"), "count": (3, 5), "wave": "sawtooth"},
    "chatty": {"freq": (500, 2800), "shapes": ("beep", "up", "down", "warble", "beep"), "count": (6, 12)},
    # Son court des alertes (mode « bips ») : 3 ou 4 bips vifs, moins d'une seconde
    "court": {"freq": (900, 2400), "shapes": ("up", "beep", "warble", "up"), "count": (3, 4)},
}
VOLUME_BIPS = 0.08  # son court des alertes : discret, nettement sous la voix (moyenne ≈ −31 dB), réglé à l'oreille
LED_MAX = 168  # plafond conseillé par l'outil self.robot.set_led_color
DUREE = 10  # secondes avant d'éteindre les LEDs et d'effacer la bulle
ATTENTE_MAX = 90  # secondes d'attente au plus de la fin d'une conversation
SILENCE = Path("/opt/billy/commande/silence")  # posé et retiré par billy-commande (serveur/commande/serveur.py)

_args: argparse.Namespace
_last_alert = 0.0
_lock = threading.Lock()


def post_json(url: str, data: dict, timeout: float = 10) -> dict:
    request = urllib.request.Request(url, json.dumps(data).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def call_tool(name: str, arguments: dict) -> None:
    post_json(f"{_args.passerelle}/mcp", {"method": "tools/call",
                                          "params": {"name": name, "arguments": arguments}})


def send(message: dict) -> None:
    """Pousse un message JSON du protocole XiaoZhi à Billy par la passerelle MQTT."""
    post_json(f"{_args.passerelle}/envoyer", {"message": message})


def set_leds(color: str) -> None:
    r, g, b = (int(color[i:i + 2], 16) * LED_MAX // 255 for i in (0, 2, 4))
    call_tool("self.robot.set_led_color", {"red": r, "green": g, "blue": b})


def wait_until_idle() -> bool:
    """Billy ignore les notifications sonores en pleine conversation : on attend qu'elle finisse."""
    deadline = time.monotonic() + ATTENTE_MAX
    while time.monotonic() < deadline:
        with urllib.request.urlopen(f"{_args.passerelle}/appareils", timeout=5) as response:
            robots = json.loads(response.read() or b"[]")
        if robots and not robots[0]["conversation"]:
            return True
        time.sleep(3)
    return False


def r2d2(rate: int, mood: str, seed: str) -> np.ndarray:
    """Bips et sifflements façon R2-D2, comme dans le simulateur (beepPhrase), en float32.

    La graine dépend de l'appli : chaque alerte garde la même « signature » sonore.
    """
    rng = np.random.default_rng(int(hashlib.sha1(seed.encode()).hexdigest()[:8], 16))
    m = MOODS[mood]
    out = [np.zeros(int(0.03 * rate), np.float32)]
    for _ in range(int(rng.integers(m["count"][0], m["count"][1] + 1))):
        shape = rng.choice(m["shapes"])
        dur = rng.uniform(0.25, 0.45) if m.get("long") else rng.uniform(0.05, 0.16)
        f0 = rng.uniform(*m["freq"])
        t = np.arange(int(dur * rate)) / rate
        freq = np.full_like(t, f0)
        if shape == "up":
            freq = f0 * (min(4200, f0 * rng.uniform(1.6, 2.6)) / f0) ** (t / dur)
        elif shape == "down":
            freq = f0 * rng.uniform(0.4, 0.65) ** (t / dur)
        elif shape in ("warble", "buzz"):  # modulation rapide de la fréquence
            lfo = rng.uniform(45, 70) if shape == "buzz" else rng.uniform(16, 32)
            freq = f0 + f0 * (0.5 if shape == "buzz" else 0.25) * np.sin(2 * np.pi * lfo * t)
        phase = 2 * np.pi * np.cumsum(freq) / rate
        wave = m.get("wave") or ("square" if rng.random() < 0.3 else "sine")
        if wave == "sine":
            signal, peak = np.sin(phase), 0.35
        elif wave == "square":
            signal, peak = np.sign(np.sin(phase)), 0.1
        else:  # dent de scie
            signal, peak = 2 * ((phase / (2 * np.pi)) % 1) - 1, 0.1
        env = np.minimum(1, np.minimum(t / 0.008, (dur - t) / 0.015).clip(0))
        out.append((signal * env * peak * 2.2).astype(np.float32))  # ×2,2 : petit haut-parleur
        out.append(np.zeros(int(rng.uniform(0.015, 0.07) * rate), np.float32))
    out.append(np.zeros(int(0.12 * rate), np.float32))
    return np.concatenate(out)


def voix_audio(text: str) -> str:
    """Phrase dite par Piper seule (sans bips), synthétisée une fois (cache) ; renvoie le nom du Ogg."""
    voice_params = {"voice": _args.voix, **json.loads(_args.reglages)}
    filename = hashlib.sha1(f"voix|{json.dumps(voice_params, sort_keys=True)}|{text}".encode()).hexdigest()[:16] + ".ogg"
    path = AUDIO_DIR / filename
    if not path.exists():
        request = urllib.request.Request(f"{_args.piper}/tts", json.dumps(
            {"text": text, **voice_params}).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            wav = response.read()
        AUDIO_DIR.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp.ogg")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", "pipe:0", "-ar", "16000", "-ac", "1",
                        "-c:a", "libopus", "-b:a", "32k", str(tmp)], input=wav, check=True)
        tmp.replace(path)
    return filename


def phrase_audio(name: str, text: str) -> str:
    """Bips R2-D2 puis la phrase dite par Piper, synthétisés une fois (cache) ; renvoie le nom du Ogg."""
    mood = BIPS.get(name, "happy")
    voice_params = {"voice": _args.voix, **json.loads(_args.reglages)}
    key = f"{json.dumps(voice_params, sort_keys=True)}|{mood}|{name}|{text}"
    filename = hashlib.sha1(key.encode()).hexdigest()[:16] + ".ogg"
    path = AUDIO_DIR / filename
    if not path.exists():
        request = urllib.request.Request(f"{_args.piper}/tts", json.dumps(
            {"text": text, **voice_params}).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            with wave.open(io.BytesIO(response.read())) as w:
                rate = w.getframerate()
                voice = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        pcm = np.concatenate([r2d2(rate, mood, name), voice]).clip(-1, 1)
        AUDIO_DIR.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp.ogg")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1",
                        "-i", "pipe:0", "-ar", "16000", "-c:a", "libopus", "-b:a", "32k", str(tmp)],
                       input=(pcm * 32767).astype(np.int16).tobytes(), check=True)
        tmp.replace(path)
    return filename


def bips_audio(name: str) -> str:
    """Son R2-D2 court de l'appli, synthétisé une fois (cache) ; renvoie le nom du Ogg."""
    filename = hashlib.sha1(f"court|{name}|{VOLUME_BIPS}".encode()).hexdigest()[:16] + ".ogg"
    path = AUDIO_DIR / filename
    if not path.exists():
        rate = 16000
        pcm = (r2d2(rate, "court", name) * VOLUME_BIPS).clip(-1, 1)
        AUDIO_DIR.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp.ogg")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1",
                        "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", str(tmp)],
                       input=(pcm * 32767).astype(np.int16).tobytes(), check=True)
        tmp.replace(path)
    return filename


def en_silence() -> bool:
    return SILENCE.exists()


def alert(name: str, color: str, level: float) -> None:
    global _last_alert
    if en_silence():
        print(f"[notif] {name} : silence", flush=True)
        return
    with _lock:
        _last_alert = stamp = time.monotonic()
    text = ALERTES.get(name, f"Nouvelle notification {name}")
    try:
        set_leds(color)
        call_tool("self.robot.set_head_angles", {"yaw": 0, "pitch": 0, "speed": 150})
        if level < 0.5:  # intensité basse (le soir, par exemple) : Billy reste muet
            pass
        elif _args.mode == "bulle":
            # Message « alert » : bulle de parole, visage surpris et petit son (firmware 1.6.0).
            send({"type": "alert", "status": name, "message": text, "emotion": "surprised"})
        elif _args.mode == "bips":
            # Son R2-D2 court par « notify », texte en bulle pendant la lecture, visage surpris
            audio = bips_audio(name)
            if not wait_until_idle():
                print(f"[notif] {name} : Billy est resté en conversation, son abandonné", flush=True)
                return
            send({"type": "llm", "emotion": "surprised"})
            send({"type": "notify", "audio_url": f"{_args.public}/audio/{audio}",
                  "subtitles": [{"start_ms": 0, "text": text}]})
        else:
            audio = phrase_audio(name, text)
            if not wait_until_idle():
                print(f"[notif] {name} : Billy est resté en conversation, son abandonné", flush=True)
                return
            send({"type": "notify", "audio_url": f"{_args.public}/audio/{audio}",
                  "subtitles": [{"start_ms": 0, "text": text}]})
        print(f"[notif] {name} ({level:.2f}) : {text}", flush=True)
    except (OSError, subprocess.CalledProcessError, ValueError) as e:
        print(f"[notif] {name} : échec ({e})", flush=True)
        return
    time.sleep(DUREE)
    with _lock:
        if _last_alert != stamp:
            return  # une autre alerte a pris le relais
    try:
        call_tool("self.robot.set_led_color", {"red": 0, "green": 0, "blue": 0})
        if level >= 0.5 and _args.mode == "bips":
            send({"type": "llm", "emotion": "neutral"})
        if level >= 0.5 and _args.mode == "bulle":
            # Une phrase vide efface la bulle sans son, puis le visage redevient neutre.
            send({"type": "tts", "state": "sentence_start", "text": ""})
            send({"type": "llm", "emotion": "neutral"})
    except OSError:
        pass


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, data) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/salut.ogg":  # « Il te regarde » (firmware maison)
            if en_silence():
                print("[notif] salut : silence", flush=True)
                return self._json(503, {"erreur": "silence"})
            try:
                nom = voix_audio(os.environ.get("BILLY_SALUT", "Salut !"))
            except (OSError, subprocess.CalledProcessError, ValueError) as e:
                print(f"[notif] salut : échec ({e})", flush=True)
                return self._json(500, {"erreur": "synthèse impossible"})
        else:
            match = re.fullmatch(r"/audio/([0-9a-f]{16}\.ogg)", self.path)
            if not match or not (AUDIO_DIR / match.group(1)).exists():
                return self._json(404, {"erreur": "introuvable"})
            nom = match.group(1)
        body = (AUDIO_DIR / nom).read_bytes()
        print(f"[notif] son {self.path} ({nom}) téléchargé par {self.client_address[0]}", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "audio/ogg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/notif":
            return self._json(404, {"erreur": "introuvable"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(min(length, 10_000)) or b"{}")
            name = str(req.get("alerte", "")).strip()[:40]
            color = str(req.get("couleur", "FFFFFF"))
            level = min(1.0, max(0.0, float(req.get("intensite", 1.0))))
        except (ValueError, TypeError):
            return self._json(400, {"erreur": "requête invalide"})
        if not name or not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
            return self._json(400, {"erreur": "alerte ou couleur invalide"})
        threading.Thread(target=alert, args=(name, color, level), daemon=True).start()
        self._json(202, {"recu": name})

    def log_message(self, fmt, *args) -> None:
        pass  # les alertes sont journalisées par alert()


def main() -> None:
    global _args
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5004)
    parser.add_argument("--public", default=f"http://{os.environ.get('BILLY_SERVEUR', '127.0.0.1')}:5004",
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
    parser.add_argument("--mode", choices=("bulle", "voix", "bips"), default="bulle",
                        help="bulle : bulle et son (firmware 1.6.0) ; voix : bips et phrase dite (notify, 1.6.2) ; "
                             "bips : son R2-D2 court et bulle (notify, 1.6.2)")
    _args = parser.parse_args()
    print(f"Alertes de Billy sur http://{_args.host}:{_args.port}", flush=True)
    ThreadingHTTPServer((_args.host, _args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
