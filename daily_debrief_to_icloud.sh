#!/bin/bash
# Lancé automatiquement chaque soir à 23h59 (voir com.laslodinello.debrief-daily.plist) :
# génère + rend le DEBRIEF vidéo du jour, puis copie le résultat (vidéo + légende/
# hashtags) dans iCloud Drive pour que ce soit accessible sans rouvrir Claude Code.
# Tout est loggé dans daily_debrief.log — c'est la seule trace si ça échoue pendant
# la nuit (le Mac doit être allumé/éveillé à 23h59 pour que launchd déclenche la tâche).
set -uo pipefail
cd "$(dirname "$0")" || exit 1

ICLOUD_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/DEBRIEF Videos"
mkdir -p "$ICLOUD_DIR"

TODAY=$(date +%Y-%m-%d)
LOG_FILE="daily_debrief.log"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') — Génération DEBRIEF automatique pour $TODAY ===" >> "$LOG_FILE"

# Le Mac vient peut-être de se réveiller (pmset repeat wake, voir README) — le
# WiFi met parfois plusieurs secondes/dizaines de secondes à se reconnecter,
# et un lancement trop tôt a déjà fait échouer la synchro Render ET Turso par
# timeout un soir (constaté en conditions réelles le 27/08 : "Read timed out"
# sur les deux, script généré sans aucune vraie donnée). Attend jusqu'à 60s
# qu'une vraie connexion soit dispo avant de lancer la génération ; continue
# quand même après ce délai (best-effort, comme le reste du pipeline) plutôt
# que de bloquer indéfiniment si le réseau reste indisponible.
NETWORK_READY=0
for i in $(seq 1 12); do
    if curl -s --max-time 3 -o /dev/null https://github.com; then
        NETWORK_READY=1
        echo "Réseau disponible après $(( (i - 1) * 5 ))s d'attente." >> "$LOG_FILE"
        break
    fi
    sleep 5
done
if [ "$NETWORK_READY" -eq 0 ]; then
    echo "⚠️  Réseau toujours indisponible après 60s — on continue quand même (best-effort)." >> "$LOG_FILE"
fi

# Plafond dur de 10 minutes (une génération normale prend 1-3 min) : un vrai
# blocage a été constaté en conditions réelles (02/09 — le processus de
# synchro Render est resté bloqué 16h+ sans avancer, probablement un souci
# Turso qui bloque au lieu d'échouer proprement — voir db.py). macOS n'a pas
# la commande `timeout` par défaut, donc ce garde-fou est fait à la main :
# lance generate_debrief.sh dans son propre groupe de processus (set -m),
# un "chien de garde" en parallèle tue tout le groupe (pas juste le process
# principal, sinon un sous-processus python bloqué survivrait) s'il dépasse
# le délai. Sans ça, un futur blocage similaire paralyserait l'automatisation
# indéfiniment, nuit après nuit, jusqu'à intervention manuelle.
GENERATE_TIMEOUT_SECONDS=600
set -m
./generate_debrief.sh "$TODAY" >> "$LOG_FILE" 2>&1 &
GENERATE_PID=$!
(
    sleep "$GENERATE_TIMEOUT_SECONDS"
    if kill -0 "$GENERATE_PID" 2>/dev/null; then
        echo "⚠️  Génération bloquée depuis plus de ${GENERATE_TIMEOUT_SECONDS}s — arrêt forcé du groupe de processus." >> "$LOG_FILE"
        kill -9 -- -"$GENERATE_PID" 2>/dev/null
    fi
) &
WATCHDOG_PID=$!
wait "$GENERATE_PID" 2>/dev/null
kill "$WATCHDOG_PID" 2>/dev/null
wait "$WATCHDOG_PID" 2>/dev/null
set +m

VIDEO_SRC="video_output/$TODAY/debrief.mp4"
JSON_SRC="video_output/$TODAY/debrief.json"

if [ -f "$VIDEO_SRC" ]; then
    cp "$VIDEO_SRC" "$ICLOUD_DIR/debrief_$TODAY.mp4"
    echo "Vidéo copiée vers iCloud : $ICLOUD_DIR/debrief_$TODAY.mp4" >> "$LOG_FILE"

    # Légende + hashtags prêts à coller, dans un fichier texte à côté de la vidéo
    # (voir feedback_video_caption_workflow : toujours fournir la description).
    if [ -f "$JSON_SRC" ]; then
        .venv/bin/python3 -c "
import json
with open('$JSON_SRC') as f:
    data = json.load(f)
with open('$ICLOUD_DIR/debrief_$TODAY.txt', 'w') as f:
    f.write(data['legende_complete'] + '\n\n')
    f.write(' '.join('#' + h for h in data['hashtags']) + '\n')
" >> "$LOG_FILE" 2>&1
        echo "Description copiée vers iCloud : $ICLOUD_DIR/debrief_$TODAY.txt" >> "$LOG_FILE"
    fi
else
    echo "⚠️  Pas de vidéo générée pour $TODAY (pas de breaking news ce jour-là, ou échec — voir ci-dessus)." >> "$LOG_FILE"
fi

# Remet le Mac en veille une fois terminé (pmset repeat wake l'a réveillé pour
# cette tâche, voir README) — mais SEULEMENT si personne ne l'utilise
# activement à ce moment précis, pour ne jamais couper une session en cours.
# HIDIdleTime (ioreg) donne le temps écoulé depuis la dernière action
# clavier/souris, en nanosecondes ; > 5 min sans activité = personne devant
# la machine, sans risque de la rendormir.
IDLE_SECONDS=$(ioreg -c IOHIDSystem | awk '/HIDIdleTime/ {print int($NF/1000000000); exit}')
if [ "${IDLE_SECONDS:-0}" -gt 300 ]; then
    echo "Inactif depuis ${IDLE_SECONDS}s — remise en veille automatique." >> "$LOG_FILE"
    pmset sleepnow
else
    echo "Activité récente détectée (${IDLE_SECONDS}s d'inactivité) — pas de remise en veille, le Mac est probablement en cours d'utilisation." >> "$LOG_FILE"
fi

echo "=== Terminé ===" >> "$LOG_FILE"
