"""Petit serveur de synthèse vocale Piper pour le simulateur StackChan.

GET  /        -> le simulateur (même origine : pas de souci de CORS)
GET  /voices  -> liste des voix disponibles dans le dossier voix/
POST /tts     -> {"text": "...", "voice": "fr_FR-siwis-medium", "speaker": 0, "speed": 1.0,
                  "noise": 0.667, "noise_w": 0.8, "pitch": 1.0}  (noise, noise_w et pitch facultatifs)
                 renvoie un fichier WAV
                 (le texte passe d'abord par prononciation.py : « 18 h 53 », dictionnaire…)
POST /prononciation -> {"ecrit": "Wi-Fi", "dit": "oui-fi"}  ajoute au dictionnaire
POST /notif   -> {"alerte": "minuteur", "couleur": "00FF40", "intensite": 1.0}
                 alerte de notification (envoyée par un démon de notifications), relayée au simulateur
GET  /events  -> flux SSE des alertes, écouté par le simulateur

Lancement : .venv\\Scripts\\python.exe serveur.py [--host 127.0.0.1] [--port 5002]
"""

import argparse
import io
import json
import queue
import re
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from piper import PiperVoice, SynthesisConfig

from prononciation import apprendre, prononcer

VOICES_DIR = Path(__file__).resolve().parent / "voix"
SIMULATOR = Path(__file__).resolve().parents[2] / "simulateur" / "index.html"
MAX_TEXT = 1000

_loaded: dict[str, PiperVoice] = {}
_lock = threading.Lock()

_subscribers: list[queue.Queue] = []
_subscribers_lock = threading.Lock()


def broadcast(event: dict) -> int:
    """Envoie l'événement à chaque simulateur connecté ; renvoie leur nombre."""
    with _subscribers_lock:
        for q in _subscribers:
            q.put(event)
        return len(_subscribers)


def list_voices() -> list[dict]:
    voices = []
    for config_path in sorted(VOICES_DIR.glob("*.onnx.json")):
        model_id = config_path.name.removesuffix(".onnx.json")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        speakers = sorted(config.get("speaker_id_map", {}).items(), key=lambda kv: kv[1])
        voices.append({
            "id": model_id,
            "name": model_id.split("-")[1].capitalize(),
            "speakers": [name for name, _ in speakers],
        })
    return voices


def get_voice(model_id: str) -> PiperVoice:
    if model_id not in {v["id"] for v in list_voices()}:
        raise ValueError(f"voix inconnue : {model_id}")
    if model_id not in _loaded:
        _loaded[model_id] = PiperVoice.load(VOICES_DIR / f"{model_id}.onnx")
    return _loaded[model_id]


def raise_pitch(wav: bytes, pitch: float) -> bytes:
    """Annonce une fréquence d'échantillonnage multipliée par « pitch » : le son est lu plus vite
    et plus aigu, exactement comme la « Hauteur » du simulateur (playbackRate)."""
    if pitch == 1.0:
        return wav
    with wave.open(io.BytesIO(wav)) as src:
        params, frames = src.getparams(), src.readframes(src.getnframes())
    buf = io.BytesIO()
    with wave.open(buf, "wb") as dst:
        dst.setparams(params._replace(framerate=round(params.framerate * pitch)))
        dst.writeframes(frames)
    return buf.getvalue()


def synthesize(text: str, model_id: str, speaker: int | None, speed: float,
               noise: float | None = None, noise_w: float | None = None) -> bytes:
    """noise = expressivité de l'intonation, noise_w = variation du rythme (None : valeurs du modèle)."""
    with _lock:
        voice = get_voice(model_id)
        if voice.config.num_speakers <= 1:
            speaker = None
        config = SynthesisConfig(speaker_id=speaker, length_scale=1.0 / speed,
                                 noise_scale=noise, noise_w_scale=noise_w)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            voice.synthesize_wav(prononcer(text), wav, syn_config=config)
        return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/voices":
            self._json(200, list_voices())
        elif self.path == "/events":
            self._events()
        elif self.path in ("/", "/index.html"):
            self._send(200, SIMULATOR.read_bytes(), "text/html; charset=utf-8")
        else:
            self._json(404, {"erreur": "introuvable"})

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        q: queue.Queue = queue.Queue()
        with _subscribers_lock:
            _subscribers.append(q)
        try:
            self.wfile.write(b": connecte\n\n")
            self.wfile.flush()
            while True:
                try:
                    chunk = f"data: {json.dumps(q.get(timeout=15))}\n\n"
                except queue.Empty:
                    chunk = ": ping\n\n"  # garde la connexion ouverte et détecte les départs
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except OSError:
            pass  # simulateur fermé
        finally:
            with _subscribers_lock:
                _subscribers.remove(q)

    def _notif(self, req: dict) -> None:
        name = str(req.get("alerte", "")).strip()[:40]
        color = str(req.get("couleur", "FFFFFF"))
        if not name or not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
            return self._json(400, {"erreur": "alerte ou couleur invalide"})
        level = min(1.0, max(0.0, float(req.get("intensite", 1.0))))
        receivers = broadcast({"alerte": name, "couleur": color, "intensite": level})
        self._json(200, {"recepteurs": receivers})

    def do_POST(self) -> None:
        if self.path not in ("/tts", "/notif", "/prononciation"):
            return self._json(404, {"erreur": "introuvable"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(min(length, 10_000)) or b"{}")
            if self.path == "/notif":
                return self._notif(req)
            if self.path == "/prononciation":
                apprendre(str(req.get("ecrit", "")), str(req.get("dit", "")))
                return self._json(200, {"appris": req.get("ecrit")})
            text = str(req.get("text", "")).strip()[:MAX_TEXT]
            if not text:
                return self._json(400, {"erreur": "texte vide"})
            speaker = req.get("speaker")
            speed = min(2.0, max(0.5, float(req.get("speed", 1.0))))
            noise, noise_w = (min(1.5, max(0.0, float(req[k]))) if req.get(k) is not None else None
                              for k in ("noise", "noise_w"))
            pitch = min(2.0, max(0.5, float(req.get("pitch", 1.0))))
            wav = raise_pitch(synthesize(text, str(req.get("voice", "fr_FR-siwis-medium")),
                                         int(speaker) if speaker is not None else None, speed, noise, noise_w),
                              pitch)
        except (ValueError, TypeError) as e:
            return self._json(400, {"erreur": str(e)})
        self._send(200, wav, "audio/wav")

    def log_message(self, fmt, *args) -> None:
        print(f"[piper] {self.address_string()} {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5002)
    args = parser.parse_args()
    names = ", ".join(v["id"] for v in list_voices()) or "aucune"
    print(f"Serveur Piper sur http://{args.host}:{args.port} (voix : {names})")

    def warm_up() -> None:  # charger un modèle prend ~10 s : on le fait au démarrage
        for v in list_voices():
            with _lock:
                get_voice(v["id"])
        print("[piper] voix chargées")

    threading.Thread(target=warm_up, daemon=True).start()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
