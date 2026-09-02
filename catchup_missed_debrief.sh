#!/bin/bash
# Rattrapage automatique (voir com.laslodinello.debrief-catchup.plist,
# StartInterval — se relance environ toutes les heures tant que le Mac est
# éveillé) : si la tâche du soir (daily_debrief_to_icloud.sh) a échoué la
# veille (pas de wifi au réveil, blocage réseau...), retente dès qu'une vraie
# connexion est là. Best-effort et silencieux : ne fait rien de visible si la
# vidéo de la veille existe déjà.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./_debrief_generate_and_copy.sh

LOG_FILE="daily_debrief.log"
ICLOUD_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/DEBRIEF Videos"
YESTERDAY=$(date -v-1d +%Y-%m-%d)  # `date` BSD (macOS), pas GNU

# Déjà générée (par la tâche du soir ou un rattrapage précédent) : rien à faire.
if [ -f "$ICLOUD_DIR/debrief_$YESTERDAY.mp4" ]; then
    exit 0
fi

# Verrou simple partagé avec daily_debrief_to_icloud.sh : ne jamais tourner en
# même temps qu'elle (ou qu'un autre rattrapage déjà en cours).
LOCK_DIR="/tmp/debrief_generation.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

# Pas d'attente longue ici (contrairement à la tâche du soir) : si le réseau
# n'est pas là maintenant, le prochain passage (dans l'heure environ)
# retentera de lui-même — inutile d'attendre en bloquant celui-ci.
if ! curl -s --max-time 5 -o /dev/null https://github.com; then
    exit 0
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') — Rattrapage : $YESTERDAY manquante, réseau disponible, nouvelle tentative ===" >> "$LOG_FILE"
generate_and_copy_debrief "$YESTERDAY" "$LOG_FILE"
echo "=== Rattrapage terminé ===" >> "$LOG_FILE"
