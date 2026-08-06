# 🤖 Agent de veille news économiques pour trading

Cet agent surveille en continu (24h/24, 7j/7) :
- le **calendrier économique** (USD, EUR, GBP — impact fort 🔴 et moyen 🟠, plus les événements "faible" 🤖 que l'IA juge sous-évalués par ForexFactory — voir plus bas)
- les **news "choc"** hors calendrier (déclaration surprise, tweet à impact, conflit, régulation crypto, choc pétrolier, etc.)

... et t'envoie des **alertes Telegram** avec une analyse IA (Google Gemini) : résumé, biais probable sur tes paires (par défaut XAUUSD, EURUSD, GBPUSD, US30, BTCUSD, ETHUSD, DAX, SP500, NASDAQ, BRENT, CAC40 — 100% configurable), et niveau de danger pour trader.

⚠️ Important à comprendre : seuls USD/EUR/GBP ont un vrai **calendrier économique** (heure précise, prévision/résultat). Le crypto, les indices et le pétrole n'ont pas d'équivalent gratuit fiable — ils profitent de deux choses : (1) le biais IA généré à chaque news USD/EUR déjà couverte (le crypto et les indices US réagissent fortement aux news USD), et (2) la veille "breaking news" étendue à leurs propres déclencheurs (régulation SEC/CFTC, hack d'exchange, décision OPEP...). Voir "Limites honnêtes" plus bas.

Il tourne indépendamment de ton ordinateur une fois déployé (voir Étape 9).

---

## Avant de commencer : ce qu'il te faut

**100% gratuit, sans limite de temps, sans carte bancaire.** Tu vas créer plusieurs comptes/clés au total, ça prend environ 25 minutes :

| # | Compte | Gratuit ? | Obligatoire ? |
|---|--------|-----------|----------------|
| 1 | Bot Telegram (@BotFather) | ✅ | Oui |
| 2 | Google AI Studio (Gemini) | ✅ Palier gratuit permanent | Oui |
| 3 | Alpha Vantage | ✅ (25 requêtes/jour) | Recommandé — résultat réel USD (voir Étape 4) |
| 4 | Financial Modeling Prep (FMP) | ✅ mais fonctionnalité limitée (voir Étape 4) | Facultatif |
| 5 | NewsAPI.org | ✅ | Recommandé |
| 6 | Render.com (hébergement 24/7) | ✅ | Oui, pour le 24/7 |

---

## Étape 1 — Créer ton bot Telegram avec @BotFather

1. Ouvre Telegram (téléphone ou ordinateur) et cherche **@BotFather** dans la barre de recherche.
2. Clique sur "Démarrer" (ou envoie `/start`).
3. Envoie la commande `/newbot`.
4. BotFather te demande un **nom** pour ton bot (ex : `Mon Trading News Bot`) — c'est juste l'affichage, tape ce que tu veux et valide.
5. BotFather te demande ensuite un **username** — doit être unique et se terminer par `bot` (ex : `laslo_trading_news_bot`).
6. BotFather te répond avec un message contenant un **token**, qui ressemble à ça :
   ```
   123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   👉 **Copie ce token quelque part**, c'est ton `TELEGRAM_BOT_TOKEN`. Ne le partage à personne (c'est comme un mot de passe pour ton bot).

---

## Étape 2 — Récupérer ton chat_id

Ton `chat_id` est l'identifiant de la conversation où le bot doit t'envoyer les messages.

1. Dans Telegram, cherche ton bot par son username (celui que tu as choisi à l'étape 1) et clique sur "Démarrer" pour lui envoyer un premier message (par exemple juste "salut").
   ⚠️ C'est une étape obligatoire : un bot Telegram ne peut pas écrire à quelqu'un qui ne lui a jamais parlé en premier.
2. Ouvre ce lien dans ton navigateur (remplace `<TOKEN>` par ton vrai token de l'étape 1) :
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
   Exemple : `https://api.telegram.org/bot123456789:AAExxxx.../getUpdates`
3. Tu vas voir une page de texte façon "JSON". Cherche une section qui ressemble à :
   ```json
   "chat":{"id":987654321,"first_name":"Laslo", ...}
   ```
   👉 Le nombre après `"id":` (ici `987654321`) est ton `TELEGRAM_CHAT_ID`. Copie-le.

Si la page est vide (`"result":[]`), c'est que le message envoyé à l'étape 1 n'a pas encore été détecté : réenvoie un message au bot et recharge la page.

---

## Étape 3 — Récupérer ta clé Google Gemini (gratuite)

1. Va sur [aistudio.google.com/apikey](https://aistudio.google.com/apikey) et connecte-toi avec un compte Google (aucune carte bancaire demandée).
2. Clique **Create API key** (ou "Créer une clé API"), puis choisis "Create API key in new project" si on te demande un projet.
3. Copie la clé générée (elle commence par `AIza...`).
   👉 C'est ton `GEMINI_API_KEY`.

Le palier gratuit de Gemini (modèle Flash) offre 1500 requêtes par jour — cet agent en utilise typiquement quelques dizaines par jour, donc aucun risque de dépassement en usage normal.

---

## Étape 4 — Clés gratuites recommandées (Alpha Vantage + EIA + FMP + NewsAPI)

Ces clés sont **optionnelles** : sans elles, l'agent fonctionne quand même (calendrier via ForexFactory) mais sans résultat réel après publication ni veille breaking news.

### Alpha Vantage (résultat réel — USD uniquement)
Seule source trouvée qui donne vraiment le **résultat réel** après publication, gratuitement — mais seulement pour les États-Unis, et seulement pour quelques indicateurs "headline" (pas les versions "Core", qu'Alpha Vantage ne distingue pas de la version globale) :
- Emploi (Non-Farm Employment Change)
- Inflation (CPI m/m et y/y)
- Durable Goods Orders m/m
- Retail Sales m/m
- Unemployment Rate

Pour EUR/GBP, ou pour les versions "Core", l'agent tente un dernier recours : il relit les titres RSS ForexLive/FXStreet (déjà utilisés pour les breaking news) et demande à l'IA s'ils rapportent le chiffre réel de CET indicateur précis (ForexLive publie souvent le chiffre brut en quelques minutes, ex. constaté : *"Conference Board Consumer Confidence for July 90.8 versus 92.3 estimate"*). Ça fonctionne pour les indicateurs suffisamment suivis pour avoir leur propre article — pas pour tous, donc "indisponible" reste possible sur les événements plus confidentiels.

⚠️ **Bug corrigé (à savoir)** : Alpha Vantage peut avoir jusqu'à un mois de retard sur la publication ForexFactory (constaté sur Durable Goods Orders). L'agent vérifie maintenant que le dernier point de données correspond bien au mois attendu (mois de la publication moins un) avant de l'afficher — sinon "indisponible" plutôt qu'un chiffre calculé sur le mauvais mois.

1. Va sur [alphavantage.co/support/#api-key](https://www.alphavantage.co/support/#api-key), entre juste ton email.
2. La clé s'affiche immédiatement. Copie-la → `ALPHAVANTAGE_API_KEY`.
3. Le plan gratuit donne 25 requêtes/jour (1/seconde max) — l'agent met chaque indicateur en cache 20h, donc largement suffisant (5-6 requêtes/jour au maximum).

### EIA (résultat réel — stocks pétroliers hebdo)
Comble le trou d'Alpha Vantage sur le pétrole : Crude Oil Inventories, Gasoline Inventories, Distillate Inventories et Crude Oil Inventories at Cushing (pertinent pour BRENT). Source officielle de l'agence gouvernementale américaine de l'énergie, gratuite, sans limite de quota agressive.

⚠️ Même prudence que pour Alpha Vantage : l'API EIA elle-même peut mettre plus d'une journée à refléter une publication toute fraîche. L'agent vérifie que la donnée la plus récente correspond bien à la semaine attendue avant de l'afficher — sinon "indisponible" plutôt qu'un chiffre calculé sur une semaine plus ancienne. Concrètement : le jour même de la publication, il est possible que ça reste "indisponible" le temps que l'EIA mette son API à jour ; ça devrait apparaître au(x) prochain(s) événement(s) une fois leur API rattrapée.

1. Va sur [eia.gov/opendata/register.php](https://www.eia.gov/opendata/register.php), entre juste ton email.
2. La clé s'affiche/arrive par email. Copie-la → `EIA_API_KEY`.

### FMP (Financial Modeling Prep)
⚠️ **Mise à jour (testé en conditions réelles) : le plan gratuit FMP ne donne plus du tout accès au calendrier économique**, ni via l'ancien endpoint ("réservé aux abonnés antérieurs à août 2025") ni via le nouveau ("réservé aux plans payants"). L'agent l'utilise uniquement comme secours si ForexFactory tombe, et comme filet de sécurité pour le résultat réel (au cas où FMP changerait sa politique) — Alpha Vantage ci-dessus reste la source principale pour ça.

1. Va sur [site.financialmodelingprep.com/register](https://site.financialmodelingprep.com/register) et crée un compte gratuit.
2. Ton **Dashboard** affiche directement ta clé API. Copie-la → `FMP_API_KEY`.

Ou saute complètement cette clé (laisse `FMP_API_KEY` vide dans `.env`) — ça ne change rien d'autre.

### NewsAPI.org
1. Va sur [newsapi.org/register](https://newsapi.org/register) et crée un compte gratuit.
2. Ta clé API s'affiche immédiatement après inscription. Copie-la → `NEWSAPI_KEY`.
3. Le plan gratuit donne 100 requêtes/jour (l'agent en utilise ~96 max avec les réglages par défaut).

---

## Étape 5 — Installer le projet en local

Ces étapes se font dans un **terminal**. Sur Mac : ouvre l'application "Terminal".

```bash
cd chemin/vers/trading-news-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Sur Windows (PowerShell), remplace la 3ème ligne par : `.venv\Scripts\activate`

---

## Étape 6 — Configurer le fichier `.env`

1. Duplique le fichier `.env.example` et renomme la copie en `.env` :
   ```bash
   cp .env.example .env
   ```
2. Ouvre `.env` avec un éditeur de texte (Bloc-notes, TextEdit, VS Code...) et colle les valeurs récupérées aux étapes précédentes :
   ```
   TELEGRAM_BOT_TOKEN=123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   TELEGRAM_CHAT_ID=987654321
   GEMINI_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   FMP_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   NEWSAPI_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
3. Enregistre le fichier.

`TRADING_PAIRS` (déjà pré-rempli avec `XAUUSD,EURUSD,GBPUSD,US30`) peut être modifié dans ce même fichier si tu veux suivre d'autres paires — voir section Personnalisation.

---

## Étape 7 — Vérifier que tout fonctionne (`--test`)

Toujours dans le terminal (venv activé) :

```bash
python main.py --test
```

Tu dois recevoir **6 messages sur Telegram** en quelques secondes (message de connexion, exemple de résumé quotidien, exemple d'alerte avant/après une news, exemple de breaking news, message de fin). Le terminal t'indique aussi, ligne par ligne, ce qui fonctionne ou non — s'il y a un ❌, relis le message d'erreur : il te dit quelle variable du `.env` vérifier.

---

## Étape 8 — Lancer l'agent en local (test avant déploiement)

```bash
python main.py
```

L'agent tourne maintenant en continu dans ton terminal : tu dois recevoir un message Telegram "🟢 Agent trading news démarré". Laisse tourner quelques minutes pour vérifier qu'il n'y a pas d'erreur dans le terminal, puis arrête-le avec `Ctrl+C`.

👉 Tant que ce terminal est ouvert, l'agent tourne. Si tu fermes le terminal ou éteins ton PC, il s'arrête — c'est pour ça qu'on le déploie en ligne à l'étape suivante.

---

## Étape 9 — Faire tourner l'agent 24/7 gratuitement (même PC éteint)

On utilise **Render.com** (offre gratuite, aucune carte bancaire requise pour ce type de service). Une astuce est nécessaire pour éviter que Render ne mette le service en veille (voir 9.4) — sans elle, le plan gratuit fonctionne mais s'endort après 15 minutes d'inactivité.

### 9.1 — Mettre le code sur GitHub

Render déploie depuis un dépôt Git.

1. Crée un compte sur [github.com](https://github.com) si tu n'en as pas.
2. Crée un nouveau dépôt (bouton vert "New") — mets-le en **Private** (recommandé, même si `.env` n'y sera jamais poussé grâce au `.gitignore` déjà inclus dans le projet).
3. Dans le terminal, à la racine du projet :
   ```bash
   git init
   git add .
   git commit -m "Premier envoi de l'agent"
   git branch -M main
   git remote add origin https://github.com/TON-USERNAME/TON-DEPOT.git
   git push -u origin main
   ```
   (remplace l'URL par celle de ton dépôt, affichée sur la page GitHub après création)
4. Vérifie sur GitHub que le fichier `.env` n'apparaît **pas** dans le dépôt (seul `.env.example` doit y être).

### 9.2 — Créer le service sur Render

1. Va sur [render.com](https://render.com) et crée un compte (tu peux te connecter directement avec GitHub).
2. Clique **New +** → **Web Service**.
3. Sélectionne ton dépôt `trading-news-agent`.
4. Renseigne :
   - **Name** : `trading-news-agent` (ou ce que tu veux)
   - **Runtime** : `Python 3`
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `python main.py`
   - **Plan** : `Free`
5. Ne clique pas encore sur "Create" — passe à l'étape suivante pour ajouter les clés.

### 9.3 — Ajouter tes clés secrètes

Toujours sur la page de création, section **Environment Variables** : ajoute une variable par ligne de ton `.env` (mêmes noms, mêmes valeurs) :

```
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
GEMINI_API_KEY
FMP_API_KEY
NEWSAPI_KEY
```

Puis clique **Create Web Service**. Render installe et lance l'agent (compte 2-3 minutes) — surveille l'onglet **Logs** : tu dois y voir `Scheduler démarré` et recevoir le message Telegram de démarrage.

> 💡 Raccourci : le projet contient un fichier `render.yaml`. Si tu préfères, tu peux utiliser **New + → Blueprint** sur Render et pointer vers ton dépôt : Render remplira la configuration automatiquement (il te demandera juste de coller tes clés).

### 9.4 — Empêcher la mise en veille (obligatoire pour du vrai 24/7)

Le plan gratuit Render met un service en veille après 15 minutes sans requête HTTP entrante (il se réveille au message suivant, mais tu perdrais des alertes en attendant). L'agent inclut déjà un petit serveur qui répond "OK" pour cette raison — il faut juste le "pinguer" régulièrement depuis l'extérieur, gratuitement :

1. Une fois ton service Render créé, copie son URL publique (visible en haut de la page du service, du type `https://trading-news-agent-xxxx.onrender.com`).
2. Va sur [cron-job.org](https://cron-job.org) (gratuit, sans carte bancaire) et crée un compte.
3. Crée un nouveau **cronjob** :
   - URL : colle l'URL de ton service Render
   - Intervalle : toutes les **5 minutes**
4. Enregistre. C'est tout — ce ping garde ton agent éveillé en permanence, gratuitement, indéfiniment.

### ⚠️ Limite importante à connaître (plan gratuit)

Sur le plan gratuit, le disque de Render est **temporaire** : si Render redémarre ou redéploie ton service (mise à jour du code, maintenance Render...), le fichier `alerts.db` repart de zéro. Concrètement : ça ne casse rien, mais juste après un redémarrage, l'agent pourrait renvoyer une alerte pour une news déjà annoncée juste avant le redémarrage. C'est rare (les redémarrages ne sont pas fréquents) et sans danger, juste bon à savoir. Le contenu réellement envoyé sur Telegram, lui, peut être préservé malgré ces redémarrages via le journal durable (voir section "Journal durable des messages Telegram").

---

## Monétiser : diffuser vers un canal payant

Tu peux garder tes alertes personnelles privées (comme configuré) et **en plus** diffuser les mêmes alertes vers un canal Telegram séparé, payant, ouvert à d'autres personnes.

⚠️ **Avant de facturer qui que ce soit** : diffuser des news économiques accompagnées d'un biais IA généré automatiquement peut, selon comment c'est présenté et le pays, être encadré par la réglementation sur le conseil en investissement. Ce n'est pas la même chose que vendre des "signaux de trading", mais ça reste une zone grise selon la présentation — vérifie ça avec un professionnel (avocat/comptable spécialisé) avant de lancer une offre payante réelle. Ajoute a minima un disclaimer clair ("contenu informatif, pas un conseil personnalisé, aucune garantie") dans la description de ton canal.

### 1. Créer le canal et y ajouter le bot
1. Dans Telegram, crée un nouveau **canal** (pas un groupe) : Menu → Nouveau canal → donne-lui un nom → mets-le en **Privé**.
2. Va dans les paramètres du canal → **Administrateurs** → **Ajouter un administrateur** → cherche ton bot (le même que celui créé à l'Étape 1) → donne-lui le droit **Publier des messages**.
3. Poste n'importe quel message dans le canal (juste pour que Telegram enregistre une activité).

### 2. Récupérer l'ID du canal
1. Ouvre dans ton navigateur (remplace `<TOKEN>` par ton `TELEGRAM_BOT_TOKEN`) :
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
2. Cherche une section `"channel_post":{"chat":{"id":-100xxxxxxxxxx, ...}}` — le nombre (négatif, il commence par `-100`) est ton `TELEGRAM_CHANNEL_ID`.
3. Ajoute-le dans `.env` (en local) et dans les variables d'environnement Render :
   ```
   TELEGRAM_CHANNEL_ID=-100xxxxxxxxxx
   ```
4. Relance `python main.py --test` pour vérifier : le terminal doit indiquer "Canal payant configuré", et les messages de test doivent apparaître aussi dans le canal.

### 3. Activer le paiement (Telegram Stars — aucun code requis)
1. Dans les paramètres du canal → **Type de canal** → **Gérer les liens d'invitation** → **Créer un nouveau lien**.
2. Active **"Frais mensuel"** (Require Monthly Fee) et choisis le prix en Telegram Stars.
3. Partage ce lien d'invitation à tes futurs abonnés — Telegram gère 100% de la facturation mensuelle, l'ajout et le retrait automatique des abonnés qui ne paient plus. Tu reçois 100% des Stars (convertibles ensuite via l'app Telegram).

Le code ne change rien d'autre : tes alertes personnelles (chat perso) continuent d'arriver normalement, et sont maintenant aussi copiées vers le canal. Les messages internes (démarrage de l'agent, erreurs techniques) restent perso uniquement — les abonnés du canal ne voient que le contenu (résumé quotidien, débrief du soir, alertes avant/après news, breaking news).

---

## Scripts vidéo courts (TikTok/Reels/Shorts)

L'agent peut transformer les données qu'il collecte déjà (calendrier économique, breaking news) en scripts prêts à tourner pour des vidéos courtes. Il ne publie rien nulle part : il écrit des fichiers dans `video_output/<date>/`, à toi de les relire et de tourner.

**`DEBRIEF` est LE format généré automatiquement chaque soir** — le récapitulatif complet de la journée (événements macro publiés + breaking news), 75 à 90 secondes. 5 autres formats existent pour un usage ponctuel, à la demande uniquement :

Structure imposée du DEBRIEF (texte ET vidéo) : un sujet par bloc (jamais deux événements mélangés dans le même bloc), tous les blocs "événements du jour" d'abord, puis tous les blocs "breaking news" ensuite — jamais entremêlés — avec une phrase de transition parlée qui annonce le changement, et une chute qui se termine toujours en donnant un cap (quoi surveiller dans les prochains jours). Ce classement est vérifié et réordonné par le code après coup (pas seulement demandé à l'IA) : impossible d'obtenir un ordre mélangé même si le modèle se trompe.

| Format | Durée | Contenu | Génération |
|---|---|---|---|
| `DEBRIEF` | 75-90s | Le débrief complet de la journée (événements + breaking news) | **Automatique, chaque soir** |
| `REACTION` | 45s | Réaction factuelle à une publication économique majeure du jour | À la demande |
| `POURQUOI` | 45-60s | Explique le mécanisme derrière un mouvement de marché du jour | À la demande |
| `PEDAGO` | 30s | Définit un concept économique simplement | À la demande |
| `FACTCHECK` | 45s | Remet en contexte un titre de presse à partir des vrais chiffres | À la demande |
| `SEMAINE` | 60s | Calendrier commenté des événements de la semaine à venir | À la demande |

Chaque format a son propre prompt éditable dans `video_templates/*.txt` (zéro texte éditorial codé en dur — modifie le `.txt`, pas le `.py`).

### Utilisation

```bash
python video_scripts.py --format DEBRIEF                      # le débrief du jour
python video_scripts.py --format SEMAINE --date 2026-07-20    # une autre date, un autre format
python video_scripts.py --format PEDAGO --concept "Taux BCE"  # concept imposé
python video_scripts.py --format ALL --dry-run                # aperçu de tous les formats, rien n'est écrit
python video_scripts.py --format DEBRIEF --notes "Insiste sur le pétrole"  # remarque prise en compte par l'IA
```

`--notes` te permet de glisser une remarque libre (angle à privilégier, sujet à
mettre en avant, ton à adopter...) qui est injectée dans le prompt envoyé à
l'IA pour ce script précis — sans toucher aux fichiers `.txt` dans
`video_templates/`. Fonctionne avec tous les formats, combinable avec `--render`.

Chaque script génère un `.md` (lisible tel quel sur mobile) et un `.json` (structure complète, pour un usage automatisé ultérieur) dans `video_output/<YYYY-MM-DD>/<format>.md`/`.json`.

`DEBRIEF` est généré automatiquement chaque soir après le débrief texte (23h30 par défaut, réglable via `VIDEO_SCRIPTS_HOUR`/`VIDEO_SCRIPTS_MINUTE` dans `config.py`) — mais uniquement s'il existe au moins un événement du jour avec un résultat publié (`actual`) OU une breaking news captée. Vu la limite connue sur le "résultat réel" (voir "Limites honnêtes à connaître" plus bas), ça peut rester silencieux certains soirs tant que cette donnée n'est pas fiable côté source — ce n'est pas un bug : le module logue "aucune donnée exploitable" et ne génère rien plutôt que d'inventer un script vide. **Seul le texte est généré automatiquement** (le rendu vidéo reste local, voir plus bas) : `python video_scripts.py --format DEBRIEF --render` le soir pour obtenir la vidéo.

### Avant ta première génération réelle

Édite dans `config.py` :
- **`VIDEO_CTA_TEXT`** : la formule d'appel à l'action redite sur chaque script — la valeur par défaut contient un `[NOM_DU_CANAL]` à remplacer.
- **`VIDEO_DISCLAIMER`** : ajouté en fin de légende de chaque script (reprend la formule recommandée en section "Monétiser" ci-dessus).

Ces deux valeurs sont injectées par le code, jamais par l'IA : elles apparaissent mot pour mot sur chaque script, quoi qu'ait produit le modèle.

### Générer la vidéo (voix + visuel), pas juste le script

Le flag `--render` transforme le script en vraie vidéo verticale (1080×1920, mp4) :
- **Voix** : synthèse française gratuite (`edge-tts`, aucune clé requise).
- **Sous-titres dynamiques** : le texte à l'écran défile par petits groupes de
  mots (~1s chacun), synchros avec la voix — jamais une légende figée pour tout
  un bloc. edge-tts ne fournit pas de vrai timing mot-par-mot pour le français
  (seulement phrase par phrase), donc le rythme des mots à l'intérieur d'une
  phrase est interpolé à partir de leur longueur — pas exact à la milliseconde
  près, mais ça défile.
- **Fond vidéo en boucle qui suit le sujet** (optionnel, voir juste en dessous) :
  chaque bloc du script a son propre mot-clé de fond généré par l'IA en fonction
  de CE dont il parle à ce moment précis (ex. un bloc sur la BCE cherche une
  vidéo différente d'un bloc sur le pétrole) — pas un seul fond fixe pour toute
  la vidéo. Fond uni sombre par défaut sans clé Pexels.
- **Repères de structure** (DEBRIEF uniquement) : une étiquette en haut de
  chaque bloc ("BREAKING NEWS" en rouge sur les blocs développés, "RÉCAP ÉCO DU
  JOUR" en ambre sur le bloc final qui récapitule tout le calendrier économique
  du jour) plus une courte carte de transition silencieuse au moment où la
  vidéo bascule vers le récap — pour que le découpage s'entende et se voie, pas
  juste un enchaînement de sujets sans rapport apparent.
- **Carte de fin dédiée** pour le CTA : contrairement au reste, un écran stable
  (pas de fond vidéo, pas de sous-titres qui défilent) — un appel à l'action doit
  rester lisible, pas clignoter.

Toujours **0€** : pas d'API payante.

```bash
pip install -r requirements-video.txt   # une fois — dépendances lourdes, séparées
python video_scripts.py --format DEBRIEF --render
```

**Fond vidéo animé (optionnel)** : sans rien faire, le fond est uni. Pour un
fond vidéo en boucle, ajoute une clé gratuite dans `.env` :
```
# Clé gratuite sur https://www.pexels.com/api/ (200 req/h, 20 000/mois)
PEXELS_API_KEY=
```
Le hook et la chute utilisent le thème générique du format
(`STOCK_FOOTAGE_THEME_BY_FORMAT` dans `config.py`, éditable) ; chaque bloc du
corps utilise son propre mot-clé généré par l'IA pour ce bloc précis (voir
`video_templates/_system.txt`, champ `visual_keyword`). Les clips sont
téléchargés et mis en cache localement dans `stock_footage/<mot-clé>/` — une
petite réserve pour un mot-clé de contenu ponctuel (peut ne jamais revenir),
une réserve plus large pour le thème générique du format (réutilisé tous les
soirs). Un rendu classique ne refait donc pas d'appel réseau pour un mot-clé
déjà rencontré. Licence Pexels : usage libre, y compris commercial, sans
attribution requise.

À savoir :
- **Toujours en local, jamais sur Render** : `requirements-video.txt` n'est pas
  installé sur le service déployé, et le job automatique de 23h30 ne génère que le
  texte. Le rendu vidéo est trop lourd (CPU/mémoire) pour le plan gratuit Render.
- **`--render` et `--dry-run` sont incompatibles** (dry-run n'écrit rien).
- **Pas besoin d'installer ffmpeg toi-même** : `imageio-ffmpeg` (dans
  `requirements-video.txt`) fournit un binaire ffmpeg autonome automatiquement.
- **Changer de voix** : `edge-tts --list-voices | grep fr-FR` liste les voix
  françaises disponibles, puis modifie `VIDEO_TTS_VOICE` dans `config.py`.
- Un `DEBRIEF` (4 à 7 blocs, jusqu'à 90s) prend plus longtemps à rendre qu'un
  format court : plus de segments à synthétiser et potentiellement plus de
  mots-clés de fond différents à chercher sur Pexels la première fois. Compte
  quelques minutes plutôt qu'une pour les formats courts (~45s).

### Raccourci bureau — générer la vidéo sans passer par le Terminal

Un double-clic sur **`Générer Vidéo Débrief.app`** (sur ton Bureau) suffit pour
produire la vidéo DEBRIEF d'un jour donné, sans ouvrir de Terminal :

1. Une première fenêtre demande la **date** (pré-remplie avec la date du jour —
   modifiable si tu veux régénérer un autre jour).
2. Une deuxième fenêtre te propose d'ajouter une **remarque optionnelle**
   (ex. "insiste sur le pétrole", "reste optimiste sur la chute") — laisse vide
   si tu n'as rien de particulier à préciser. C'est exactement le `--notes`
   décrit ci-dessus.
3. L'app rafraîchit d'abord le calendrier économique local (voir
   `generate_debrief.sh` à la racine du projet), puis génère et rend la vidéo.
   Compte quelques minutes.
4. À la fin :
   - **Si une vidéo a été produite** : une notification macOS te le confirme et
     le fichier s'ouvre directement dans le Finder (`video_output/<date>/debrief.mp4`).
   - **Si aucune vidéo n'a été produite** : une fenêtre t'explique que c'est très
     probablement parce qu'il n'y avait aucune donnée exploitable ce jour-là
     (voir "Limites honnêtes à connaître" plus bas — ce n'est pas un bug), avec
     le détail technique en dessous si besoin.

À savoir :
- L'app appelle `generate_debrief.sh`, qui synchronise d'abord avec Render
  (voir "Synchroniser avec Render" ci-dessous, si configuré) puis rafraîchit
  le calendrier en local — la vidéo reflète alors ce que le bot déployé a
  réellement capté et envoyé sur Telegram, pas une reconstruction partielle.
- Aucune donnée sensible n'est demandée par l'app — elle réutilise simplement
  ton `.env` et ta base SQLite locale existants.
- Pour changer la date ou les remarques par défaut, ou modifier le comportement,
  le script source est `GenerateDebrief.applescript` (à ouvrir avec
  l'app **Éditeur de scripts** sur macOS, puis "Exporter..." en type
  "Application" pour recompiler après modification).

### Synchroniser avec Render (recommandé)

Par défaut, générer la vidéo en local ne connaît que ce qu'un simple
rafraîchissement du calendrier peut trouver — pas les résultats confirmés ni
les breaking news que le bot **déployé sur Render** a réellement captés et
envoyés sur Telegram au fil de la journée (ces deux bases ne se parlent pas
nativement, voir "Limites honnêtes à connaître"). La synchro comble cet écart :
`generate_debrief.sh` va chercher les vraies données du jour directement sur
ton service Render avant de générer, via un petit point d'accès protégé par
une clé secrète (jamais tes données exposées publiquement).

**Mise en place (une fois) :**
1. Une clé `SYNC_API_KEY` a déjà été générée et ajoutée à ton `.env` local.
2. Va sur [dashboard.render.com](https://dashboard.render.com), ouvre ton
   service, onglet **Environment**, et ajoute une variable `SYNC_API_KEY` avec
   **exactement la même valeur** que celle dans ton `.env` local. Sauvegarde
   (Render redéploie automatiquement).
3. Dans ton `.env` local, renseigne `RENDER_SYNC_URL` avec l'URL publique de
   ton service (visible en haut de la page du service sur le dashboard, du
   type `https://trading-news-agent-xxxx.onrender.com`).

Une fois ces trois valeurs en place, `generate_debrief.sh` s'en sert
automatiquement — rien d'autre à faire. Si `RENDER_SYNC_URL` ou
`SYNC_API_KEY` est vide, ou si le service est injoignable (endormi, clé qui ne
correspond pas...), la synchro est simplement sautée et le script continue
avec le rafraîchissement local habituel — jamais bloquant.

### Journal durable des messages Telegram

Problème réglé par cette section : sur le plan gratuit Render, `alerts.db`
repart de zéro à chaque redémarrage (voir "Limites honnêtes" plus bas) — tout
ce que le bot avait capté dans la journée peut disparaître avant que tu aies
pu en faire une vidéo. Le journal durable règle ça en copiant chaque message
réellement envoyé sur Telegram dans une base à part qui, elle, ne s'efface
jamais.

**Mise en place (une fois, gratuit, ~2 minutes) :**
1. Va sur [app.turso.tech/signup](https://app.turso.tech/signup) et crée un
   compte (possible avec ton compte GitHub). Aucune carte bancaire requise.
2. Clique **Create Database**, donne-lui un nom, choisis une région proche de
   toi, laisse le reste par défaut.
3. Une fois créée, ouvre la base et récupère deux valeurs : l'**URL** (commence
   par `libsql://...`, bouton "Connect") et un **token** (bouton "Create
   Token"/"Generate Token").
4. Ajoute-les dans ton `.env` local (`TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`)
   **et** dans les variables d'environnement de ton service Render (dashboard
   → Environment, mêmes noms, mêmes valeurs — jamais dans `render.yaml`).

Une fois ces deux valeurs en place des deux côtés, chaque message envoyé par
`telegram_bot.broadcast()` (résumés, alertes, breaking news) est
automatiquement archivé, en plus d'être envoyé — l'envoi Telegram lui-même
n'est jamais affecté même si le journal est indisponible. Vide des deux
côtés = journal simplement désactivé, aucun changement de comportement.

À savoir : la génération vidéo n'exploite pas encore ce journal directement
(il vient d'être mis en place, le temps qu'il accumule du contenu réel) — pour
l'instant c'est une sauvegarde qui tourne en parallèle des autres sources.

---

## Personnaliser l'agent

Tout se règle dans `.env` (valeurs) ou `config.py` (réglages avancés) :

- **Paires suivies** : variable `TRADING_PAIRS` dans `.env` (par défaut : `XAUUSD,EURUSD,GBPUSD,US30,BTCUSD,ETHUSD,DAX,SP500,NASDAQ,BRENT,CAC40`). Si tu ajoutes une paire (ex : `SOLUSD`, `USDJPY`), ajoute aussi son mapping de devises dans `PAIR_CURRENCIES` en haut de `config.py` — sinon elle apparaîtra dans les résumés mais ne recevra jamais de biais IA.
- **Horaire du résumé quotidien** : `DAILY_SUMMARY_HOUR` / `DAILY_SUMMARY_MINUTE` dans `config.py` (minuit par défaut).
- **Horaire du débrief du soir** : `EVENING_DEBRIEF_HOUR` / `EVENING_DEBRIEF_MINUTE` dans `config.py` (23h30 par défaut, calé sur la clôture de la session de New York) — récapitule les news publiées et les breaking news de la journée avec une analyse IA rétrospective.
- **Horaire de génération des scripts vidéo** : `VIDEO_SCRIPTS_HOUR` / `VIDEO_SCRIPTS_MINUTE` dans `config.py` (23h30 par défaut, après le débrief) — voir section "Scripts vidéo courts" plus haut pour le CTA/disclaimer à personnaliser avant la première utilisation.
- **Délai de l'alerte "avant news"** : `ALERT_BEFORE_MINUTES` (30 min par défaut).
- **Taille max du lot soumis à la reclassification IA** : `MAX_RECLASSIFY_BATCH` dans `ai_analyzer.py` (60 par défaut, une semaine chargée peut approcher les 40-45 events "Low" — augmente si le log affiche un avertissement de troncature).
- **Fraîcheur du cache Alpha Vantage** : `ALPHAVANTAGE_CACHE_MAX_AGE_HOURS` dans `config.py` (20h par défaut — laisse tel quel sauf si tu ajoutes beaucoup d'indicateurs et approches le quota de 25 requêtes/jour).
- **Mots-clés de la veille breaking news** : variable `BREAKING_NEWS_KEYWORDS` dans `.env`, séparés par des virgules. Couvre à la fois les chocs majeurs (guerre, démission, hack crypto...) et l'économie plus "ordinaire" non planifiée (licenciements, confiance des consommateurs, pénurie de puces...) — le tri IA (`ai_analyzer.filter_breaking_news`) calibre le niveau de danger en conséquence (🟢 pour du simplement informatif, pas systématiquement alarmiste).
- **Fréquence de la veille breaking news** : `BREAKING_NEWS_INTERVAL_MINUTES` dans `config.py` (15 min par défaut — ne descends pas trop bas, le quota NewsAPI gratuit est de 100 requêtes/jour).

Après modification, redéploie sur Render (un `git push` suffit, Render redéploie automatiquement) ou relance en local.

---

## Limites honnêtes à connaître

Pour que tu saches exactement ce que fait (et ne fait pas) l'agent :

- **Breaking news = best-effort, pas du vrai temps réel.** Il n'existe aucune API gratuite fiable pour capter un tweet ou une déclaration à la seconde près (l'API X/Twitter coûte ~100$/mois). L'agent combine plusieurs sources : flux RSS spécialisés forex (InvestingLive, FXStreet — 25-60 min de délai constaté, les plus fiables), GDELT (souvent bloqué depuis l'IP de Render, constaté) et NewsAPI (plan gratuit avec ~24h de délai, constaté — quasi inutile pour du temps réel mais gardé en secours). Un tri par IA limite les fausses alertes, mais des faux négatifs (rien détecté) et faux positifs (alerte peu pertinente) restent possibles.
- **Le "résultat réel" après publication reste best-effort, pas garanti à 100%.** Le calendrier ForexFactory ne publie jamais le résultat réel, et FMP a retiré cette donnée de son plan gratuit (testé, accès refusé même avec une clé valide). Quatre sources en cascade comblent le trou : Alpha Vantage (NFP, CPI, Durable Goods Orders, Retail Sales, Unemployment Rate — USD headline uniquement), EIA (stocks pétroliers hebdo — Crude/Gasoline/Distillate/Cushing), FMP en secours, puis en dernier recours les titres RSS ForexLive/FXStreet relus par l'IA (couvre potentiellement toute devise/indicateur, à condition qu'un article rapporte spécifiquement son chiffre — constaté que ça fonctionne pour des indicateurs suivis comme CB Consumer Confidence). Pour un événement plus confidentiel qu'aucune des quatre sources ne couvre, ça reste "indisponible".
- **Le calendrier ForexFactory se décale le week-end.** Le flux gratuit utilisé ne couvre que la semaine calendaire en cours ; le nouveau contenu de la semaine suivante apparaît généralement dimanche soir/lundi matin.
- **La classification "impact" de ForexFactory sous-évalue parfois des events réellement suivis** (constaté : Durable Goods Orders, Ifo Business Climate classés "Low" alors qu'Investing.com les classe plus haut). L'agent fait relire chaque event "Low" par l'IA à chaque rafraîchissement du calendrier (toutes les 6h) : ceux qu'elle juge sous-évalués sont remontés en Medium/High et marqués 🤖 dans les messages pour rester distinguables d'une classification ForexFactory native. Ce n'est pas une donnée de marché en temps réel, juste le jugement général de l'IA sur ce qui compte habituellement en day trading — à prendre comme un filet de sécurité, pas une garantie absolue.
- **Crypto (BTC/ETH), indices (US30/SP500/NASDAQ/DAX/CAC40) et pétrole (BRENT) n'ont PAS de calendrier économique dédié.** Il n'existe pas de source gratuite équivalente à ForexFactory pour ces instruments (ex : pas d'heure précise pour "prochaine décision SEC sur un ETF"). Ils reçoivent : (1) le biais IA généré automatiquement à chaque news USD ou EUR existante (le crypto et les indices US sont très corrélés au dollar), et (2) une couverture best-effort via la veille breaking news (mots-clés SEC/CFTC/OPEP/exchange hack...). En clair : pas d'alerte "30 min avant" programmée pour ces instruments, seulement des alertes réactives.
- **L'IA peut se tromper.** Le biais directionnel et le niveau de danger sont des indications générées automatiquement, pas des conseils financiers personnalisés — la décision de trader reste la tienne.
- **Les scripts vidéo générés sur Render peuvent disparaître avant que tu les récupères.** Le disque gratuit de Render est éphémère (voir Étape 9.4) : si le job `video_scripts` tourne sur le service déployé plutôt qu'en local, un redéploiement ou redémarrage peut effacer `video_output/` avant consultation. Le module écrit volontairement des fichiers et ne publie nulle part (voir section "Scripts vidéo courts") — pense à les récupérer rapidement, ou lance la génération en local si tu préfères des fichiers durables.
- **La base locale et celle de Render ne se parlent pas nativement.** Le rendu vidéo se fait toujours en local (dépendances trop lourdes pour le plan gratuit Render), mais c'est le service déployé qui capte les vraies breaking news et confirme les résultats publiés en continu — deux bases SQLite séparées, par défaut. Voir "Synchroniser avec Render" (section "Scripts vidéo courts") pour combler cet écart.

---

## Combien ça coûte, vraiment ?

**0 € — tout est sur un palier gratuit permanent, aucune carte bancaire nulle part.**

- **Render + cron-job.org + ForexFactory + GDELT + Turso** : 0 €, sans limite de temps.
- **FMP + NewsAPI** : 0 € (plans gratuits). Pour FMP, le plan gratuit ne couvre plus le calendrier économique (voir "Limites honnêtes" plus haut) — la clé reste gratuite à créer, elle est juste actuellement sans effet pour cette fonctionnalité précise.
- **Google Gemini** : 0 € (1500 requêtes/jour offertes en continu ; cet agent en utilise typiquement quelques dizaines par jour avec les réglages par défaut — résumé quotidien + débrief du soir (2 appels/jour), alertes calendrier ~5-15/semaine, veille breaking news toutes les 15 min mais IA appelée seulement si un nouvel article correspond aux mots-clés).

⚠️ Nuance honnête : un palier "gratuit" chez un fournisseur cloud peut toujours changer dans le futur (Google comme les autres). Si ça arrivait, `--test` te le signalera immédiatement (❌ sur l'appel IA) — il suffira d'ajuster `GEMINI_MODEL` dans `.env` ou de vérifier les conditions à jour sur [ai.google.dev/pricing](https://ai.google.dev/pricing).

---

## Dépannage rapide

| Problème | Solution |
|---|---|
| Aucun message reçu avec `--test` | Vérifie `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` dans `.env`. As-tu bien envoyé un premier message à ton bot (Étape 2) ? |
| "Pas de réponse IA" dans `--test` | Vérifie `GEMINI_API_KEY` dans `.env`. Si la clé est bonne mais que l'erreur persiste (voir `agent.log`), le nom de modèle `GEMINI_MODEL` a peut-être changé côté Google — vérifie sur [aistudio.google.com](https://aistudio.google.com) |
| L'agent tourne mais rien ne se passe après plusieurs heures | Regarde le fichier `agent.log` (ou l'onglet Logs sur Render) pour voir les erreurs |
| Alertes en double après un redéploiement Render | Comportement normal du plan gratuit (voir "Limite importante" à l'Étape 9.4) |
| Le service Render semble "endormi" / lent à réagir | Vérifie que le cronjob cron-job.org (Étape 9.4) est bien actif |
| "Synchro Render sautée ou indisponible" dans `generate_debrief.sh` | Vérifie que `RENDER_SYNC_URL` et `SYNC_API_KEY` sont bien renseignées dans `.env`, que `SYNC_API_KEY` est **identique** côté dashboard Render (onglet Environment), et que le service n'est pas endormi (voir ligne au-dessus) |
| "Journal Turso indisponible" dans les logs | Vérifie que `TURSO_DATABASE_URL` et `TURSO_AUTH_TOKEN` sont bien renseignées **des deux côtés** (`.env` local ET variables d'environnement Render) — sans danger si oublié, le journal est juste désactivé |

---

## Structure du projet

```
trading-news-agent/
├── main.py              # Point d'entrée + scheduler (APScheduler)
├── config.py             # Réglages (paires, horaires, seuils, clés)
├── db.py                  # Base SQLite (cache calendrier + anti-doublons)
├── calendar_fetcher.py    # Calendrier économique (ForexFactory + fallback FMP)
├── news_watcher.py        # Veille breaking news (GDELT + NewsAPI)
├── ai_analyzer.py         # Analyse IA (Google Gemini) : résumé, biais, danger
├── telegram_bot.py        # Envoi + mise en forme des messages Telegram
├── message_log.py         # Journal durable des messages envoyés (Turso, voir README)
├── render_sync.py         # Synchro locale <- Render (voir "Synchroniser avec Render")
├── video_scripts.py       # Génération de scripts vidéo courts (TikTok/Reels/Shorts)
├── video_renderer.py      # Rendu voix + visuel (--render), local uniquement
├── stock_footage.py       # Fond vidéo Pexels (optionnel) : recherche + cache local
├── video_templates/       # Un prompt éditable par format (REACTION, PEDAGO, ...)
├── video_output/          # Scripts + vidéos générés (gitignored), un dossier par date
├── stock_footage/         # Clips de fond mis en cache (gitignored)
├── tests/                 # Tests pytest
├── requirements.txt
├── requirements-dev.txt   # + pytest, pour lancer les tests
├── requirements-video.txt # + edge-tts/moviepy/matplotlib, pour --render (local)
├── .env.example
├── runtime.txt             # Version Python pour Render
└── render.yaml             # Config de déploiement Render (optionnel)
```
