"""Reconnaissance vocale locale (faster-whisper) au format de l'API OpenAI, pour le serveur XiaoZhi de Billy.

POST /v1/audio/transcriptions  -> multipart avec un champ « file » (wav, mp3, opus…)
                                  renvoie {"text": "..."}
GET  /health                   -> {"modele": "small", "pret": true}

Lancement : venv/bin/python serveur.py [--host 127.0.0.1] [--port 5003] [--modele small] [--langue fr]
"""

import argparse
import json
import tempfile
import threading
import time
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from faster_whisper import WhisperModel

MAX_AUDIO = 25 * 1024 * 1024  # même limite que l'API OpenAI

_model: WhisperModel | None = None
_lock = threading.Lock()
_args: argparse.Namespace


def audio_from_multipart(content_type: str, body: bytes) -> bytes | None:
    """Extrait le champ « file » d'un corps multipart/form-data."""
    message = BytesParser(policy=HTTP).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") == "file":
            return part.get_payload(decode=True)
    return None


def transcribe(audio: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".audio") as f:
        f.write(audio)
        f.flush()
        with _lock:  # un seul calcul à la fois : le processeur est partagé
            segments, _ = _model.transcribe(f.name, language=_args.langue, beam_size=1,
                                            condition_on_previous_text=False)
            return "".join(s.text for s in segments).strip()


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, data) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"modele": _args.modele, "pret": _model is not None})
        else:
            self._json(404, {"erreur": "introuvable"})

    def do_POST(self) -> None:
        if self.path != "/v1/audio/transcriptions":
            return self._json(404, {"erreur": "introuvable"})
        if _model is None:
            return self._json(503, {"erreur": "modèle en cours de chargement"})
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length <= MAX_AUDIO:
            return self._json(400, {"erreur": "audio absent ou trop gros"})
        audio = audio_from_multipart(self.headers.get("Content-Type", ""), self.rfile.read(length))
        if not audio:
            return self._json(400, {"erreur": "champ file manquant"})
        start = time.monotonic()
        try:
            text = transcribe(audio)
        except Exception as e:  # audio illisible ou erreur du modèle : on répond quand même
            print(f"[whisper] échec : {e!r}", flush=True)
            return self._json(500, {"erreur": "transcription impossible"})
        print(f"[whisper] {time.monotonic() - start:.1f} s : {text}", flush=True)
        self._json(200, {"text": text})

    def log_message(self, fmt, *args) -> None:
        pass  # la transcription est déjà journalisée


def main() -> None:
    global _args, _model
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5003)
    parser.add_argument("--modele", default="small")
    parser.add_argument("--langue", default="fr")
    _args = parser.parse_args()
    server = ThreadingHTTPServer((_args.host, _args.port), Handler)
    print(f"Whisper sur http://{_args.host}:{_args.port} (modèle {_args.modele}, langue {_args.langue})", flush=True)
    _model = WhisperModel(_args.modele, device="cpu", compute_type="int8")
    print("[whisper] modèle chargé", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
