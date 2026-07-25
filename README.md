# 🤖 Agent de veille news économiques pour trading

Cet agent surveille en continu (24h/24, 7j/7) :
- le **calendrier économique** (USD, EUR, GBP — impact fort 🔴 et moyen 🟠)
- les **news "choc"** hors calendrier (déclaration surprise, tweet à impact, conflit, régulation crypto, choc pétrolier, etc.)

... et t'envoie des **alertes Telegram** avec une analyse IA (Google Gemini) : résumé, biais probable sur tes paires (par défaut XAUUSD, EURUSD, GBPUSD, US30, BTCUSD, ETHUSD, DAX, SP500, NASDAQ, BRENT, CAC40 — 100% configurable), et niveau de danger pour trader.

⚠️ Important à comprendre : seuls USD/EUR/GBP ont un vrai **calendrier économique** (heure précise, prévision/résultat). Le crypto, les indices et le pétrole n'ont pas d'équivalent gratuit fiable — ils profitent de deux choses : (1) le biais IA généré à chaque news USD/EUR déjà couverte (le crypto et les indices US réagissent fortement aux news USD), et (2) la veille "breaking news" étendue à leurs propres déclencheurs (régulation SEC/CFTC, hack d'exchange, décision OPEP...). Voir "Limites honnêtes" plus bas.

Il tourne indépendamment de ton ordinateur une fois déployé (voir Étape 9).

---

## Avant de commencer : ce qu'il te faut

**100% gratuit, sans limite de temps, sans carte bancaire.** Tu vas créer 5 comptes/clés au total, ça prend environ 20 minutes :

| # | Compte | Gratuit ? | Obligatoire ? |
|---|--------|-----------|----------------|
| 1 | Bot Telegram (@BotFather) | ✅ | Oui |
| 2 | Google AI Studio (Gemini) | ✅ Palier gratuit permanent | Oui |
| 3 | Financial Modeling Prep (FMP) | ✅ mais fonctionnalité limitée (voir Étape 4) | Facultatif |
| 4 | NewsAPI.org | ✅ | Recommandé |
| 5 | Render.com (hébergement 24/7) | ✅ | Oui, pour le 24/7 |

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

## Étape 4 — Clés gratuites recommandées (FMP + NewsAPI)

Ces deux clés sont **optionnelles** : sans elles, l'agent fonctionne quand même (calendrier via ForexFactory) mais sans veille breaking news (voir NewsAPI ci-dessous).

### FMP (Financial Modeling Prep)
⚠️ **Mise à jour (testé en conditions réelles) : le plan gratuit FMP ne donne plus du tout accès au calendrier économique**, ni via l'ancien endpoint ("réservé aux abonnés antérieurs à août 2025") ni via le nouveau ("réservé aux plans payants"). Concrètement, tant que tu n'as pas un compte FMP payant, le **"résultat réel" après publication restera indisponible** — l'agent continue de fonctionner normalement pour tout le reste (calendrier ForexFactory, prévision/précédent, alertes avant/après, IA), juste sans cette donnée précise.

Tu peux quand même créer une clé gratuite au cas où FMP changerait sa politique (l'agent l'utilisera automatiquement si ça redevient accessible, sans rien à reconfigurer) :
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

Sur le plan gratuit, le disque de Render est **temporaire** : si Render redémarre ou redéploie ton service (mise à jour du code, maintenance Render...), le fichier `alerts.db` repart de zéro. Concrètement : ça ne casse rien, mais juste après un redémarrage, l'agent pourrait renvoyer une alerte pour une news déjà annoncée juste avant le redémarrage. C'est rare (les redémarrages ne sont pas fréquents) et sans danger, juste bon à savoir.

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

Le code ne change rien d'autre : tes alertes personnelles (chat perso) continuent d'arriver normalement, et sont maintenant aussi copiées vers le canal. Les messages internes (démarrage de l'agent, erreurs techniques) restent perso uniquement — les abonnés du canal ne voient que le contenu (résumé quotidien, alertes avant/après news, breaking news).

---

## Personnaliser l'agent

Tout se règle dans `.env` (valeurs) ou `config.py` (réglages avancés) :

- **Paires suivies** : variable `TRADING_PAIRS` dans `.env` (par défaut : `XAUUSD,EURUSD,GBPUSD,US30,BTCUSD,ETHUSD,DAX,SP500,NASDAQ,BRENT,CAC40`). Si tu ajoutes une paire (ex : `SOLUSD`, `USDJPY`), ajoute aussi son mapping de devises dans `PAIR_CURRENCIES` en haut de `config.py` — sinon elle apparaîtra dans les résumés mais ne recevra jamais de biais IA.
- **Horaire du résumé quotidien** : `DAILY_SUMMARY_HOUR` / `DAILY_SUMMARY_MINUTE` dans `config.py` (8h00 par défaut).
- **Délai de l'alerte "avant news"** : `ALERT_BEFORE_MINUTES` (30 min par défaut).
- **Mots-clés de la veille breaking news** : variable `BREAKING_NEWS_KEYWORDS` dans `.env`, séparés par des virgules.
- **Fréquence de la veille breaking news** : `BREAKING_NEWS_INTERVAL_MINUTES` dans `config.py` (15 min par défaut — ne descends pas trop bas, le quota NewsAPI gratuit est de 100 requêtes/jour).

Après modification, redéploie sur Render (un `git push` suffit, Render redéploie automatiquement) ou relance en local.

---

## Limites honnêtes à connaître

Pour que tu saches exactement ce que fait (et ne fait pas) l'agent :

- **Breaking news = best-effort, pas du vrai temps réel.** Il n'existe aucune API gratuite fiable pour capter un tweet ou une déclaration à la seconde près (l'API X/Twitter coûte ~100$/mois). L'agent combine GDELT et NewsAPI, avec un délai typique de 5 à 15 minutes, et un tri par IA pour limiter les fausses alertes — mais des faux négatifs (rien détecté) et faux positifs (alerte peu pertinente) restent possibles.
- **Le "résultat réel" après publication ne fonctionne pas actuellement (confirmé).** Le calendrier ForexFactory (source principale, gratuite et fiable pour les horaires/prévisions) ne publie jamais le résultat réel après coup, et FMP a retiré cette donnée de son plan gratuit courant 2025 — testé en conditions réelles, l'accès est refusé même avec une clé FMP valide. Les alertes "après publication" afficheront donc "indisponible" tant que tu n'as pas un compte FMP payant (ou une autre source que tu voudrais brancher toi-même).
- **Le calendrier ForexFactory se décale le week-end.** Le flux gratuit utilisé ne couvre que la semaine calendaire en cours ; le nouveau contenu de la semaine suivante apparaît généralement dimanche soir/lundi matin.
- **Crypto (BTC/ETH), indices (US30/SP500/NASDAQ/DAX/CAC40) et pétrole (BRENT) n'ont PAS de calendrier économique dédié.** Il n'existe pas de source gratuite équivalente à ForexFactory pour ces instruments (ex : pas d'heure précise pour "prochaine décision SEC sur un ETF"). Ils reçoivent : (1) le biais IA généré automatiquement à chaque news USD ou EUR existante (le crypto et les indices US sont très corrélés au dollar), et (2) une couverture best-effort via la veille breaking news (mots-clés SEC/CFTC/OPEP/exchange hack...). En clair : pas d'alerte "30 min avant" programmée pour ces instruments, seulement des alertes réactives.
- **L'IA peut se tromper.** Le biais directionnel et le niveau de danger sont des indications générées automatiquement, pas des conseils financiers personnalisés — la décision de trader reste la tienne.

---

## Combien ça coûte, vraiment ?

**0 € — tout est sur un palier gratuit permanent, aucune carte bancaire nulle part.**

- **Render + cron-job.org + ForexFactory + GDELT** : 0 €, sans limite de temps.
- **FMP + NewsAPI** : 0 € (plans gratuits). Pour FMP, le plan gratuit ne couvre plus le calendrier économique (voir "Limites honnêtes" plus haut) — la clé reste gratuite à créer, elle est juste actuellement sans effet pour cette fonctionnalité précise.
- **Google Gemini** : 0 € (1500 requêtes/jour offertes en continu ; cet agent en utilise typiquement quelques dizaines par jour avec les réglages par défaut — résumé quotidien, alertes calendrier ~5-15/semaine, veille breaking news toutes les 15 min mais IA appelée seulement si un nouvel article correspond aux mots-clés).

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
├── requirements.txt
├── .env.example
├── runtime.txt             # Version Python pour Render
└── render.yaml             # Config de déploiement Render (optionnel)
```
