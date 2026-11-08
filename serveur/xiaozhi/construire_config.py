"""Assemble le .config.yaml du serveur XiaoZhi à partir de config.yaml, du .env et de personnalite.md.

  python construire_config.py SORTIE [--env CHEMIN] [--personnalite CHEMIN]

Par défaut : le .env est à la racine du dépôt (copie de .env.example), la personnalité est
personnalite.md à côté de ce script (copie de personnalite.exemple.md). Les commentaires
<!-- … --> de la personnalité sont retirés, le reste devient le bloc « prompt » de la config.
Chaque ${VARIABLE} de config.yaml doit être définie dans le .env (ou dans l'environnement).
"""

import argparse
import os
import re
import sys
from pathlib import Path

ICI = Path(__file__).resolve().parent
MARQUE = "  PERSONNALITE\n"


def lire_env(chemin: Path) -> dict:
    valeurs = {}
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, valeur = ligne.split("=", 1)
        valeurs[cle.strip()] = valeur.strip().strip('"').strip("'")
    return valeurs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sortie")
    parser.add_argument("--env", default=str(ICI.parent.parent / ".env"))
    parser.add_argument("--personnalite", default=str(ICI / "personnalite.md"))
    args = parser.parse_args()

    valeurs = {**lire_env(Path(args.env)), **{k: v for k, v in os.environ.items() if k.startswith(("BILLY_", "OPENAI_"))}}
    config = (ICI / "config.yaml").read_text(encoding="utf-8")
    manquantes = sorted({m for m in re.findall(r"\$\{([A-Z0-9_]+)\}", config) if m not in valeurs})
    if manquantes:
        raise SystemExit("variables absentes du .env : " + ", ".join(manquantes))
    config = re.sub(r"\$\{([A-Z0-9_]+)\}", lambda m: valeurs[m.group(1)], config)

    texte = re.sub(r"<!--.*?-->", "", Path(args.personnalite).read_text(encoding="utf-8"), flags=re.S)
    texte = re.sub(r"\$\{([A-Z0-9_]+)\}", lambda m: valeurs.get(m.group(1), m.group(0)), texte)
    lignes = [l.rstrip() for l in texte.strip().splitlines()]
    if config.count(MARQUE) != 1 or not lignes:
        raise SystemExit("marque PERSONNALITE introuvable ou personnalité vide")
    bloc = "".join(f"  {l}\n" if l else "\n" for l in lignes)
    Path(args.sortie).write_text(config.replace(MARQUE, bloc), encoding="utf-8", newline="\n")
    print(f"{args.sortie} : personnalité de {len(lignes)} lignes, LLM {valeurs.get('BILLY_LLM')}", file=sys.stderr)


if __name__ == "__main__":
    main()
