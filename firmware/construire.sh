#!/usr/bin/env bash
# Construit le firmware maison de Billy (StackChan 1.6.2, ESP-IDF v6.1).
# À lancer en root sur une machine Linux Debian ou Ubuntu (conteneur, VM ou PC), avec
# sdkconfig.defaults.local posé à côté de ce script (construire.ps1 s'en charge depuis Windows).
#
# Adresse du serveur (facultative) : BILLY_OTA_URL=http://<votre-serveur>:8003/xiaozhi/ota/
# l'ajoute au firmware (CONFIG_OTA_URL). Sans elle, le firmware garde l'adresse d'usine et
# on branche le robot sur son serveur par la clé NVS wifi:ota_url (outils/nvs_ota_url.py).
#
# Reproductible : versions figées par commit, sdkconfig et build effacés à
# chaque fois (les valeurs par défaut ne s'appliquent qu'à un sdkconfig neuf).
# Dossier de travail : /opt/billy-fw, ou BILLY_FW_TRAVAIL=/autre/dossier.
# Résultat : <dossier de travail>/sortie/ (binaires, adresses, empreintes, contrôles).
set -euo pipefail

STACKCHAN_URL=https://github.com/m5stack/StackChan.git
STACKCHAN_COMMIT=28b9f0c8bd6acd81cb3c70f1a93c3e373b39d400   # firmware v1.6.2, 4 novembre 2026
IDF_TAG=v6.1
IDF_COMMIT=fff9895c82d744c7237be8847347bdd1b07c6643   # commit de l'étiquette v6.1
OTA_ATTENDUE=${BILLY_OTA_URL:-}

TRAVAIL=${BILLY_FW_TRAVAIL:-/opt/billy-fw}
export IDF_PATH=/opt/esp-idf-v6.1
export IDF_TOOLS_PATH=/opt/espressif
ICI=$(cd "$(dirname "$0")" && pwd)
FW=$TRAVAIL/StackChan/firmware
SORTIE=$TRAVAIL/sortie

etape() { printf '\n=== %s\n' "$*"; }
echec() { printf 'ÉCHEC : %s\n' "$*" >&2; exit 1; }

[ -f "$ICI/sdkconfig.defaults.local" ] || echec "sdkconfig.defaults.local absent de $ICI"
mkdir -p "$TRAVAIL"

etape "Paquets Debian"
if ! dpkg -s git cmake ninja-build python3-venv ccache flex bison gperf libusb-1.0-0 binutils >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq git wget curl flex bison gperf python3 python3-pip python3-venv \
        cmake ninja-build ccache libffi-dev libssl-dev dfu-util libusb-1.0-0 xz-utils unzip binutils
fi

etape "ESP-IDF $IDF_TAG"
if [ ! -d "$IDF_PATH/.git" ]; then
    git clone -q --depth 1 --branch "$IDF_TAG" --recurse-submodules --shallow-submodules \
        https://github.com/espressif/esp-idf.git "$IDF_PATH"
fi
[ "$(git -C "$IDF_PATH" rev-parse HEAD)" = "$IDF_COMMIT" ] || echec "ESP-IDF n'est pas au commit $IDF_COMMIT"
"$IDF_PATH/install.sh" esp32s3 >/dev/null
# shellcheck disable=SC1091
. "$IDF_PATH/export.sh" >/dev/null

etape "Sources StackChan $STACKCHAN_COMMIT"
if [ ! -d "$TRAVAIL/StackChan/.git" ]; then
    git clone -q "$STACKCHAN_URL" "$TRAVAIL/StackChan"
fi
git -C "$TRAVAIL/StackChan" fetch -q origin
git -C "$TRAVAIL/StackChan" checkout -q --detach "$STACKCHAN_COMMIT"
# main/ repart lui aussi de sources propres (nos correctifs et la skin sont remis plus bas)
git -C "$TRAVAIL/StackChan" checkout -q -- firmware/main
git -C "$TRAVAIL/StackChan" clean -q -fd firmware/main  # fichiers ajoutés (skin, correctifs)
cd "$FW"
# xiaozhi-esp32 repart de sources propres : fetch_repos.py y remet le correctif M5Stack, puis
# on ajoute les nôtres (patches/*.patch, dans l'ordre alphabétique).
if [ -d xiaozhi-esp32/.git ]; then
    git -C xiaozhi-esp32 reset -q --hard
    git -C xiaozhi-esp32 clean -q -fd
