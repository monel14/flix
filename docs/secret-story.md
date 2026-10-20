# Mise en avant Secret Story Afrique

Base : origin/master 394b868. Le patch ne déploie rien automatiquement.

- Page `/secret-story-afrique` sur le domaine catalogue : lecteur YouTube officiel chargé au clic, sans autoplay et sans extraction de flux.
- Promotion en première slide et rail de quatre cartes sur l’accueil ; les contenus existants restent disponibles. Aucun badge de présence en direct non vérifiée.
- Visuel typographique original SVG, sans photographie ou logo repris.
- Page de lecture en `noindex, follow`, hors sitemap ; le domaine observatoire continue de répondre 404 sur ce chemin.
- Horaires quotidiens et prime sourcés : https://secretstoryafrique.com/emission, consulté le 20 octobre 2026. Les conversions GMT/UTC+1 sont statiques, pas un calendrier d’événements en direct.
- Candidats, votes et rediffusions : informations et accès officiels ; pas de replays inventés ou hébergés.

## Configuration (variables d’environnement, redémarrer l’application après changement)

```text
SECRET_STORY_ENABLED=true
SECRET_STORY_YOUTUBE_ID=_ZqZmDeWmDI
```

L’identifiant vient de la capture réseau fournie par l’exploitant. Il n’a pas été validé comme direct actuellement disponible. Vérifier la lecture et l’autorisation d’intégration en production depuis une session ordinaire avant de promouvoir la page. Les restrictions territoriales, de confidentialité ou d’intégration YouTube restent applicables. Aucun cookie, jeton de lecture ou URL googlevideo n’est enregistré.

Pour masquer immédiatement la promotion et désactiver la page : `SECRET_STORY_ENABLED=false`, puis redémarrer. Pour un nouvel événement, remplacer uniquement l’identifiant par celui autorisé (11 caractères). Les textes et horaires doivent être actualisés séparément.

## Vérifications

`python -m unittest test_secret_story test_alternatives_linking -v`

Tests : route, protection du domaine observatoire, noindex, absence du sitemap, absence d’iframe au chargement, une seule H1, conservation des slides, configuration et désactivation.

Un contrôle Chromium desktop (1440 px) et mobile (390 px) a également vérifié le rendu sans débordement horizontal et la création de l’iframe au clic. Le domaine YouTube a été simulé dans ce contrôle : ce n’est pas un test du contenu du direct.

Après déploiement, vérifier accueil, page dédiée, lecture YouTube et console navigateur, notamment une éventuelle CSP ajoutée par l’hébergement. Ne pas masquer les éléments d’identification ou les restrictions du fournisseur.
