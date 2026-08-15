# Coflix

Interface web locale pour parcourir et regarder des films et séries en streaming. L'application scrape le contenu de `coflix.wiki` à la volée, le présente dans une UI propre, et lit les vidéos via des iframes pointant sur des hébergeurs tiers.

## Fonctionnalités

- Page d'accueil avec les films/séries récents et le top tendances du jour (chargement concurrent)
- Listes paginées de films et de séries
- Recherche full-text avec autocomplétion (API JSON)
- Page de détail : affiche, synopsis, genres, année, statut, liste d'épisodes
- Player avec sélection de serveur, sécurisation sandbox et navigation épisode précédent/suivant
- Cache SQLite persistant (mode WAL) avec TTL par ressource et purge automatique des données expirées

## Stack technique

| Composant | Choix |
|---|---|
| Langage | Python 3.10+ |
| Framework web | FastAPI 0.115 |
| Serveur ASGI | Uvicorn 0.30 |
| Client HTTP async | httpx 0.28 |
| Parser HTML | BeautifulSoup4 4.12 |
| Templates | Jinja2 3.1 |
| Cache | SQLite natif (mode WAL) |
| Frontend | Vanilla CSS/JS + Font Awesome 6 |

## Structure du projet

```
coflix/
├── main.py              # Point d'entrée FastAPI : app, routers, lifespan
├── cache.py             # Cache SQLite avec TTL et purge automatique
├── .env.example         # Exemple de variables d'environnement
├── requirements.txt     # Dépendances Python
│
├── routes/
│   ├── home.py          # Routes / , /films, /series
│   ├── search.py        # Routes /recherche et /api/search
│   ├── detail.py        # Route /film/{slug}
│   └── player.py        # Routes /regarder/{slug}/ep-{id} et /api/servers/{id}
│
├── scraper/
│   ├── coflix_client.py # Client HTTP async singleton (retry/backoff)
│   └── coflix_parser.py # Parseurs BeautifulSoup (listes, détail, épisodes, serveurs)
│
├── templates/           # Templates Jinja2
└── static/
    └── style.css        # CSS principal
```

## Routes

| Méthode | Path | Description |
|---|---|---|
| GET | `/` | Accueil : films récents, séries récentes, top du jour |
| GET | `/films?page=N` | Liste paginée des films |
| GET | `/series?page=N` | Liste paginée des séries |
| GET | `/recherche?q=...` | Page HTML des résultats de recherche |
| GET | `/api/search?q=...` | Autocomplétion JSON (≥ 2 caractères, 10 résultats) |
| GET | `/film/{slug}` | Page de détail d'un film ou d'une série |
| GET | `/regarder/{slug}/ep-{episode_id}` | Player pour un épisode ou un film |
| GET | `/api/servers/{episode_id}` | Liste JSON des serveurs disponibles |

## Installation

### Prérequis

- Python 3.10+
- `pip`

### Étapes

```bash
# Cloner le dépôt
git clone <url-du-repo>
cd coflix

# Créer un environnement virtuel
python -m venv .venv
source .venv/bin/activate

# Installer les dépendances
pip install -r requirements.txt

# Configurer les variables d'environnement
cp .env.example .env
```

## Configuration

Créer ou éditer le fichier `.env` à la racine du projet :

```env
BASE_URL=http://localhost:8001
COFLIX_SOURCE_URL=https://coflix.wiki
```

| Variable | Description | Défaut |
|---|---|---|
| `BASE_URL` | URL de base de l'application | `http://localhost:8001` |
| `COFLIX_SOURCE_URL` | URL du site source Coflix (miroir) | `https://coflix.wiki` |

## Démarrage

```bash
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

L'application est ensuite accessible sur [http://localhost:8001](http://localhost:8001).

## Cache

Le cache SQLite (`cache.db`) est créé automatiquement au premier démarrage. Les TTL sont configurés par type de ressource :

| Type | TTL |
|---|---|
| Accueil et listes | 5 minutes |
| Fiches film/série | 30 minutes |
| Listes d'épisodes | 10 minutes |
| Liens de streaming | 5 minutes |
| Résultats de recherche | 10 minutes |

Pour vider le cache manuellement, supprimer le fichier `cache.db`. Les entrées expirées sont également purgées automatiquement au démarrage de l'application.

## Notes

- Le scraper cible le site source avec un mécanisme de retry (3 tentatives, backoff exponentiel) pour absorber les erreurs réseau et les rate limits (HTTP 429).
- Les vidéos sont lues depuis des hébergeurs tiers via `<iframe>` ; aucun contenu vidéo n'est stocké localement.
- Ce projet est destiné à un usage personnel et local.
