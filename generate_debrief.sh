#!/bin/bash
# Synchronise avec le service Render (voir render_sync.py — sauté si non
# configuré), rafraîchit le calendrier économique local, puis génère + rend la
# vidéo DEBRIEF pour une date donnée. Appelé par le raccourci bureau (voir
# Debrief Video.app), mais utilisable directement en Terminal :
#   ./generate_debrief.sh 2026-07-26 "remarque optionnelle"
#
# Sort toujours avec le code 0 : le raccourci bureau distingue "pas de vidéo
# générée" (aucune donnée exploitable, cas normal) d'une vraie panne en
# vérifiant simplement si le fichier .mp4 attendu existe après coup, pas en
# interprétant le code de sortie de video_scripts.py (qui, lui, sort en erreur
# sur "aucune donnée" — ce n'est pas une panne du point de vue de ce script).
set -uo pipefail
cd "$(dirname "$0")" || exit 1

DATE="${1:?Usage: generate_debrief.sh AAAA-MM-JJ [remarque]}"
NOTES="${2:-}"

echo "=== Synchro avec le service Render (données réellement publiées) ==="
.venv/bin/python3 -c "
import render_sync, db
from datetime import date
db.init_db()
target = date.fromisoformat('$DATE')
if render_sync.sync_from_render(target):
    print('Synchro Render OK.')
else:
    print('Synchro Render sautée ou indisponible (RENDER_SYNC_URL/SYNC_API_KEY non configurées, ou service injoignable) — on continue avec le rafraîchissement local ci-dessous.')
"

echo ""
echo "=== Rafraîchissement du calendrier économique local ==="
.venv/bin/python3 -c "
import calendar_fetcher, db
db.init_db()  # idempotent (CREATE TABLE IF NOT EXISTS) - garde-fou si alerts.db est absente/vierge
events, source = calendar_fetcher.refresh_calendar()
for e in events:
    db.upsert_event(e)
print(f'{len(events)} evenement(s) rafraichi(s) (source: {source})')
"
if [ $? -ne 0 ]; then
    echo "Rafraîchissement du calendrier échoué (réseau ?) — on continue avec les données déjà en base locale."
fi

echo ""
echo "=== Génération + rendu DEBRIEF pour $DATE ==="
if [ -n "$NOTES" ]; then
    .venv/bin/python3 video_scripts.py --format DEBRIEF --date "$DATE" --render --notes "$NOTES"
else
    .venv/bin/python3 video_scripts.py --format DEBRIEF --date "$DATE" --render
fi

exit 0
