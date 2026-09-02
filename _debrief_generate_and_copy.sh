# Bibliothèque partagée (à SOURCER, pas à exécuter directement) entre
# daily_debrief_to_icloud.sh (tâche du soir, 23h59) et catchup_missed_debrief.sh
# (rattrapage en journée si la veille a échoué) : génère + rend le DEBRIEF pour
# une date donnée, avec le plafond dur de 10 min anti-blocage (voir commit du
# 02/09 — un vrai blocage de 16h+ a été constaté sans lui), puis copie la
# vidéo + la légende/hashtags vers iCloud Drive si un fichier a bien été produit.

generate_and_copy_debrief() {
    local target_date="$1"
    local log_file="$2"
    local icloud_dir="$HOME/Library/Mobile Documents/com~apple~CloudDocs/DEBRIEF Videos"
    mkdir -p "$icloud_dir"

    # macOS n'a pas la commande `timeout` par défaut : implémenté à la main.
    # set -m (job control) + kill du GROUPE de processus (pas juste le parent,
    # sinon un sous-processus python bloqué survivrait) via "-- -$PID".
    local generate_timeout_seconds=600
    set -m
    ./generate_debrief.sh "$target_date" >> "$log_file" 2>&1 &
    local generate_pid=$!
    (
        sleep "$generate_timeout_seconds"
        if kill -0 "$generate_pid" 2>/dev/null; then
            echo "⚠️  Génération bloquée depuis plus de ${generate_timeout_seconds}s — arrêt forcé du groupe de processus." >> "$log_file"
            kill -9 -- -"$generate_pid" 2>/dev/null
        fi
    ) &
    local watchdog_pid=$!
    wait "$generate_pid" 2>/dev/null
    kill "$watchdog_pid" 2>/dev/null
    wait "$watchdog_pid" 2>/dev/null
    set +m

    local video_src="video_output/$target_date/debrief.mp4"
    local json_src="video_output/$target_date/debrief.json"

    if [ -f "$video_src" ]; then
        cp "$video_src" "$icloud_dir/debrief_$target_date.mp4"
        echo "Vidéo copiée vers iCloud : $icloud_dir/debrief_$target_date.mp4" >> "$log_file"

        # Légende + hashtags prêts à coller, dans un fichier texte à côté de la
        # vidéo (voir feedback_video_caption_workflow : toujours fournir la description).
        if [ -f "$json_src" ]; then
            .venv/bin/python3 -c "
import json
with open('$json_src') as f:
    data = json.load(f)
with open('$icloud_dir/debrief_$target_date.txt', 'w') as f:
    f.write(data['legende_complete'] + '\n\n')
    f.write(' '.join('#' + h for h in data['hashtags']) + '\n')
" >> "$log_file" 2>&1
            echo "Description copiée vers iCloud : $icloud_dir/debrief_$target_date.txt" >> "$log_file"
        fi
        return 0
    else
        echo "⚠️  Pas de vidéo générée pour $target_date (pas de breaking news ce jour-là, ou échec — voir ci-dessus)." >> "$log_file"
        return 1
    fi
}
