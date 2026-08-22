# Nokaflix — Films, Séries, K-Dramas & Animés

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
- **SEO & Partage Social** : Balises OpenGraph dynamiques (aperçu riche sur WhatsApp, Telegram, Discord, X) et génération automatique de `sitemap.xml` / `robots.txt`.

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
| Frontend | Vanilla CSS/JS + Plus Jakarta Sans + Font Awesome 6 |
| Conteneurisation | Docker & Docker Compose |

---

## 🚀 Installation & Démarrage

### Option 1 : Avec Docker (Recommandé en 1 clic)

```bash
# 1. Cloner le dépôt
git clone <url-du-repo>
cd nokaflix

# 2. Lancer le conteneur en tâche de fond
docker compose up -d
```
L'application est immédiatement disponible sur **`http://localhost:8001`**.

---

### Option 2 : Installation Classique (Python venv)

```bash
# 1. Cloner le dépôt
git clone <url-du-repo>
cd nokaflix

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
BASE_URL=http://localhost:8001
COFLIX_SOURCE_URL=https://coflix.wiki
VOIRDRAMA_SOURCE_URL=https://voirdrama.to
VOIRANIME_SOURCE_URL=https://voir-anime.to
```