fi
# Récupère mooncake, smooth_ui_toolkit, xiaozhi-esp32 v2.5.2 (+ correctif M5Stack), etc.
python3 ./fetch_repos.py > "$TRAVAIL/fetch_repos.log" 2>&1 || { tail -20 "$TRAVAIL/fetch_repos.log"; echec "fetch_repos.py"; }
grep -E '^(Applied|Patch) ' "$TRAVAIL/fetch_repos.log"
[ "$(git -C xiaozhi-esp32 describe --tags)" = "v2.5.2" ] || echec "xiaozhi-esp32 n'est pas en v2.5.2"
for correctif in "$ICI"/patches/*.patch; do
    [ -e "$correctif" ] || continue
    git -C xiaozhi-esp32 apply --check "$correctif" || echec "correctif $(basename "$correctif") inapplicable"
    git -C xiaozhi-esp32 apply "$correctif"
    echo "Correctif Billy appliqué : $(basename "$correctif")"
done
# Visage de Billy : la skin (skin-billy/) et son branchement dans main/ (patches-stackchan/)
if [ -d "$ICI/skin-billy" ]; then
    mkdir -p "$FW/main/stackchan/avatar/skins/billy"
    cp "$ICI"/skin-billy/*.h "$ICI"/skin-billy/*.cpp "$FW/main/stackchan/avatar/skins/billy/"
    echo "Skin Billy copiée : $(ls "$ICI"/skin-billy | tr '\n' ' ')"
fi
# Ajouts de Billy hors visage (« Il te regarde », play_moves) : billy-extras/ -> main/stackchan/billy/
if [ -d "$ICI/billy-extras" ]; then
    mkdir -p "$FW/main/stackchan/billy"
    cp "$ICI"/billy-extras/*.h "$ICI"/billy-extras/*.cpp "$FW/main/stackchan/billy/"
    echo "Ajouts Billy copiés : $(ls "$ICI"/billy-extras | tr '\n' ' ')"
fi
for correctif in "$ICI"/patches-stackchan/*.patch; do
    [ -e "$correctif" ] || continue
    git -C "$TRAVAIL/StackChan" apply --check "$correctif" || echec "correctif $(basename "$correctif") inapplicable"
    git -C "$TRAVAIL/StackChan" apply "$correctif"
    echo "Correctif Billy appliqué à StackChan : $(basename "$correctif")"
done

etape "Configuration"
cp "$ICI/sdkconfig.defaults.local" "$FW/sdkconfig.defaults.local"
if [ -n "$OTA_ATTENDUE" ]; then
    printf '\nCONFIG_OTA_URL="%s"\n' "$OTA_ATTENDUE" >> "$FW/sdkconfig.defaults.local"
fi
rm -rf "$FW/build" "$FW/sdkconfig"

etape "Compilation (idf.py build)"
idf.py --ccache build

etape "Contrôles"
APP=build/stack-chan.bin
if [ -n "$OTA_ATTENDUE" ]; then
    grep -qx "CONFIG_OTA_URL=\"$OTA_ATTENDUE\"" sdkconfig || echec "CONFIG_OTA_URL n'est pas BILLY_OTA_URL dans sdkconfig"
    strings -n 8 "$APP" | grep -qF "$OTA_ATTENDUE" || echec "adresse OTA absente du binaire"
fi
grep -q 'strcmp(type->valuestring, "notify") == 0' xiaozhi-esp32/main/application.cc || echec "notify non géré dans application.cc"
strings -n 8 "$APP" | grep -qF 'Notify message requires audio_url' || echec "code notify absent du binaire"
grep -q 'xTaskCreatePinnedToCoreWithCaps' xiaozhi-esp32/main/notify/notify_player.cc || echec "tâche notify pas en PSRAM (patches/notify-pile-psram.patch)"
grep -q 'this, 4, &opus_codec_task_handle_' xiaozhi-esp32/main/audio/audio_service.cc || echec "décodage audio pas prioritaire (patches/decodage-prioritaire.patch)"
grep -q 'BillyAvatar' main/hal/board/stackchan_display.cc || echec "visage de Billy non branché (patches-stackchan/visage-billy.patch)"
grep -q 'PresenceModifier' main/hal/board/stackchan_display.cc || echec "« Il te regarde » non branché"
strings -n 8 "$APP" | grep -qF 'tection de mouvement par la cam' || echec "« Il te regarde » par la caméra absent du binaire"
grep -q 'self.robot.play_moves' main/hal/hal_mcp.cpp || echec "outil play_moves absent"
grep -q 'kBillyPitchMaxRepos' main/stackchan/modifiers/idle_motion.h || echec "regard au repos pas limité (patches-stackchan/regard-horizon.patch)"
grep -q 'self.billy.set_style' main/hal/hal_mcp.cpp || echec "outil set_style absent (patches-stackchan/visage-style.patch)"
strings -n 8 "$APP" | grep -qF 'self.billy.set_style' || echec "set_style absent du binaire"
strings -n 8 "$APP" | grep -qF 'self.robot.play_moves' || echec "play_moves absent du binaire"
grep -qx 'CONFIG_SR_WN_WN9L_HISTACKCHAN_TTS3=y' sdkconfig || echec "mot d'éveil Hi StackChan absent"
grep -qx 'CONFIG_SEND_WAKE_WORD_DATA=y' sdkconfig || echec "mot d'éveil non transmis au serveur (Oui <prénom> ?)"
grep -qx '# CONFIG_BT_ENABLED is not set' sdkconfig || echec "Bluetooth encore actif (sdkconfig.defaults.local)"
grep -qx 'CONFIG_LANGUAGE_EN_US=y' sdkconfig || echec "langue différente de l'usine"
grep -qx 'CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y' sdkconfig || echec "flash différente de 16 Mo"
echo "Contrôles OK"

etape "Copie dans $SORTIE"
rm -rf "$SORTIE"; mkdir -p "$SORTIE"
python3 - "$FW/build" "$SORTIE" <<'PY'
import json, os, shutil, sys
build, sortie = sys.argv[1], sys.argv[2]
args = json.load(open(os.path.join(build, "flasher_args.json")))
lignes = []
for adresse, chemin in sorted(args["flash_files"].items(), key=lambda kv: int(kv[0], 16)):
    nom = os.path.basename(chemin)
    shutil.copy2(os.path.join(build, chemin), os.path.join(sortie, nom))
    lignes.append(f"{adresse} {nom}")
open(os.path.join(sortie, "adresses.txt"), "w").write("\n".join(lignes) + "\n")
shutil.copy2(os.path.join(build, "flasher_args.json"), sortie)
print("\n".join(lignes))
PY
cp "$FW/sdkconfig" "$SORTIE/sdkconfig"
cp "$FW/partitions.csv" "$SORTIE/partitions.csv"
{
    echo "StackChan firmware 1.6.2, commit $STACKCHAN_COMMIT"
    echo "xiaozhi-esp32 $(git -C xiaozhi-esp32 describe --tags) + patches/xiaozhi-esp32.patch"
    for correctif in "$ICI"/patches/*.patch; do [ -e "$correctif" ] && echo "  + correctif Billy $(basename "$correctif")"; done
    for correctif in "$ICI"/patches-stackchan/*.patch; do [ -e "$correctif" ] && echo "  + correctif Billy (StackChan) $(basename "$correctif")"; done
    [ -d "$ICI/skin-billy" ] && echo "  + skin Billy (visage maison)"
    echo "ESP-IDF $IDF_TAG ($IDF_COMMIT)"
    echo "CONFIG_OTA_URL=${OTA_ATTENDUE:-(non compilée : clé NVS wifi:ota_url)}"
    echo "Construit le $(date '+%Y-%m-%d %H:%M %Z')"
} > "$SORTIE/version.txt"
(cd "$SORTIE" && sha256sum ./*.bin > sha256sums.txt)
ls -l "$SORTIE"
