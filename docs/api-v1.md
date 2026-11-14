# NokaTV — API v1, première tranche animés

## État

Implémentation additive basée sur le commit `35421ef`. Les routes HTML restent présentes. Aucun changement de domaine, frontend, base de données ou scraper existant. Cette tranche ne comprend pas encore les films/séries/dramas, la recherche multisource, l'authentification utilisateur, les favoris synchronisés ou la fourniture des lecteurs vidéo.

## Configuration serveur

```dotenv
NOKATV_API_KEY=<clé aléatoire privée générée pour le serveur PHP>
NOKATV_API_PUBLIC_URL=https://nokatv.xyz
VOIRANIME_SOURCE_URL=https://voiranime.cx
```

- Générer la clé avec `python -c "import secrets; print(secrets.token_urlsafe(32))"` dans un environnement privé. Ne pas la committer.
- Aucune clé configurée : l'API répond 503 et ne contacte aucune source.
- Clé envoyée dans `X-API-Key`, jamais dans l'URL, le JavaScript public ou les liens d'images.
- `NOKATV_API_PUBLIC_URL` est l'origine HTTPS publique de l'application FastAPI, pas celle du frontend s'il est déplacé. Les images sont rendues absolues sur cette origine. Sans configuration, l'origine de la requête est utilisée ; en production, configurer explicitement cette variable et des hôtes autorisés au niveau hébergement.
- Conserver les paramètres `.env` existants et éviter les définitions contradictoires. Redémarrer Passenger après installation/configuration.

## Routes

Toutes les routes ci-dessous exigent la clé serveur :

| GET | Paramètres |
|---|---|
| `/api/v1/genres` | `type=anime` uniquement |
| `/api/v1/animes` | `page=1..1000`, `genre` parmi les slugs exposés, `sort=latest\|az` |
| `/api/v1/search` | `q` de 2 à 120 caractères après suppression des espaces de bord, `type=anime`, `page=1..100` |
| `/api/v1/titles/anime/{slug}` | Slug alphanumérique, tirets ou underscores, 200 caractères maximum |

Recherche exacte de la source, sans correction orthographique nouvelle dans cette tranche. Le tri `az` reprend le catalogue alphabétique de la source. Les listes n'ajoutent pas de filtre VF/VOSTFR côté API dans cette version.

```bash
curl --fail-with-body \
  -H "X-API-Key: $NOKATV_API_KEY" \
  'https://nokatv.xyz/api/v1/animes?genre=action&page=1'
```

```json
{
  "items": [{
    "id": "voiranime:exemple",
    "type": "anime",
    "slug": "exemple",
    "title": "Exemple",
    "image_url": "https://nokatv.xyz/api/image-proxy?url=...",
    "version": "VOSTFR",
    "latest_episode": "03"
  }],
  "pagination": {"page": 1, "has_next": true, "total_pages": null}
}
```

`total_pages` reste volontairement `null` : les liens de certaines pages sources n'indiquent que la page suivante. `has_next` est déduit de la navigation observée, pas d'un total inventé. Le nombre de titres peut être réduit par la blocklist.

La fiche expose aussi `synopsis`, `genres`, `year`, `status`, `first_episode_id` et `episodes` (`id`, `title`, `number`, `version`). Pas de liens de lecteurs dans cette tranche. Les métadonnées et numéros d'épisodes proviennent du parser existant ; pas de nouvel audit exhaustif de ce parser.

## Cache, erreurs et sécurité

- Cache source SQLite existant, espace de clés `api:v1:voiranime:{source}:...`, TTL listes/recherche et fiches déjà définis dans le projet. Les données sont mises en cache avant conversion des liens en URL publiques.
- Pas de fallback silencieux sur un cache expiré en cas d'erreur. Un résultat vide valide renvoie 200 ; une erreur connue de récupération ou une structure manifestement inattendue renvoie 502.
- Les contrôles structurels sont heuristiques : une modification de thème peut nécessiter une adaptation. Ils ne détectent pas toute dégradation possible de la source.
- 401 : clé absente/invalide ; 404 : fiche absente ou bloquée ; 422 : paramètres invalides ; 429 : limite atteinte ; 502 : source indisponible ; 503 : clé serveur non configurée.
- Erreurs métier sous `detail.code`. La validation native FastAPI utilise son format standard `detail` (liste). Les détails d'erreurs réseau ne sont pas renvoyés au client.
- Réponses API marquées `Cache-Control: private, no-store` et `X-Robots-Tag: noindex, nofollow` dans l'application principale, y compris les erreurs.
- Limite simple : **120 requêtes/minute par clé et par processus**. Ce n'est pas un quota global multiprocessus ni une protection DDoS complète. Prévoir une protection à l'entrée si exposition plus large. Une seule clé de service dans cette première version ; rotation via configuration et redémarrage.
- Les titres bloqués sont filtrés des listes/recherches et refusés en fiche, même si le cache contient encore leur HTML.
- Le proxy d'images préexistant reste public et conserve son fonctionnement. La clé API ne sécurise pas rétroactivement les anciennes routes du site. Son durcissement général (hôtes autorisés, limites de taille, etc.) n'est pas inclus dans ce patch.
- Pas de CORS ajouté : le frontend PHP contacte FastAPI côté serveur. CORS ne remplace pas une authentification.
- Une panne/suspension de PlanetHoster reste une dépendance pour le frontend. Traiter le motif du signalement demeure nécessaire.

## Installation et recette

1. Sauvegarder et vérifier les différences serveur/dépôt avant tout déploiement.
2. Installer `routes/api_v1.py` et les ajouts de `main.py` ensemble. Les dépendances du projet suffisent.
3. Configurer la clé et l'origine publique sans modifier les autres paramètres, puis redémarrer l'application.
4. Vérifier sans clé (401), avec clé (200), une fiche, un genre et la pagination.
5. Vérifier le type JPEG des affiches via les URL renvoyées et les anciennes pages HTML.
6. Les tests locaux s'exécutent avec `python -m pytest -q test_api_v1.py` ; aucun accès réseau ni secret de production requis.

Tests supplémentaires réels effectués localement contre Voiranime : nouveautés (16 titres), page 2 (16 titres différents), Action (10 titres), recherche Naruto (10 titres), fiche Firefly Wedding VF (3 épisodes). Cela valide l'API locale avec la source réelle, pas un déploiement sur PlanetHoster ni la lecture vidéo.
