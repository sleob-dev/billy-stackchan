"""Ce que Piper doit lire autrement qu'il n'est écrit.

1. Le dictionnaire `prononciations.txt` (une ligne « écrit = dit », relu dès qu'il change),
   que l'on enrichit à la main, à la voix (« Billy, prononce Wi-Fi comme oui-fi ») ou par les
   fonctions lister, apprendre et oublier de ce module.
2. Des règles pour ce que Piper lit mal : « 18 h 53 » (lu « hache »), « km/h », « °C », « % »…
"""

import os
import re
import threading
from pathlib import Path

FICHIER = Path(__file__).resolve().parent / "prononciations.txt"
ENTETE = ("# Prononciations de Billy : une ligne « écrit = dit » (majuscules ignorées).\n"
          "# Relu automatiquement ; Billy en ajoute quand on lui dit « prononce X comme Y ».\n")

_cache = {"mtime": None, "regles": []}
_verrou = threading.Lock()


def _heures(m: re.Match) -> str:
    h, minutes = int(m.group(1)), m.group(2)
    mot = "une heure" if h == 1 else ("minuit" if h == 0 else ("midi" if h == 12 else f"{h} heures"))
    return f"{mot} {int(minutes)}" if minutes and int(minutes) else mot


REGLES = [
    (re.compile(r"\b(\d{1,2}) ?[hH] ?(\d{2})?\b(?![/\w])"), _heures),       # 18 h 53, 7h, 12 h
    (re.compile(r"(\d) ?km/h\b"), r"\1 kilomètres heure"),
    (re.compile(r"(\d) ?km\b"), r"\1 kilomètres"),
    (re.compile(r"(\d) ?mm\b"), r"\1 millimètres"),
    (re.compile(r"(\d) ?min\b"), r"\1 minutes"),
    (re.compile(r"(\d) ?°C?"), r"\1 degrés"),
    (re.compile(r"(\d) ?%"), r"\1 pour cent"),
    (re.compile(r"(\d) ?€"), r"\1 euros"),
    (re.compile(r"(?<![\w-])-(\d)"), r"moins \1"),                          # -3 degrés
    (re.compile(r"\bBd\b"), "boulevard"),
]


def _dictionnaire() -> list:
    """Règles du fichier, recompilées seulement quand il a changé (les plus longues d'abord)."""
    mtime = FICHIER.stat().st_mtime if FICHIER.exists() else None
    with _verrou:
        if mtime != _cache["mtime"]:
            paires = []
            for ligne in FICHIER.read_text(encoding="utf-8").splitlines() if mtime else []:
                if "=" in ligne and not ligne.lstrip().startswith("#"):
                    ecrit, dit = (x.strip() for x in ligne.split("=", 1))
                    if ecrit and dit:
                        paires.append((ecrit, dit))
            paires.sort(key=lambda p: -len(p[0]))
            _cache["regles"] = [(re.compile(rf"(?<!\w){re.escape(e)}(?!\w)", re.IGNORECASE), d)
                                for e, d in paires]
            _cache["mtime"] = mtime
        return _cache["regles"]


def prononcer(texte: str) -> str:
    for motif, dit in _dictionnaire():
        texte = motif.sub(lambda _: dit, texte)
    for motif, remplacement in REGLES:
        texte = motif.sub(remplacement, texte)
    return texte


def _cle(ligne: str) -> str | None:
    """Le mot écrit d'une ligne du dictionnaire, en minuscules (None pour un commentaire)."""
    if ligne.lstrip().startswith("#") or "=" not in ligne:
        return None
    return ligne.split("=", 1)[0].strip().lower() or None


def _lire_lignes() -> list[str]:
    return FICHIER.read_text(encoding="utf-8").splitlines() if FICHIER.exists() else ENTETE.splitlines()


def _ecrire_lignes(lignes: list[str]) -> None:
    """Écriture atomique : Piper ne relit jamais un fichier à moitié écrit."""
    tmp = FICHIER.with_name(FICHIER.name + ".tmp")
    tmp.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    os.replace(tmp, FICHIER)


def lister() -> list[dict]:
    """Les entrées du dictionnaire, dans l'ordre du fichier."""
    with _verrou:
        lignes = _lire_lignes()
    mots = []
    for ligne in lignes:
        if _cle(ligne):
            ecrit, dit = (x.strip() for x in ligne.split("=", 1))
            if dit:
                mots.append({"ecrit": ecrit, "dit": dit})
    return mots


def apprendre(ecrit: str, dit: str, ancien: str | None = None) -> None:
    """Ajoute ou remplace « ecrit = dit » dans le dictionnaire.

    « ancien » : le mot écrit avant modification (quand on corrige l'orthographe du mot lui-même).
    Une entrée existante garde sa place dans le fichier ; une nouvelle va à la fin."""
    ecrit, dit = " ".join(ecrit.split()), " ".join(dit.split())
    if not ecrit or not dit or "=" in ecrit + dit or "\n" in ecrit + dit or len(ecrit + dit) > 120:
        raise ValueError("prononciation invalide")
    a_remplacer = {ecrit.lower(), " ".join((ancien or "").split()).lower()} - {""}
    nouvelle = f"{ecrit} = {dit}"
    with _verrou:
        lignes, place = [], None
        for ligne in _lire_lignes():
            if _cle(ligne) in a_remplacer:
                if place is None:
                    place = len(lignes)
                    lignes.append(nouvelle)
                continue
            lignes.append(ligne)
        if place is None:
            lignes.append(nouvelle)
        _ecrire_lignes(lignes)


def oublier(ecrit: str) -> bool:
    """Retire l'entrée « ecrit » ; renvoie False si elle n'existait pas."""
    cle = " ".join(ecrit.split()).lower()
    with _verrou:
        lignes = _lire_lignes()
        restantes = [l for l in lignes if _cle(l) != cle]
        if len(restantes) == len(lignes):
            return False
        _ecrire_lignes(restantes)
        return True
