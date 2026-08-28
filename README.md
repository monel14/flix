# NokaTV — Films, Séries, K-Dramas & Animés

Interface web moderne pour parcourir et regarder des films, séries, K-Dramas et animés japonais en streaming gratuit HD.
L'application agrège à la volée le contenu de 3 sources majeures (`coflix.wiki`, `voirdrama.to`, `voir-anime.to`), le présente dans une interface cinématique soignée et sans publicité, et lit les vidéos via des lecteurs intégrés.

---

## 🌟 Fonctionnalités

- **Accueil Cinématique Commercial** : Hero Showcase en pleine largeur, Reprendre la lecture (*Continue Watching*), Top 10 tendances numéroté, et sélections par univers.
- **Section Animés** (`/animes`) : Catalogue d'animés japonais et donghua chinois avec 19 genres (*Action, Isekai, Shonen, etc.*) et tri Nouveaux Épisodes / A-Z.
- **Section K-Dramas** (`/dramas`) : Catalogue de dramas coréens et asiatiques avec 35 filtres de genres.
- **Ma Liste de Favoris** (`/ma-liste`) : Mémorisation locale sans inscription pour sauvegarder vos contenus préférés en 1 clic.
- **Filtres de Versions Linguistiques** : Bascule instantanée entre *Toutes versions*, *VOSTFR* et *VF* avec micro-badges visuels.
- **Lecteur Vidéo Multi-Serveurs** : Sélection de lecteurs (*Voembed, Vidmoly, VOE, Streamtape, Mail.ru, Kokoflix...*), mode cinéma et playlist d'épisodes pour le binge-watching.
- **Recherche Instantanée Tri-Sources** : Recherche simultanée sur les 3 catalogues avec autocomplétion.
- **Cache SQLite & Stale-on-Error** : Continuité de service même en cas de panne temporaire des sources distantes.
- **Proxy d'Images Intégré** : Contournement transparent des protections 403 anti-hotlink pour des affiches toujours visibles.
- **SEO & Partage Social** : Balises OpenGraph dynamiques (aperçu riche sur WhatsApp, Telegram, Discord, X), données structurées Schema.org (`WebSite`+`SearchAction`, `BreadcrumbList` sur les fiches, `ItemList` sur les catalogues, `Movie`/`TVSeries` quand la source est fiable) et génération automatique de `sitemap.xml` (fiches incluses) / `robots.txt`.
- **Pages de confiance (E-E-A-T)** : `/mentions-legales` et `/contact` — transparence sur la nature du service d'agrégation et procédure de retrait pour les ayants droit.
- **Performance & Core Web Vitals** : zéro CDN tiers sur le chemin critique — police *Plus Jakarta Sans* auto-hébergée (woff2 latin, `font-display: swap`) et icônes *Font Awesome* en SVG local via *mask CSS* (plus de webfont d'icônes) ; gzip automatique et cache navigateur 1 an sur les assets versionnés.
- **Installation PWA contextuelle** : invitation retardée et non intrusive fondée sur les capacités réelles du navigateur ; prompt natif quand `beforeinstallprompt` existe, consignes « Ajouter à l’écran d’accueil » sur Safari iOS/iPadOS, et silence sûr sur TV ou lorsqu’aucune installation fiable n’est disponible.
- **Publications Telegram autonomes** : worker quotidien à 12:00 WAT, baseline silencieuse, déduplication SQLite durable, retries et liens de fiches NokaTV uniquement pour Films, Séries, Animés et Films d’animation.

---

## 🛠️ Stack Technique

| Composant | Choix |
|---|---|
| Langage | Python 3.10+ |
| Framework web | FastAPI 0.115 |
| Serveur ASGI | Uvicorn 0.30 |
| Client HTTP async | httpx 0.28 (Multi-sources asynchrones) |
| Parser HTML | BeautifulSoup4 4.12 |
| Templates | Jinja2 3.1 |
| Cache & Archive | SQLite natif (mode WAL) avec Stale-on-Error |
| Frontend | Vanilla CSS/JS — police woff2 & icônes SVG auto-hébergées (Font Awesome 6.5.2, mask CSS — zéro CDN) |
| Conteneurisation | Docker & Docker Compose |

---

## 📲 Installation PWA

Le manifeste (`static/manifest.webmanifest`), ses icônes et le service worker
racine (`/sw.js`) sont déjà intégrés au shell de l’application. L’invitation
d’installation est séparée en trois fichiers :

- `static/pwa-install-manager.js` : détection des capacités, cooldown et
  événements navigateur ;
- `static/pwa-install-prompt.js` : rendu du dialogue et accessibilité ;
- `static/pwa-install.css` : styles responsive, safe areas et mouvement réduit.

Le manager ne déduit jamais une installation depuis `localStorage` ni le type
d’appareil. Il utilise les display modes PWA et `navigator.standalone` lorsqu’il
est exposé par Safari iOS/iPadOS. Sur les navigateurs Chromium/Edge qui émettent
`beforeinstallprompt`, le bouton appelle le prompt natif une seule fois. Sans
mécanisme fiable — notamment sur une TV détectée par le mode TV existant — aucun
bouton n’est affiché.

Un très petit bootstrap inline, chargé dans le `<head>` avant les scripts
`defer`, retient et neutralise un éventuel `beforeinstallprompt` précoce. Le
manager le consomme ensuite, donne toujours priorité à `appinstalled` et libère
la référence avant `prompt()`. Après un `appinstalled` réel, la fenêtre se ferme
immédiatement ; un `BroadcastChannel` optionnel relaie ce seul signal réel aux
autres onglets ouverts. Le cooldown reste limité à un refus et est aussi relayé
par l’événement `storage` : il ne devient jamais un indicateur d’installation.

### Prérequis de production

L’installation d’une PWA nécessite une origine **HTTPS** valide (hors
`localhost` en développement), un manifeste valide et un service worker
contrôlant le même scope. `SITE_URL=https://…` configure les URLs SEO, mais ne
met pas en place TLS à lui seul : le domaine et le reverse proxy / hébergeur
doivent réellement servir l’application en HTTPS.

### Réglage et diagnostic

Les valeurs par défaut sont un délai de `3000` ms et un cooldown de `3` jours
après « Plus tard » ou le refus du prompt natif. Pour les surcharger, placez cet
objet **avant** `pwa-install-manager.js` dans `templates/base.html` :

```html
<script>
  window.NokaTVPWAInstallConfig = {
    delay: 3000,
    cooldownDays: 3,
    debug: false
  };
</script>
```

L’API de diagnostic `window.NokaTVPWAInstall` expose `canInstall`,
`isInstalled`, `shouldShowPrompt`, `platform`, `installationInstructions`,
`install()` et `dismiss()`. Ajoutez `?pwa-install-debug=1` à une URL de
développement pour activer les messages `[PWA Install]` dans la console ; ils
sont désactivés par défaut en production.

### Vérification de régression et appareils réels

Le contrôle logique sans navigateur graphique s’exécute avec :

```bash
.venv/bin/python -m pytest -q
node scripts/test_pwa_install_manager.js
node scripts/test_pwa_install_prompt.js
```

Ces tests simulent notamment un événement précoce, `appinstalled` avant ou
après `userChoice`, le double clic, l’absence définitive de
`beforeinstallprompt`, le refus/cooldown inter-onglets, iOS/iPadOS, desktop,
TV, le focus clavier et la fermeture accessible. L’API d’installation restant
fournie par le navigateur, compléter chaque
mise en production HTTPS par ce court contrôle sur appareils réels :

1. **Chrome Android et desktop Chromium/Edge éligible** : vérifier que le CTA
   n’apparaît qu’après `beforeinstallprompt`, qu’un double clic n’ouvre qu’un
   dialogue natif, puis que la modal se ferme après installation ou refus.
2. **Safari iPhone/iPad (portrait et paysage)** : vérifier les seules consignes
   Partager → Ajouter à l’écran d’accueil, les marges de zone sûre et l’absence
   de CTA après lancement depuis l’icône installée.
3. **Tablette et Smart TV** : vérifier le défilement de la modal sur tablette,
   puis l’absence complète de l’invitation et de prise de focus en mode TV.
4. **Clavier desktop** : contrôler focus initial, Tab/Shift+Tab, Échap, le
   bouton de fermeture et le retour du focus à l’élément déclencheur.

---

## 🤖 Publication automatique sur Telegram

Le worker `scripts/publish_telegram.py` publie des **affiches, légendes et
boutons vers les fiches NokaTV** ; il ne publie jamais un iframe, un flux vidéo
ni une URL de lecteur tiers. Utilisez-le uniquement pour les contenus que vous
êtes autorisé à promouvoir.

La configuration retenue est un passage quotidien à **12:00 WAT**
(`Africa/Lagos`, UTC+1) pour quatre canaux :

| Canal | Variable | Source surveillée |
|---|---|---|
| Films | `TELEGRAM_CHANNEL_FILMS` | liste récente `/movies/`, hors films d’animation |
| Séries | `TELEGRAM_CHANNEL_SERIES` | épisodes des titres de la liste récente `/series/` |
| Animés / « manga » | `TELEGRAM_CHANNEL_ANIMES` ou `TELEGRAM_CHANNEL_MANGA` | épisodes des titres de la liste récente Voiranime `/` |
| Films d’animation | `TELEGRAM_CHANNEL_ANIMATION` | liste récente `/movies/animation/` |

Les quatre canaux sont requis lorsque `TELEGRAM_PUBLISH_ENABLED=true`. Les
K-Dramas sont volontairement exclues.

Par défaut, `TELEGRAM_DISCOVERY_MODE=hybrid` est conçu pour un cron partagé :

- il lit d'abord passivement les fiches et listes déjà présentes dans `cache.db` ;
- il interroge ensuite **une seule liste récente** par source : `/movies/`,
  `/movies/animation/`, `/series/` et la page d'accueil Voiranime `/` (qui est
  aussi la liste « Nouveaux épisodes » utilisée par l'application) ;
- pour les séries et animés, une fiche cache encore fraîche évite une requête de
  détail. Si elle est expirée et que la source échoue, son dernier état sert de
  secours, sans masquer l'erreur ni valider une baseline partielle.

Au premier passage, l'union du cache local et de ces listes constitue une
**baseline** SQLite silencieuse : aucun historique n'est envoyé. Aux passages
suivants, seuls les candidats des listes récentes peuvent devenir de nouveaux
posts ; une vieille fiche ouverte par un visiteur ne sera donc jamais publiée.
Tous les éléments détectés et valides sont traités, sans taille maximale de lot :
un post par film ou film d'animation, et un post par épisode de série ou animé.

Ce mode accepte explicitement le compromis suivant : un contenu qui n'a jamais
été en cache et qui a déjà disparu des listes récentes de sa source peut être
manqué. Pour un rattrapage exceptionnel, `TELEGRAM_DISCOVERY_MODE=complete`
réactive le parcours paginé exhaustif des catalogues ; il est sensiblement plus
coûteux et ne doit pas être le réglage quotidien PlanetHoster. Une photo que
Telegram ne peut pas récupérer bascule automatiquement vers un message texte
avec le même bouton. Les sources et l'API Telegram sont retentées ; une panne
de collecte est aussi mémorisée dans SQLite puis rejouée par
`--flush-retries` après un backoff persistant. Les retries Telegram respectent
également `retry_after`.

1. Copiez les variables Telegram de `.env.example` dans votre `.env` local.
   Le bot doit être administrateur avec l'autorisation de publier dans chacun
   des quatre canaux. Définissez aussi `SITE_URL` sur le domaine HTTPS canonique
   public de NokaTV (jamais `localhost`), car il est utilisé par les boutons.
   Ne commitez et ne partagez jamais `TELEGRAM_BOT_TOKEN`.
2. Vérifiez la collecte sans écrire ni envoyer :

   ```bash
   .venv/bin/python scripts/publish_telegram.py --dry-run
   ```

3. Une fois tous les canaux configurés et
   `TELEGRAM_PUBLISH_ENABLED=true`, lancez une première fois le worker. Cette
   exécution mémorise le catalogue courant sans le reposter :

   ```bash
   .venv/bin/python scripts/publish_telegram.py --once
   ```

4. **PlanetHoster / N0C (déploiement retenu, sans Docker)** : conservez
   `cache.db` à côté de l'application, car il contient la baseline, les
   déduplications et les retries. Créez d'abord le dossier de log une fois :

   ```bash
   mkdir -p /home/wtzjscfs/nokatv/logs
   ```

   Avant de choisir l'horaire dans l'interface N0C, exécutez via SSH :

   ```bash
   date '+%F %T %Z %z'
   ```

   N0C planifie généralement selon l'heure de son serveur. Convertissez donc
   12:00 `Africa/Lagos` (Cotonou / WAT, UTC+1) à partir de ce résultat ; ne
   supposez pas que le serveur est déjà en WAT. Une fois les champs minute et
   heure correspondants connus, créez ces **deux** tâches (remplacez `MM HH`
   par la conversion obtenue) :

   ```cron
   # Tous les jours à 12:00 WAT — découverte + publications
   MM HH * * * cd /home/wtzjscfs/nokatv && /home/wtzjscfs/virtualenv/nokatv/3.10/bin/python scripts/publish_telegram.py --once >> /home/wtzjscfs/nokatv/logs/telegram-publisher.log 2>&1

   # Toutes les 5 minutes — retries Telegram ; une collecte n'est rejouée
   # que si un échec source persistant est arrivé à échéance
   */5 * * * * cd /home/wtzjscfs/nokatv && /home/wtzjscfs/virtualenv/nokatv/3.10/bin/python scripts/publish_telegram.py --flush-retries --json >> /home/wtzjscfs/nokatv/logs/telegram-publisher.log 2>&1
   ```

   N'utilisez pas `--schedule` dans N0C : c'est un daemon prévu pour un
   processus persistant, pas pour une tâche Cron. N'ajoutez pas non plus le
   service Docker `telegram-publisher` sur ce même hébergement.

5. **Docker Compose (optionnel, hors N0C)** : `docker compose up -d` lance le
   service `telegram-publisher`, qui attend chaque jour
   `TELEGRAM_PUBLISH_HOUR=12` dans `TELEGRAM_TIMEZONE=Africa/Lagos`. Ne le
   combinez jamais avec les crons ci-dessus.


Le worker réserve un message à la fois dans une transaction SQLite et
renouvelle son lease pendant les délais Telegram. Ainsi, un redémarrage, deux
processus web ou un déclenchement concurrent normal ne publient pas deux fois.
Comme toute API externe sans clé d’idempotence, un crash exactement après la
confirmation de Telegram et avant l’écriture locale peut exceptionnellement
nécessiter une vérification manuelle.

---

## 🚀 Installation & Démarrage

### Option 1 : Avec Docker (Recommandé en 1 clic)

```bash
# 1. Cloner le dépôt
git clone <url-du-repo>
cd nokatv

# 2. Lancer le conteneur en tâche de fond
docker compose up -d
```
L'application est immédiatement disponible sur **`http://localhost:8001`**.

---

### Option 2 : Installation Classique (Python venv)

```bash
# 1. Cloner le dépôt
git clone <url-du-repo>
cd nokatv

# 2. Créer l'environnement virtuel
python -m venv .venv
source .venv/bin/activate  # Sur Windows : .venv\Scripts\activate

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Configurer les variables d'environnement
cp .env.example .env

# 5. Démarrer le serveur
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

---

## ⚙️ Configuration (`.env`)

```env
# Domaine public (OBLIGATOIRE en prod) : ancre canonicals, Open Graph & sitemap
SITE_URL=https://nokatv.xyz
# Email de contact affiché sur /contact et /mentions-legales (retraits, liens morts)
CONTACT_EMAIL=contact@nokatv.xyz
BASE_URL=http://localhost:8001
COFLIX_SOURCE_URL=https://coflix.wiki
VOIRDRAMA_SOURCE_URL=https://voirdrama.to
VOIRANIME_SOURCE_URL=https://voir-anime.to
# Optionnel : profondeur de collecte du sitemap par catégorie (défaut 5)
# SITEMAP_MAX_PAGES=5
```
