"""Banc d'essai Whisper pour Billy : temps et erreurs de mots sur des phrases dites par Piper.

À lancer sur le serveur (Piper doit tourner sur 127.0.0.1:5002), modèles téléchargés à part :
  HF_HOME=/tmp/banc-whisper/hf nice /opt/billy/venv-voix/bin/python -I banc_whisper.py base small:th=4
Chaque argument : modèle[:ct=int8][:th=fils][:beam=1][:nots=1]. Effacer /tmp/banc-whisper ensuite.
Mesures du 6 novembre 2026 (N150) : small ≈ 3,0 s, base ≈ 1,1 s (5 % d'erreurs), tiny ≈ 0,7 s (14 %).
"""
import json, os, re, sys, time, unicodedata, urllib.request
from pathlib import Path
from faster_whisper import WhisperModel

DOSSIER = Path("/tmp/banc-whisper"); DOSSIER.mkdir(exist_ok=True)
PHRASES = ["Oui.", "Bonne nuit !", "À quoi tu sers ?", "Raconte-moi une blague.",
           "Lève la tête au maximum.", "Quelle heure est-il à Tokyo ?",
           "Non, je voudrais savoir la météo pour demain.",
           "C'est quoi la principale actualité dans le monde ?",
           "Est-ce que tu peux nous prendre une photo ?",
           "Baisse la tête, tu n'es pas revenu à un état normal, tu regardes le plafond."]

def wav(i, texte):
    f = DOSSIER / f"p{i}.wav"
    if not f.exists():
        corps = json.dumps({"text": texte, "voice": "fr_FR-tom-medium", "speed": 1.0}).encode()
        req = urllib.request.Request("http://127.0.0.1:5002/tts", corps, {"Content-Type": "application/json"})
        f.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    return f

def mots(t):
    t = unicodedata.normalize("NFC", t.lower()).replace("’", "'")
    return re.findall(r"[\w']+", t)

def wer(ref, hyp):
    r, h = mots(ref), mots(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        p, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            p, d[j] = d[j], min(d[j] + 1, d[j-1] + 1, p + (r[i-1] != h[j-1]))
    return d[len(h)], len(r)

fichiers = [wav(i, t) for i, t in enumerate(PHRASES)]
for spec in sys.argv[1:]:
    nom, *opts = spec.split(":")
    o = dict(x.split("=") for x in opts)
    m = WhisperModel(nom, device="cpu", compute_type=o.get("ct", "int8"), cpu_threads=int(o.get("th", 0)))
    m.transcribe(str(fichiers[0]), language="fr")  # chauffe
    total, err, nmots, pire = 0.0, 0, 0, 0.0
    lignes = []
    for f, ref in zip(fichiers, PHRASES):
        t0 = time.monotonic()
        seg, _ = m.transcribe(str(f), language="fr", beam_size=int(o.get("beam", 1)), condition_on_previous_text=False,
                              without_timestamps=o.get("nots") == "1")
        txt = "".join(s.text for s in seg).strip()
        dt = time.monotonic() - t0; total += dt; pire = max(pire, dt)
        e, n = wer(ref, txt); err += e; nmots += n
        lignes.append(f"    {dt:4.1f} s  {e}/{n}  {txt}")
    print(f"{spec:28} moyenne {total/len(PHRASES):.2f} s, pire {pire:.2f} s, erreurs {err}/{nmots} mots ({100*err/nmots:.0f} %)", flush=True)
    print("\n".join(lignes), flush=True)
    del m
