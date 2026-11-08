"""Empreintes vocales de Billy : qui parle ? Compatible avec le client « voiceprint » de XiaoZhi.

Service local (127.0.0.1:8005), sans base de données : un modèle sherpa-onnx (NVIDIA TitaNet
small, nemo_en_titanet_small.onnx renommé modele.onnx) calcule l'empreinte de chaque phrase,
comparée à celles des personnes apprises (empreintes.json, moyenne de leurs échantillons).
Choisi le 7 novembre 2026 sur des voix Piper : environ 0,8 pour une même voix et 0,4 au plus
entre deux voix, là où WeSpeaker CAM++ confondait les voix (seuil 0,5 dans la config XiaoZhi).

GET  /voiceprint/health?key=K           -> {"status": "healthy"}             (appelé par XiaoZhi)
POST /voiceprint/identify               -> {"speaker_id": "Alex", "score": 0.71}
     (Bearer K ; multipart : file = WAV 16 kHz mono de la phrase, speaker_ids ignoré)
POST /apprendre {"nom": "Sam"}        -> ajoute à Sam l'empreinte de la dernière phrase entendue
POST /oublier   {"nom": "Sam"}        -> efface Sam
GET  /personnes                         -> {"Alex": 3, "Sam": 1}  (nombre d'échantillons)

La dernière phrase est celle que XiaoZhi vient d'envoyer à /identify : quand quelqu'un dit
« Billy, retiens ma voix, je suis Sam », c'est cette phrase-là qui sert d'échantillon.

Lancement : venv/bin/python serveur.py [--port 8005] [--cle billy-local]
"""

import argparse
import io
import json
import threading
import time
import wave
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import sherpa_onnx

ICI = Path(__file__).resolve().parent
EMPREINTES = ICI / "empreintes.json"
MAX_ECHANTILLONS = 10  # par personne : les plus anciens sont remplacés
FRAICHEUR = 120  # secondes pendant lesquelles la dernière phrase peut servir d'échantillon
MIN_SECONDES = 1.0  # une phrase plus courte donne une empreinte peu fiable

_args: argparse.Namespace
_extracteur = None
_verrou = threading.Lock()
_derniere = {"empreinte": None, "quand": 0.0}


def charger() -> dict:
    if not EMPREINTES.exists():
        return {}
    return {nom: [np.array(e, dtype=np.float32) for e in liste]
            for nom, liste in json.loads(EMPREINTES.read_text(encoding="utf-8")).items()}


def enregistrer(personnes: dict) -> None:
    tmp = EMPREINTES.with_suffix(".tmp")
    tmp.write_text(json.dumps({n: [e.round(5).tolist() for e in l] for n, l in personnes.items()}),
                   encoding="utf-8")
    tmp.replace(EMPREINTES)


def empreinte(wav_bytes: bytes):
    with wave.open(io.BytesIO(wav_bytes)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError("WAV 16 bits mono attendu")
        taux = w.getframerate()
        echantillons = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    if len(echantillons) < MIN_SECONDES * taux:
        return None
    with _verrou:
        flux = _extracteur.create_stream()
        flux.accept_waveform(taux, echantillons)
        flux.input_finished()
        v = np.array(_extracteur.compute(flux), dtype=np.float32)
    return v / (np.linalg.norm(v) or 1)


def identifier(v) -> tuple:
    meilleur, score = "", 0.0
    for nom, liste in charger().items():
        moyenne = np.mean(liste, axis=0)
        s = float(np.dot(v, moyenne / (np.linalg.norm(moyenne) or 1)))
        if s > score:
            meilleur, score = nom, s
    return meilleur, score


def fichier_multipart(content_type: str, corps: bytes):
    message = BytesParser(policy=HTTP).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + corps)
    for partie in message.iter_parts():
        if partie.get_param("name", header="content-disposition") == "file":
            return partie.get_payload(decode=True)
    return None


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, data) -> None:
        corps = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _corps(self) -> bytes:
        longueur = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(min(longueur, 10 * 1024 * 1024))

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/voiceprint/health":
            ok = parse_qs(url.query).get("key", [""])[0] == _args.cle
            return self._json(200 if ok else 401, {"status": "healthy" if ok else "cle invalide"})
        if url.path == "/personnes":
            return self._json(200, {n: len(l) for n, l in charger().items()})
        self._json(404, {"erreur": "introuvable"})

    def do_POST(self) -> None:
        if self.path == "/voiceprint/identify":
            if self.headers.get("Authorization", "") != f"Bearer {_args.cle}":
                return self._json(401, {"erreur": "clé invalide"})
            wav = fichier_multipart(self.headers.get("Content-Type", ""), self._corps())
            try:
                v = empreinte(wav) if wav else None
            except (ValueError, wave.Error, EOFError) as e:
                return self._json(400, {"erreur": str(e)})
            if v is None:
                return self._json(200, {"speaker_id": "", "score": 0.0})
            _derniere.update(empreinte=v, quand=time.monotonic())
            nom, score = identifier(v)
            print(f"[voix] {nom or '?'} ({score:.2f})", flush=True)
            return self._json(200, {"speaker_id": nom, "score": round(score, 4)})
        if self.path in ("/apprendre", "/oublier"):
            try:
                nom = " ".join(str(json.loads(self._corps() or b"{}").get("nom", "")).split())[:40]
            except ValueError:
                nom = ""
            if not nom:
                return self._json(400, {"erreur": "nom manquant"})
            personnes = charger()
            if self.path == "/oublier":
                personnes.pop(nom, None)
                enregistrer(personnes)
                return self._json(200, {"oublie": nom})
            if _derniere["empreinte"] is None or time.monotonic() - _derniere["quand"] > FRAICHEUR:
                return self._json(409, {"erreur": "aucune phrase récente assez longue"})
            personnes[nom] = (personnes.get(nom, []) + [_derniere["empreinte"]])[-MAX_ECHANTILLONS:]
            enregistrer(personnes)
            print(f"[voix] échantillon ajouté pour {nom} ({len(personnes[nom])})", flush=True)
            return self._json(200, {"nom": nom, "echantillons": len(personnes[nom])})
        self._json(404, {"erreur": "introuvable"})

    def log_message(self, fmt, *args) -> None:
        pass


def main() -> None:
    global _args, _extracteur
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8005)
    parser.add_argument("--cle", default="billy-local", help="clé partagée avec la config XiaoZhi (service local)")
    parser.add_argument("--modele", default=str(ICI / "modele.onnx"))
    _args = parser.parse_args()
    _extracteur = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=_args.modele, num_threads=2))
    print(f"Empreintes vocales sur http://127.0.0.1:{_args.port} ({len(charger())} personnes)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", _args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
