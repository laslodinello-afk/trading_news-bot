-- Raccourci bureau : génère la vidéo DEBRIEF pour une date choisie, avec une
-- remarque optionnelle prise en compte par le script. Rafraîchit le calendrier
-- économique local avant de générer (voir generate_debrief.sh).

set projectPath to "/Users/laslobernardinello/Desktop/trading-news-agent"

try
	set todayDate to do shell script "date +%Y-%m-%d"
on error
	set todayDate to "2026-07-26"
end try

try
	set dateDialog to display dialog "Générer la vidéo DEBRIEF pour quelle date ?" with title "Débrief vidéo — trading news agent" default answer todayDate buttons {"Annuler", "Continuer"} default button "Continuer" cancel button "Annuler"
on error number -128
	return
end try
set chosenDate to text returned of dateDialog

try
	set notesDialog to display dialog "Remarques ou instructions supplémentaires pour cette vidéo (facultatif) :" with title "Remarques (optionnel)" default answer "" buttons {"Annuler", "Générer la vidéo"} default button "Générer la vidéo" cancel button "Annuler"
on error number -128
	return
end try
set chosenNotes to text returned of notesDialog

display notification "Rafraîchissement des données puis génération en cours (quelques minutes)…" with title "Débrief vidéo" subtitle ("Date : " & chosenDate)

set shellCommand to "cd " & quoted form of projectPath & " && ./generate_debrief.sh " & quoted form of chosenDate & " " & quoted form of chosenNotes & " 2>&1"

set scriptOutput to ""
try
	set scriptOutput to do shell script shellCommand
on error errMsg
	display dialog "Échec inattendu :" & return & return & errMsg with title "Débrief vidéo — Erreur" buttons {"OK"} default button "OK" with icon caution
	return
end try

set videoPath to projectPath & "/video_output/" & chosenDate & "/debrief.mp4"
set fileCheck to do shell script "test -f " & quoted form of videoPath & " && echo yes || echo no"

if fileCheck is "yes" then
	display notification "Vidéo prête : video_output/" & chosenDate & "/debrief.mp4" with title "Débrief vidéo" subtitle "Génération réussie" sound name "Glass"
	do shell script "open -R " & quoted form of videoPath
else
	display dialog "Aucune vidéo générée pour le " & chosenDate & "." & return & return & "Raison la plus probable : aucune donnée exploitable ce jour-là (pas de résultat économique publié, pas de breaking news) — ce n'est pas un bug, voir le README section \"Limites honnêtes à connaître\"." & return & return & "Détail technique :" & return & scriptOutput with title "Débrief vidéo — Rien à générer" buttons {"OK"} default button "OK"
end if
