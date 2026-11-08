#!/usr/bin/env bash
# Rend les planches du visage (planche-{A,D,E}.png, dans ce dossier ; à copier dans ../apercu/) sans la
# carte : à lancer sur une machine Linux (g++ et python3). Le script prend billy_face.* dans ../.
set -euo pipefail
cd "$(dirname "$0")"
cp ../billy_face.h ../billy_face.cpp .
g++ -std=c++17 -O2 -Istub -I. billy_face.cpp planche.cpp -o planche
./planche
python3 -I ppm2png.py planche-A.ppm planche-D.ppm planche-E.ppm
rm -f planche planche-*.ppm billy_face.h billy_face.cpp
ls -l planche-*.png
