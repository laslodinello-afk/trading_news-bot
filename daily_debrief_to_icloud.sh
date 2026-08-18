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

./generate_debrief.sh "$TODAY" >> "$LOG_FILE" 2>&1

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
