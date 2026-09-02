#!/bin/bash
# Lancé automatiquement chaque soir à 23h59 (voir com.laslodinello.debrief-daily.plist) :
# génère + rend le DEBRIEF vidéo du jour, puis copie le résultat (vidéo + légende/
# hashtags) dans iCloud Drive pour que ce soit accessible sans rouvrir Claude Code.
# Tout est loggé dans daily_debrief.log — c'est la seule trace si ça échoue pendant
# la nuit (le Mac doit être allumé/éveillé à 23h59 pour que launchd déclenche la tâche).
# Si cette tâche échoue (pas de réseau, blocage...), voir catchup_missed_debrief.sh :
# un rattrapage automatique réessaie en journée dès qu'une connexion est là.
set -uo pipefail
cd "$(dirname "$0")" || exit 1
source ./_debrief_generate_and_copy.sh

LOG_FILE="daily_debrief.log"
TODAY=$(date +%Y-%m-%d)

# Verrou simple : évite de tourner en même temps qu'un rattrapage en cours
# (catchup_missed_debrief.sh, voir ce fichier) qui écrirait dans les mêmes
# dossiers en parallèle. Si le verrou est déjà pris, on abandonne (le
# rattrapage suivant, dans l'heure, retentera si besoin).
LOCK_DIR="/tmp/debrief_generation.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') — Une génération est déjà en cours (verrou pris), tâche du soir abandonnée. ===" >> "$LOG_FILE"
    exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

echo "=== $(date '+%Y-%m-%d %H:%M:%S') — Génération DEBRIEF automatique pour $TODAY ===" >> "$LOG_FILE"

# Le Mac vient peut-être de se réveiller (pmset repeat wake, voir README) — le
# WiFi met parfois plusieurs secondes/dizaines de secondes à se reconnecter,
# et un lancement trop tôt a déjà fait échouer la synchro Render ET Turso par
# timeout un soir (constaté en conditions réelles le 27/08 : "Read timed out"
# sur les deux, script généré sans aucune vraie donnée). Attend jusqu'à 60s
# qu'une vraie connexion soit dispo avant de lancer la génération ; continue
# quand même après ce délai (best-effort, comme le reste du pipeline) plutôt
# que de bloquer indéfiniment si le réseau reste indisponible — si ça échoue
# quand même, catchup_missed_debrief.sh réessaiera en journée.
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

generate_and_copy_debrief "$TODAY" "$LOG_FILE"

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
