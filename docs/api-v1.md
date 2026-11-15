# NokaTV — API v1, catalogue et données de lecture

## État

API initiale intégrée dans `81e283d`, puis complétée par le présent patch. Les routes HTML restent présentes. Aucun changement de domaine, frontend, base de données ou scraper existant. Les comptes utilisateurs et favoris synchronisés ne font pas partie de cette migration. Les références des lecteurs sont exposées ; la lecture effective des médias n’est pas garantie.

## Configuration serveur

```dotenv
NOKATV_API_KEY=<clé aléatoire privée générée pour le serveur PHP>
NOKATV_API_PUBLIC_URL=https://noka-tv.xyz
VOIRANIME_SOURCE_URL=https://voiranime.cx
```

- Générer la clé avec `python -c "import secrets; print(secrets.token_urlsafe(32))"` dans un environnement privé. Ne pas la committer.
- Aucune clé configurée : l'API répond 503 et ne contacte aucune source.
- Clé envoyée dans `X-API-Key`, jamais dans l'URL, le JavaScript public ou les liens d'images.
- `NOKATV_API_PUBLIC_URL` est l'origine HTTPS publique de l'application FastAPI, pas celle du frontend s'il est déplacé. Les images sont rendues absolues sur cette origine. Sans configuration, l'origine de la requête est utilisée ; en production, configurer explicitement cette variable et des hôtes autorisés au niveau hébergement.
- Conserver les paramètres `.env` existants et éviter les définitions contradictoires. Redémarrer Passenger après installation/configuration.

## Routes animés conservées

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
  'https://noka-tv.xyz/api/v1/animes?genre=action&page=1'
