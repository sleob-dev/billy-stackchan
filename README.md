# Billy : firmware maison et serveur local pour le robot StackChan de M5Stack

*English summary at the end.*

Billy est un robot de bureau [StackChan](https://github.com/m5stack/StackChan) (M5Stack CoreS3, tête à deux servos) qui parle français et ne dépend d'aucun service chinois : il discute avec **votre** serveur [XiaoZhi](https://github.com/xinnan-tech/xiaozhi-esp32-server), sur votre réseau local, avec le LLM de votre choix, une reconnaissance vocale locale (faster-whisper) et une voix locale ([Piper](https://github.com/OHF-Voice/piper1-gpl)).

Ce dépôt contient :

- **`firmware/`** : des correctifs à appliquer au firmware officiel StackChan 1.6.2, et le script qui le construit ;
- **`serveur/`** : les ajouts au serveur XiaoZhi (fournisseur Claude, outils, réponse au mot d'éveil…) et les petits services qui tournent à côté (voix, reconnaissance vocale, alertes, télécommande, empreintes vocales) ;
- **`simulateur/`** : une page web qui imite le robot (visage, tête, voix Piper) pour régler la voix et tester sans la carte ;
- **`outils/`** : `nvs_ota_url.py`, pour envoyer le robot vers votre serveur sans recompiler.

Aucun binaire n'est fourni : vous construisez le firmware vous-même, à partir des sources officielles.

## Ce que fait le firmware maison

À partir du firmware StackChan **1.6.2** (xiaozhi-esp32 v2.5.2, ESP-IDF v6.1) :

- **Message `notify`** : le serveur peut faire jouer un son au robot à tout moment (alerte, phrase dite par Piper), avec sous-titres. Le correctif d'origine échouait faute de RAM interne : la tâche de téléchargement est maintenant créée une seule fois, avec sa pile en PSRAM (`patches/notify-pile-psram.patch`, `patches/notify-public.patch`).
- **Voix nette** : le décodage de la voix passe avant le dessin du visage (`patches/decodage-prioritaire.patch`).
- **Visage maison** (`skin-billy/`) : trois styles (Visière, Kawaii, Visière kawaii), quinze expressions, dessinés en couches pour ne pas faire saccader la voix ; un style par jour, ou choisi à distance par les outils MCP `self.billy.set_style` et `self.billy.get_style` (gardé en NVS).
- **Réveil** par « Hi StackChan » ou par **une petite tape sur la tête** ; le serveur répond aussitôt « Oui <prénom> ? ».
- **« Il te regarde »** (`billy-extras/presence.*`, `billy-extras/regard.*`) : la caméra détecte un mouvement devant le robot (pas un visage), il tourne la tête vers lui et prend l'air content ; une fois par heure au plus, il dit bonjour (son servi par `billy-notif`). Rien la nuit (23 h à 7 h).
- **Danses** : l'outil MCP `self.robot.play_moves` joue une suite de pas (tête et LED) avec l'horloge du robot (`billy-extras/moves.*`).
- **Regard à hauteur d'homme** au repos, pour que la caméra ne fixe pas le plafond (`patches-stackchan/regard-horizon.patch`).
- **Bluetooth coupé**, pour rendre environ 23 Ko de RAM interne (`patches-stackchan/sans-bluetooth.patch`).

Ce qu'on perd : l'appli StackChan World (modes Avatar et Dance pilotés par le téléphone), dont le code n'est pas publié.

**Aucune adresse n'est compilée** : le robot trouve votre serveur par la clé NVS `wifi:ota_url` (ou par `BILLY_OTA_URL` au moment de la compilation, si vous le souhaitez).

## Ce que fait le serveur

Le serveur est [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) (Python), installé tel quel, plus nos fichiers :

| Fichier | Rôle |
|---|---|
| `serveur/xiaozhi/claude.py` | fournisseur LLM Claude (SDK Anthropic), à copier en `core/providers/llm/claude/claude.py` |
| `serveur/xiaozhi/vision_claude.py` | description des photos de la caméra par Claude, à copier en `core/providers/vllm/claude.py` |
| `serveur/xiaozhi/billy_maison.py` | outils d'exemple : météo (Open-Meteo, sans clé), prononciation, voix |
| `serveur/xiaozhi/billy_danse.py` | trois danses, avec musiques 8 bits générées par le code (aucun fichier sous droits) |
| `serveur/xiaozhi/*.patch` | correctifs du serveur : réponse immédiate au mot d'éveil, lèvres calées sur le son, rééchantillonnage soxr, texte masqué, erreurs d'outils lisibles, voix apprises à l'oral… |
| `serveur/xiaozhi/config.yaml` | modèle de configuration, assemblé par `construire_config.py` |
| `serveur/xiaozhi/billy-prompt.txt` | gabarit du prompt système (règles de l'oral, outils, contexte) |
| `serveur/xiaozhi/personnalite.exemple.md` | personnalité d'exemple, à copier en `personnalite.md` |
| `serveur/mqtt-gateway/` | API locale ajoutée à [xiaozhi-mqtt-gateway](https://github.com/78/xiaozhi-mqtt-gateway), qui garde le robot joignable entre deux conversations |
| `serveur/piper/` | serveur de synthèse vocale Piper, avec un dictionnaire de prononciations (**GPL-3.0**, voir plus bas) |
| `serveur/whisper/` | serveur de reconnaissance vocale faster-whisper, au format de l'API OpenAI |
| `serveur/voix/` | empreintes vocales : qui parle ? |
| `serveur/notif/` | alertes (`POST /notif`) jouées par le robot, et le salut de « Il te regarde » |
| `serveur/commande/` | télécommande pour le réseau local (état, silence, faire dire un texte, volume, luminosité, danse, visage), protégée par un jeton |
| `serveur/systemd/` | services systemd d'exemple (tout sous `/opt/billy/`) |

## Tout se règle dans `.env`

Copiez [.env.example](.env.example) en `.env` et remplissez-le : adresse du serveur, prénom et nom du robot, LLM (Claude, OpenAI ou compatible, Ollama), modèle Whisper, voix Piper et ses réglages, lieu de la météo. Les clés API ne vont que dans ce fichier, jamais dans le dépôt (`.env` est ignoré par git).

Sur le serveur, ce même fichier devient `/opt/billy/billy.env`, lu par les services systemd.

## Installation (résumé)

Prévoyez une machine Linux sur votre réseau local (Debian 12 ou Ubuntu 24.04, 4 Go de RAM ; un mini-PC suffit) et un robot StackChan (CoreS3).

### 1. Le serveur

1. Installez `ffmpeg` (les services s'en servent pour préparer les sons Ogg Opus) et Node.js (pour la passerelle MQTT).
2. Installez xiaozhi-esp32-server en mode source (sans Docker), selon sa documentation, dans `/opt/billy/xiaozhi-esp32-server`, avec son environnement Python dans `/opt/billy/venv-xiaozhi`.
3. Copiez nos fichiers Python aux endroits indiqués dans le tableau, et appliquez les correctifs depuis `main/xiaozhi-server` : `git apply /chemin/vers/serveur/xiaozhi/*.patch`. Ils ont été écrits pour la version du serveur de novembre 2026 : sur une version plus récente, un correctif peut demander une adaptation.
4. Copiez les dossiers `serveur/piper`, `serveur/whisper`, `serveur/voix`, `serveur/notif` et `serveur/commande` dans `/opt/billy/` (`/opt/billy/piper`, `/opt/billy/whisper`…), là où les services systemd les attendent. Dans `/opt/billy/piper`, copiez `prononciations.exemple.txt` en `prononciations.txt`.
5. Créez `/opt/billy/venv-voix` et installez `serveur/requirements-voix.txt` (Piper, faster-whisper). Téléchargez une voix Piper (par exemple `fr_FR-siwis-medium`, fichiers `.onnx` et `.onnx.json`) dans `/opt/billy/piper/voix/`. Pour les empreintes vocales, `billy-voix` tourne dans `venv-xiaozhi` et attend le modèle sherpa-onnx `nemo_en_titanet_small.onnx`, renommé `/opt/billy/voix/modele.onnx` (détails en tête de `serveur/voix/serveur.py`).
6. Installez la passerelle MQTT ([xiaozhi-mqtt-gateway](https://github.com/78/xiaozhi-mqtt-gateway)) dans `/opt/billy/mqtt-gateway`, avec `api-billy.js`, `mqtt.json` (dans `config/`) et `vision-initialize.patch`. Ajoutez dans son `app.js`, à la fin, après `server.start()`, la ligne `require('./api-billy').startApi(server);` (voir l'en-tête d'`api-billy.js`).
7. Copiez `.env` en `/opt/billy/billy.env` (droits 600), puis assemblez la configuration :
   ```bash
   python3 serveur/xiaozhi/construire_config.py /opt/billy/xiaozhi-esp32-server/main/xiaozhi-server/data/.config.yaml --env /opt/billy/billy.env
   cp serveur/xiaozhi/billy-prompt.txt /opt/billy/xiaozhi-esp32-server/main/xiaozhi-server/data/
   ```
8. Installez les services de `serveur/systemd/` dans `/etc/systemd/system/`, puis `systemctl enable --now billy-piper billy-whisper billy-voix billy-xiaozhi billy-mqtt billy-notif`.
9. Télécommande (facultative) : créez un jeton aléatoire, gardez-le chez le client, et ne mettez sur le serveur que son empreinte :
   ```bash
   jeton=$(openssl rand -hex 32); echo "$jeton"   # à garder chez le client
   printf 'BILLY_COMMANDE_JETON_SHA256=%s\n' "$(printf %s "$jeton" | sha256sum | cut -d' ' -f1)" > /opt/billy/commande/commande.env
   chmod 600 /opt/billy/commande/commande.env; systemctl enable --now billy-commande
   ```

Les services de voix et de reconnaissance n'écoutent que `127.0.0.1`. Ceux qui écoutent le réseau (8000, 8003, 1883, 8884 en UDP, 5004, 5006) sont faits pour un **réseau local** : ne les exposez pas sur Internet.

### 2. Le firmware

Construction sur une machine Linux (root, environ 10 Go de disque) :

```bash
cd firmware
sudo bash construire.sh
# ou, pour compiler l'adresse du serveur dans le firmware :
sudo BILLY_OTA_URL=http://192.0.2.10:8003/xiaozhi/ota/ bash construire.sh
```

Depuis Windows, `construire.ps1` envoie les fichiers sur la machine Linux par SSH, lance la compilation et rapatrie les binaires :

```powershell
powershell -ExecutionPolicy Bypass -File .\firmware\construire.ps1 -Hote root@192.0.2.20
```

Le script place ESP-IDF et StackChan sur des commits figés, applique les correctifs, compile, puis vérifie que chaque ajout est bien dans le binaire. Les binaires et leurs adresses sont dans `/opt/billy-fw/sortie/` (`adresses.txt`).

### 3. Flasher

**Commencez par sauvegarder la flash complète** de votre robot (16 Mo), c'est votre seul retour arrière sûr :

```bash
python -m esptool --chip esp32s3 --port COM3 read-flash 0 0x1000000 flash-complete.bin
```

Puis écrivez seulement `otadata`, l'application et les assets. Ne touchez ni au bootloader (celui d'usine démarre très bien une application ESP-IDF v6.1, l'inverse n'est pas garanti), ni à la table de partitions, ni à la NVS (Wi-Fi, calibration des servos) :

```bash
python -m esptool --chip esp32s3 --port COM3 --baud 460800 write-flash 0xd000 ota_data_initial.bin 0x20000 stack-chan.bin 0xa00000 generated_assets.bin
```

Remplacez `COM3` par le port de votre robot (`/dev/ttyACM0` sous Linux). Vérifiez les adresses dans `adresses.txt` : elles valent pour la table de partitions de StackChan 1.6.2.

### 4. Brancher le robot sur votre serveur

Sans recompiler, écrivez l'adresse de votre serveur dans la NVS du robot :

```bash
python -m esptool --chip esp32s3 --port COM3 read-flash 0x9000 0x4000 nvs.bin
python outils/nvs_ota_url.py nvs.bin --ecrire http://192.0.2.10:8003/xiaozhi/ota/ -o nvs-nouveau.bin
python -m esptool --chip esp32s3 --port COM3 write-flash 0x9000 nvs-nouveau.bin
```

`--effacer` renvoie le robot vers le serveur d'usine.

Le salut de « Il te regarde » se trouve tout seul : `http://<machine de l'adresse OTA>:5004/salut.ogg`. La clé NVS `billy:salut_url` permet de le servir ailleurs.

Si vous déposez un jour un firmware officiel plus récent dans le dossier de mises à jour de votre serveur XiaoZhi (`data/bin/`), le robot l'installera à sa place : n'y mettez que ce que vous voulez voir flashé.

## Simulateur

`lancer-simulateur.cmd` (Windows) ouvre `simulateur/index.html` dans une fenêtre, avec la voix Piper si `serveur/piper/.venv` existe :

```powershell
cd serveur\piper
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Placez une voix Piper dans `serveur/piper/voix/`. Dans le simulateur, réglez vitesse, expressivité, rythme et hauteur, puis reportez-les dans `.env` (`BILLY_PIPER_*`).

## Licences et crédits

Ce projet s'appuie sur :

| Projet | Licence | Utilisation ici |
|---|---|---|
| [m5stack/StackChan](https://github.com/m5stack/StackChan) (dossiers `firmware/` et `server/`) | MIT | firmware de base, que nos correctifs modifient |
| [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) | MIT | cœur du firmware (correctifs `firmware/patches/`) |
| [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) | MIT | serveur, que nos fichiers et correctifs complètent |
| [78/xiaozhi-mqtt-gateway](https://github.com/78/xiaozhi-mqtt-gateway) | MIT | passerelle MQTT, complétée par `api-billy.js` |
| [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl) (`piper-tts`) | GPL-3.0 | synthèse vocale, utilisée par `serveur/piper/` |
| [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | reconnaissance vocale |
| [Espressif ESP-IDF](https://github.com/espressif/esp-idf) | Apache-2.0 | cadre du firmware |

- Le code de ce dépôt est sous licence **MIT** ([LICENSE](LICENSE)), **sauf le dossier `serveur/piper/`**, sous **GPL-3.0** ([serveur/piper/LICENSE](serveur/piper/LICENSE)), parce qu'il importe la bibliothèque `piper-tts`, elle-même sous GPL-3.0.
- Les voix Piper ont chacune leur propre licence (voir leur fiche `MODEL_CARD`) : elles ne sont pas fournies ici.
- Les correctifs (`*.patch`) modifient du code sous licence MIT ; ils restent sous MIT.
- StackChan est une marque et une création de M5Stack et de la communauté Stack-chan ; ce projet n'est ni affilié à M5Stack ni approuvé par M5Stack.

---

## English summary

Custom firmware patches and a local server for the M5Stack **StackChan** desk robot (CoreS3), so it talks French with **your own** XiaoZhi server on your local network: the LLM of your choice (Claude, OpenAI-compatible, Ollama), local speech recognition (faster-whisper) and a local voice (Piper).

- `firmware/`: patches for the official StackChan 1.6.2 firmware (working `notify` messages, voice decoding priority, custom face with remote style selection, head-tap wake-up, camera-based motion "look at you", `play_moves` MCP tool for dances, Bluetooth disabled to free internal RAM) and a reproducible build script. No server address is compiled in: point the robot to your server with the NVS key `wifi:ota_url` (`outils/nvs_ota_url.py`).
- `serveur/`: additions to xiaozhi-esp32-server (Claude LLM and vision providers, example tools, dances with generated chiptune music, patches) and small companion services (Piper TTS, faster-whisper STT, voiceprints, alerts, token-protected LAN remote control).
- All settings live in `.env` (copy `.env.example`). Back up your full flash before writing anything, and never expose these services to the Internet.

License: MIT, except `serveur/piper/` (GPL-3.0, because it imports `piper-tts`). Not affiliated with or endorsed by M5Stack.
