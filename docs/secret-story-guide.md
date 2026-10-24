# Parcours Secret Story Afrique : guide → lecteur

Base du correctif : origin/master e868f58.

- Acquisition : https://noka-tv.xyz/secret-story-afrique (indexable, canonical propre, sitemap éditorial).
- Destination : https://nokatv.xyz/secret-story-afrique (GET/HEAD 200, noindex/follow dans le HTML et l'en-tête HTTP, hors sitemap catalogue).
- Les boutons du guide ciblent désormais la page de lecture, avec les mêmes UTM et l'ancre #lecteur. Plus de retour automatique au guide côté catalogue, ni de renvoi vers l'accueil général.
- Aucun rétablissement du hero ou du rail d'accueil précédemment annulés. Aucun changement des autres rubriques ou domaines.
- Lecteur youtube-nocookie chargé seulement après clic ; bouton de fermeture, liens de secours YouTube et plateforme officielle ; aucune extraction de flux ni de session BestLive.
- Source par défaut : vUtbR0rvdOI, vidéo « SSA SAISON 3 - 2026 », mise à jour le 24 octobre 2026. L'endpoint oEmbed public YouTube renvoie ces métadonnées et un iframe d'intégration lors du contrôle.
- Cela ne certifie PAS le caractère officiel, les droits, la lecture effective ou un direct permanent. La tentative de lecture des métadonnées du player via la page watch a répondu HTTP 429 ; la lecture média réelle reste non confirmée. Aucun contournement de restriction.
- Source configurable avec SECRET_STORY_YOUTUBE_ID (11 caractères), désactivable par SECRET_STORY_ENABLED=false. Une configuration absente/invalide ou désactivée ne charge aucun lecteur distant et conserve un lien officiel. Sans variable, la source par défaut est utilisée.
- Horaires : quotidienne lundi-vendredi 18 h GMT, prime samedi 20 h 30 GMT, source https://secretstoryafrique.com/vote consultée le 23 octobre 2026 ; informations statiques, pas de mise à jour en temps réel.
- L'indexabilité du guide ne garantit ni indexation ni classement.

Déploiement habituel requis. Pas de push, de modification FTP ou de changement Cloudflare effectué par ce correctif.