```

```json
{
  "items": [{
    "id": "voiranime:exemple",
    "type": "anime",
    "slug": "exemple",
    "title": "Exemple",
    "image_url": "https://noka-tv.xyz/api/image-proxy?url=...",
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


## Extension catalogue et lecture (base du patch : `81e283d`)

Toutes ces routes ont la même protection `X-API-Key` et les mêmes en-têtes privés que les routes animés.

| GET | Paramètres / réponse |
|---|---|
| `/api/v1/home` | `top=day|week|month` ; sections `hero`, `movies`, `series`, `animation_movies`, `dramas`, `animes`, `top` |
| `/api/v1/movies` | `page=1..1000`, `genre`, `version=all|vf|vostfr` |
| `/api/v1/series` | Identique aux films |
| `/api/v1/dramas` | `page`, `genre`, `sort=latest|az`, `version`, `source=all|voirdrama|frenchstream` |
| `/api/v1/genres?type=...` | `movie`, `series`, `anime`, `drama` |
| `/api/v1/search` | `q`, `page=1..100`, `type=all|movie|series|anime|drama` ; défaut **anime conservé** pour compatibilité |
| `/api/v1/titles/{type}/{slug}` | Types `movie`, `series`, `drama`, `anime` ; fiche normalisée |
| `/api/v1/titles/{type}/{slug}/episodes` | Épisodes normalisés et `first_episode_id` |
| `/api/v1/playback/{type}/{slug}/{episode_id}` | Lecteurs et identifiants précédent/suivant |

Pour les fiches, épisodes et lecteurs dramas, `source=auto|voirdrama|frenchstream` sélectionne la source. Utiliser explicitement `source=frenchstream` lorsqu'une entrée provient de cette source. Pour les autres types, les nouvelles routes communes n'acceptent que `source=auto`.

### Contrats et compatibilité

- `Item.type` accepte désormais les quatre types. Le champ `source` indique `coflix`, `voirdrama`, `voiranime` ou `frenchstream`. `id` est construit avec la source et le slug.
- Les champs de l'ancienne API animés restent présents. `warnings` (tableau) et `source` sont additifs. Les épisodes normalisés peuvent inclure `season` (chaîne ou null).
- Le filtre langue s'applique aux éléments **de la page source**, sans reconstituer une pagination globale par langue. Une page filtrée peut être vide tout en ayant une page suivante.
- Pagination films/séries : total extrait de la source ; 404 au-delà du total observé. WordPress/dramas/recherche : total inconnu (`null`), prochaine page déduite de la navigation. La recherche multisource entrelace les pages des sources ; ce n'est pas un classement global ni une pagination d'index unifié.
- Les résultats Coflix de recherche mélangent films et saisons. L'adaptateur classe les saisons via la présence de `saison/season + numéro` dans le titre/slug ; un titre atypique peut être mal classé. Le type réel de fiche est vérifié ensuite (404 `type_mismatch` si discordance).
- La recherche animés initiale reste identique. La recherche `type=all` interroge Coflix, Voirdrama et Voiranime, plus le catalogue FrenchStream déjà chaud. Aucune correction orthographique ajoutée.

### Dramas et FrenchStream

Le catalogue est fusionné selon le principe existant, avec dédoublonnage par slug des entrées ajoutées au mélange. Un résultat FrenchStream conserve sa source afin de résoudre correctement sa fiche.

L'API ne lance pas le téléchargement lourd des 39 pages et affiches du catalogue FrenchStream à l'intérieur d'une requête. Elle réutilise `frenchstream:catalog`, préchauffé par le fonctionnement existant du backend. Si ce cache est absent/expiré, l'API expose `catalog_warming` : les autres sources restent utilisables. La source FrenchStream seule renvoie alors 503. Il faut maintenir le préchauffage existant ou un traitement planifié côté backend ; l'API ne garantit pas un catalogue chaud après chaque purge.

Les genres s'appliquent à Voirdrama uniquement ; `source=frenchstream&genre=...` est refusé (422). FrenchStream est présenté en VOSTFR dans le catalogue fusionné, comme sur le site existant ; sa sélection exclusive avec `version=vf` est refusée. Les métadonnées d'épisodes restent dépendantes du parser existant. Les épisodes FrenchStream sont remis en ordre numérique dans l'API pour une navigation cohérente.

La fiche drama `source=auto` essaie Voirdrama, puis FrenchStream si Voirdrama signale une fiche absente. Une panne de Voirdrama n'est pas silencieusement traitée comme une absence.

### Accueil et pannes partielles

Chaque section d'accueil indique `status=ok|unavailable` et contient ses `items`. Les erreurs partielles sont dans `warnings`. Un accueil sans aucune section disponible renvoie 502.

Listes fusionnées et recherche multisource renvoient 200 si au moins une source a répondu, même si son résultat est vide. Les pannes des autres sources sont annoncées dans `warnings` avec seulement la source et un code public. Toutes les sources en échec : 502. Le délai des adaptateurs ajoutés est borné à 20 secondes par chargement, hors accès SQLite.

Les espaces de cache des adaptateurs sont distincts des anciennes routes. Pas de fallback silencieux sur un cache expiré. L'API initiale animés garde son espace existant. Ne pas placer les réponses authentifiées dans un cache public.

### Lecture

Exemple de réponse :

```json
{
  "type": "anime",
  "slug": "exemple",
  "source": "voiranime",
  "episode_id": "exemple-01-vostfr",
  "servers": [{"name": "Serveur 1", "url": "https://lecteur.example/embed/123", "type": "embed", "version": "VOSTFR"}],
  "previous_episode_id": null,
  "next_episode_id": "exemple-02-vostfr"
}
```

- La fiche est vérifiée et l'épisode doit lui appartenir **avant** toute récupération de lecteurs. Pas d'endpoint v1 donnant accès à un identifiant arbitraire sans vérifier son titre.
- La blocklist est appliquée aux fiches, listes, recherche, accueil, épisodes et lectures, même avec un cache déjà rempli.
- Les liens de lecteurs sont dédoublonnés et limités aux schémas HTTP/HTTPS. Aucun flux média n'est téléchargé, déchiffré ou relayé par ces routes.
- Un lecteur absent renvoie 404 `no_servers`. Épisode hors fiche : 404 `episode_not_found`. Les identifiants d'épisodes sont limités à 220 caractères alphanumériques, tirets et underscores.
- Pour un film, une entrée de lecture unique est dérivée de l'identifiant exposé par sa fiche. Pour les séries, la liste provient de l'API épisodes de la source ; une panne de récupération n'est pas remplacée par une fausse liste vide.
- Pour Voirdrama, un complément FrenchStream n'est tenté que si une correspondance par slug est déjà connue dans le catalogue chaud. Pas de scan complet de sitemap par requête. Ce complément n'est pas un équivalent exhaustif de l'ancien rapprochement approximatif de titres.
- Un iframe peut encore refuser un nouveau domaine, demander des cookies ou être indisponible. Récupérer ses références ne prouve pas que la vidéo se lit. Le frontend doit respecter les autorisations et les contraintes des lecteurs.

### Validation et déploiement de l'extension

77 tests automatisés passent dans la sélection API + régressions essentielles. `test_api_catalog.py` utilise des sources simulées ; des contrôles réels ont aussi été effectués localement et en production.

En production : listes et pages 2 différentes des quatre catégories, genres, fiches/épisodes, références de lecteurs, recherche multisource, source FrenchStream explicite, Voirdrama explicite, accueil et blocklist testés. Les anciennes pages `/animes`, `/films`, `/series` répondent toujours avec leurs cartes. Les réponses API sans clé restent 401, y compris après des requêtes authentifiées.

L'extension déployée concerne uniquement `routes/api_v1.py` et `services/api_catalog.py`, après comparaison au commit de référence. La clé et le `.env` n'ont pas changé ; le frontend PHP n'a pas été modifié. Le patch inclut aussi les tests et la présente documentation. Aucun push GitHub automatique effectué.

La lecture effective des médias, les tests de charge, les comptes utilisateurs et les favoris synchronisés ne sont pas inclus dans cette validation.
