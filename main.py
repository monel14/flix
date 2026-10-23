from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from xml.sax.saxutils import escape

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from cache import cache
from services.indexnow import ensure_indexnow_key, indexnow_key
from services.alternatives_data import CATEGORY_GUIDES, get_all_sites, get_site, get_sites_by_category, get_related_sites
from services.fixtures import get_upcoming_matches, get_upcoming_motorsport_combat
from services.seo import breadcrumb_json_ld, faq_page_json_ld, page_seo, site_origin
from services.sitemap import LEGAL_PATHS as LEGAL_SITEMAP_PATHS
from services.sitemap import STATIC_PATHS as STATIC_SITEMAP_PATHS
from services.sitemap import collect_sitemap_paths
from services.sw import generate_service_worker
from services.templates import templates
SITE_NAME = "NokaTV"
from routes import anime, detail, drama, home, player, search
from scraper.coflix_client import close_coflix_client
from scraper.frenchstream_client import close_frenchstream_client
from scraper.voirdrama_client import close_voirdrama_client, get_voirdrama_client
from scraper.voiranime_client import close_voiranime_client, get_voiranime_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("nokatv")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Nettoyage automatique du cache expiré au démarrage (conserve les archives)
    try:
        purged = cache.purge_expired()
        if purged > 0:
            logger.info("Cache nettoyé au démarrage : %d entrée(s) temporaire(s) expirée(s) supprimée(s)", purged)
    except Exception as exc:
        logger.warning("Impossible de purger le cache : %s", exc)
    # Clé IndexNow stable entre redémarrages (servie à /{clé}.txt)
    try:
        ensure_indexnow_key()
    except Exception as exc:
        logger.warning("IndexNow indisponible au démarrage : %s", exc)
    # Pré-chauffage du catalogue FrenchStream en arrière-plan (non bloquant) :
    # la 1ʳᵉ construction prend ~70 s (39 pages + posters), on ne veut pas la
    # déclencher sur la 1ʳᵉ requête /dramas d'un visiteur.
    try:
        from routes.drama import _frenchstream_catalog
        asyncio.create_task(_frenchstream_catalog())
    except Exception as exc:
        logger.warning("Pré-chauffage catalogue FrenchStream impossible : %s", exc)
    yield
    await close_coflix_client()
    await close_voirdrama_client()
    await close_voiranime_client()
    await close_frenchstream_client()


app = FastAPI(title="NokaTV", lifespan=lifespan)

# Compression transparente des réponses textuelles (HTML, CSS, JS, JSON) —
# actif dès lors que le reverse-proxy frontal ne compresse pas déjà.
app.add_middleware(GZipMiddleware, minimum_size=1024)

BASE_DIR = Path(__file__).resolve().parent

static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)


class VersionedStaticFiles(StaticFiles):
    """Assets locaux versionnés dans le HTML (?v=N) : cache navigateur 1 an.

    Le hash/version figure dans l'URL référencée par les templates et le
    service worker (bump à chaque déploiement) : `immutable` est donc légitime
    et les ré-visites de Googlebot ne re-téléchargent rien.
    """

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/static", VersionedStaticFiles(directory=static_dir), name="static")

# ---------------------------------------------------------------------------
# Route Ma Liste (Favoris / Watchlist)
# ---------------------------------------------------------------------------

@app.get("/ma-liste", response_class=HTMLResponse)
async def watchlist_page(request: Request):
    """Page Ma Liste de favoris."""
    # Contenu personnalisé côté client (localStorage) : thin content en SSR
    # et propre à chaque visiteur -> explicitement non indexable.
    return templates.TemplateResponse(request, "watchlist.html", {
        "request": request,
        "seo": page_seo(request, title="Ma Liste — NokaTV", path="/ma-liste", noindex=True),
    })


# ---------------------------------------------------------------------------
# Pages de confiance (E-E-A-T) : mentions légales & contact
# ---------------------------------------------------------------------------

CONTACT_EMAIL_FALLBACK = "contact@nokatv.xyz"


def _contact_email() -> str:
    return (os.getenv("CONTACT_EMAIL") or "").strip() or CONTACT_EMAIL_FALLBACK


@app.get("/mentions-legales", response_class=HTMLResponse)
async def legal_notice_page(request: Request):
    """Mentions légales — indexables : transparence sur la nature du service
    (agrégateur sans hébergement), procédure de retrait et données locales."""
    return templates.TemplateResponse(request, "mentions_legales.html", {
        "request": request,
        "contact_email": _contact_email(),
        "seo": page_seo(
            request,
            title="Mentions légales — NokaTV",
            description="Mentions légales de NokaTV : nature du service d'agrégation, propriété intellectuelle, procédure de retrait et respect des données personnelles.",
            path="/mentions-legales",
        ),
    })


@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    """Page contact — indexable : signalement de liens morts et demandes de
    retrait pour les ayants droit."""
    return templates.TemplateResponse(request, "contact.html", {
        "request": request,
        "contact_email": _contact_email(),
        "seo": page_seo(
            request,
            title="Contact — NokaTV",
            description="Contacter NokaTV : signalement d'un lien mort, demande de retrait d'une référence (ayants droit) ou suggestion d'amélioration.",
            path="/contact",
        ),
    })


@app.get("/dmca", response_class=HTMLResponse)
async def dmca_page(request: Request):
    """Page DMCA - procédure de signalement et retrait de contenu."""
    return templates.TemplateResponse(request, "dmca.html", {
        "request": request,
        "contact_email": _contact_email(),
        "seo": page_seo(
            request,
            title="DMCA - Droits d'auteur & Retrait — NokaTV",
            description="Procédure DMCA de NokaTV : signalement et retrait de contenu protégé par le droit d'auteur.",
            path="/dmca",
        ),
    })


# Guide indexable sur noka-tv ; lecteur dédié non indexable sur le catalogue.
@app.api_route("/secret-story-afrique", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def secret_story_guide(request: Request):
    alt_domain = (os.getenv("ALTERNATIVES_DOMAIN") or "noka-tv.xyz").strip().lower()
    canonical = f"https://{alt_domain}/secret-story-afrique"
    if not getattr(request.state, "is_alternatives_domain", False):
        from services.secret_story import get_secret_story
        seo = page_seo(
            request,
            title="Secret Story Afrique — Diffusion en Direct sur NokaTV",
            description="Accédez à la diffusion en direct de Secret Story Afrique sur NokaTV. Programme, horaires et accès officiel.",
            path="/secret-story-afrique",
            noindex=True,
            image="https://nokatv.xyz/static/secret-story-guide.png",
        )
        seo.canonical = "https://nokatv.xyz/secret-story-afrique"
        return templates.TemplateResponse(request, "secret_story_watch.html", {
            "request": request, "seo": seo, "secret_story": get_secret_story(),
            "guide_url": canonical,
        }, headers={"X-Robots-Tag": "noindex, follow"})
    seo = page_seo(
        request,
        title="Secret Story Afrique : direct, horaires, replay et vote — NokaTV",
        description="Où regarder Secret Story Afrique ? Chaînes officielles, horaires GMT et Cotonou, replay et plateforme de vote. Guide indépendant de la saison 3.",
        path="/secret-story-afrique",
        image=f"https://{alt_domain}/static/secret-story-guide.png",
        extra_json_ld=[{
            "@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Guides NokaTV", "item": f"https://{alt_domain}/alternative"},
                {"@type": "ListItem", "position": 2, "name": "Secret Story Afrique", "item": canonical},
            ],
        }],
    )
    # SITE_URL reste le domaine catalogue ; ce guide possède sa propre canonical.
    seo.canonical = canonical
    return templates.TemplateResponse(request, "secret_story_guide.html", {
        "request": request, "seo": seo,
        "nokatv_url": "https://nokatv.xyz/secret-story-afrique?utm_source=noka-tv.xyz&utm_medium=guide&utm_campaign=secret-story-afrique#lecteur",
    })


@app.api_route("/secret-story-afrique/", methods=["GET", "HEAD"])
async def secret_story_guide_slash(request: Request):
    alt_domain = (os.getenv("ALTERNATIVES_DOMAIN") or "noka-tv.xyz").strip().lower()
    destination = f"https://{alt_domain}/secret-story-afrique" if getattr(request.state, "is_alternatives_domain", False) else "/secret-story-afrique"
    if request.url.query:
        destination += "?" + request.url.query
    return RedirectResponse(destination, status_code=301)


@app.get("/alternative", response_class=HTMLResponse)
async def alternative_page(request: Request):
    """Observatoire des alternatives — Hub de veille ARCOM 2026.

    Stratégie 2026-10 « coque propre » : page d'information institutionnelle
    indexable répertoriant l'ensemble des sites bloqués et orientant vers
    NokaTV et les services légaux.
    """
    faq_items = [
        (
            "Ces sites de streaming vont-ils revenir ?",
            "Les noms continuent d'exister sous de nouvelles adresses, mais avec le blocage automatisé de l'ARCOM, chaque adresse vit de quelques jours à quelques semaines. Attendre le retour d'un site bloqué n'est plus une stratégie viable en 2026.",
        ),
        (
            "Un VPN suffit-il pour accéder aux sites bloqués ?",
            "Un VPN contourne le blocage DNS, mais l'adresse utilisée reste instable et les faux sites pullulent. Pour un usage régulier, une alternative stable est plus confortable.",
        ),
        (
            "Est-ce risqué de regarder sur un site de streaming gratuit ?",
            "En pratique, en France, les blocages visent les sites et non les spectateurs. Les vrais risques sont techniques : publicités malveillantes et faux sites. Utilisez un bloqueur de publicités et ne renseignez jamais de coordonnées bancaires.",
        ),
    ]
    all_sites = get_all_sites()
    return templates.TemplateResponse(request, "alternative.html", {
        "request": request,
        "sites": all_sites,
        "seo": page_seo(
            request,
            title="Sites de streaming bloqués par l'ARCOM : état des lieux & alternatives (2026)",
            description=f"Observatoire indépendant des {len(all_sites)} sites de streaming bloqués en France (Wiflix, French Stream, Anime Sama, Papadustream...). Solutions stables et alternatives gratuites 2026.",
            path="/alternative",
            extra_json_ld=[
                faq_page_json_ld(faq_items),
                breadcrumb_json_ld(request, [("Alternatives", "/alternative")]),
            ],
        ),
    })


SPORT_FAQ_ITEMS = [
    (
        "Où regarder le foot gratuitement et légalement ?",
        "En clair : certains matchs de Ligue des Champions et de la sélection française sur M6 et TF1, les directs sélectionnés de France.tv Sport, et les résumés gratuits sur L'Équipe. Le reste (Ligue 1, Premier League, Liga) passe par les abonnements DAZN, Canal+ et beIN Sports.",
    ),
    (
        "Pourquoi tous les sites de streaming foot sont-ils bloqués en 2026 ?",
        "Depuis mars 2026, l'ARCOM pilote un blocage DNS dynamique en temps réel des retransmissions sportives : plus de 326 domaines ont été neutralisés, et chaque nouvelle adresse est filtrée par les FAI pendant les matchs en direct.",
    ),
    (
        "Est-ce légal de regarder un match sur un site de streaming gratuit ?",
        "En France, les procédures visent les sites et les diffuseurs, pas les spectateurs. Les vrais risques pour le visiteur sont techniques : publicités malveillantes, faux miroirs qui volent les coordonnées bancaires, et flux coupés en plein match. Un bloqueur de publicités et la méfiance envers les formulaires de paiement restent les deux réflexes essentiels.",
    ),
    (
        "Pourquoi le flux coupe-t-il toujours au coup d'envoi ?",
        "Le blocage des adresses connues est programmé exactement pendant la diffusion, et les serveurs de ces plateformes saturent dès qu'un match populaire commence. Avant le coup d'envoi, tout fonctionne ; pendant le match, plus rien. C'est le fonctionnement normal du dispositif de blocage, pas une panne de votre connexion.",
    ),
    (
        "Quelle alternative quand le match est introuvable ?",
        "Les résumés sont disponibles gratuitement quelques minutes après le coup de sifflet final (L'Équipe, France.tv), et les jours de match sans solution, le catalogue films et séries de NokaTV occupe la soirée. Voir aussi notre observatoire pour l'état à jour des sites bloqués.",
    ),
]


@app.get("/alternative/football", response_class=HTMLResponse)
async def alternative_football_page(request: Request):
    """Page pilier « Football & sport en direct » (stratégie contenu utile 2026-10).

    Cible les requêtes à forte récurrence hebdomadaire (« match ce soir chaîne »,
    « streaming foot bloqué ») avec :
    - le programme des matchs + chaînes officielles (cache 12 h, masqué si la
      source est en panne) — réponse immédiale = le visiteur reste ;
    - une checklist actionnable « ce soir c'est bloqué » ;
    - l'état des lieux des sites sport fichés (maillage vers les détails) ;
    - une FAQ alignée sur les formulations de recherche réelles.
    Zéro flux, zéro lien de stream : même ligne rouge juridique que l'observatoire.
    """
    fixtures = await get_upcoming_matches()
    sport_sites = get_sites_by_category("sport")
    today_matches = [m for m in fixtures if m.get("is_today")]
    if today_matches:
        top_today = today_matches[0]
        foot_desc = (
            f"À l'affiche ({top_today['label']}) : {top_today['home']} vs {top_today['away']} "
            f"({top_today['broadcaster']}). Programme complet des matchs, sites de streaming foot "
            "bloqués par l'ARCOM (Streamonsport, StreamEast...) et alternatives 2026."
        )
    else:
        foot_desc = (
            "Programme des matchs et chaînes officielles, état des lieux des sites de streaming foot "
            "bloqués par l'ARCOM (StreamEast, SportTuna, Streamonsport...) et solutions légales 2026."
        )
    return templates.TemplateResponse(request, "alternative_football.html", {
        "request": request,
        "fixtures": fixtures,
        "sport_sites": sport_sites,
        "faq_items": SPORT_FAQ_ITEMS,
        "total_sites": len(get_all_sites()),
        "seo": page_seo(
            request,
            title="Streaming foot 2026 : matchs ce soir, sites bloqués & alternatives",
            description=foot_desc,
            path="/alternative/football",
            extra_json_ld=[
                faq_page_json_ld(SPORT_FAQ_ITEMS),
                breadcrumb_json_ld(request, [("Alternatives", "/alternative"), ("Football & sport en direct", "/alternative/football")]),
            ],
        ),
    })


DRAMAS_FAQ_ITEMS = [
    (
        "Où regarder des K-Dramas gratuitement en VF et VOSTFR en 2026 ?",
        "Le catalogue /dramas de NokaTV propose les séries coréennes, chinoises et japonaises en VOSTFR et VF sans création de compte. Côté plateformes officielles, Rakuten Viki permet de visionner gratuitement une partie de son catalogue avec publicités, et certaines chaînes coréennes (KBS World, SBS) diffusent des séries complètes sur YouTube.",
    ),
    (
        "Pourquoi VoirDrama, DramaCool et KissAsian ne fonctionnent plus ?",
        "DramaCool et KissAsian ont officiellement fermé fin 2024 sous la pression des ayants droit coréens (CJ ENM, SBS) : les sites qui utilisent encore leur nom en 2026 sont des clones non officiels. VoirDrama subit quant à lui le filtrage DNS des FAI français et change régulièrement d'extension (.org, .tv).",
    ),
    (
        "Existe-t-il des K-Dramas doublés en français (VF) ?",
        "Oui : de plus en plus de séries coréennes disposent d'un doublage français intégral, en plus de la VOSTFR traditionnelle. Sur NokaTV, chaque fiche indique clairement les pistes VF et VOSTFR disponibles.",
    ),
    (
        "Faut-il installer une application comme Pandrama ou HiTV ?",
        "Non, et c'est même déconseillé en dehors des stores officiels : Pandrama et HiTV ayant été retirés de Google Play et de l'App Store, les fichiers APK qui circulent sur les réseaux sociaux comportent souvent des publicités intrusives ou des spywares. Un accès direct dans le navigateur reste beaucoup plus sûr.",
    ),
]


@app.get("/alternative/dramas", response_class=HTMLResponse)
async def alternative_dramas_page(request: Request):
    """Page pilier « K-Dramas & séries asiatiques » — alimente /dramas."""
    drama_sites = get_sites_by_category("dramas")
    return templates.TemplateResponse(request, "alternative_dramas.html", {
        "request": request,
        "drama_sites": drama_sites,
        "faq_items": DRAMAS_FAQ_ITEMS,
        "total_sites": len(get_all_sites()),
        "seo": page_seo(
            request,
            title="K-Dramas en streaming VF & VOSTFR : VoirDrama, DramaCool & alternatives (2026)",
            description="Où regarder des dramas coréens gratuitement en VF et VOSTFR en 2026 ? État des sites bloqués ou fermés (VoirDrama, DramaCool, KissAsian, Pandrama) et solutions fiables.",
            path="/alternative/dramas",
            extra_json_ld=[
                faq_page_json_ld(DRAMAS_FAQ_ITEMS),
                breadcrumb_json_ld(request, [("Alternatives", "/alternative"), ("K-Dramas & séries asiatiques", "/alternative/dramas")]),
            ],
        ),
    })


MANGA_FAQ_ITEMS = [
    (
        "Quelle est la vraie adresse de JapScan et Anime Sama en 2026 ?",
        "Il n'existe plus d'adresse stable : JapScan (.lol, .vip, .ws) et Anime Sama sont inscrits sur les listes de blocage DNS de l'ARCOM chez Orange, SFR, Free et Bouygues Télécom. Chaque nouveau domaine est filtré en quelques jours et copié par des sites miroirs remplis de pop-ups.",
    ),
    (
        "À quelle heure sortent les scans de One Piece et du Shōnen Jump en France ?",
        "Les chapitres officiels traduits en français (One Piece, Sakamoto Days, Kagurabachi, Blue Box) sortent gratuitement chaque dimanche à 17h00 (heure de Paris) sur l'application et le site officiel MANGA Plus by Shueisha, sans aucune publicité intrusive.",
    ),
    (
        "Pourquoi Scantrad et Reaper Scans ont-ils fermé ?",
        "Les éditeurs japonais (Shueisha, Kodansha) et coréens (Kakao, Naver Webtoon) mènent depuis 2024-2026 des actions juridiques directes contre les équipes de traduction non officielles et leurs hébergeurs, entraînant la fermeture définitive de Scantrad France et de Reaper Scans.",
    ),
    (
        "Où regarder les animés adaptés de ces mangas gratuitement en VF et VOSTFR ?",
        "La section /animes de NokaTV regroupe les adaptations animées (One Piece, Solo Leveling, Dandadan, Demon Slayer, Jujutsu Kaisen...) en VF et VOSTFR, avec plusieurs lecteurs au choix et sans création de compte.",
    ),
]


@app.get("/alternative/manga", response_class=HTMLResponse)
async def alternative_manga_page(request: Request):
    """Page pilier « Scans Manga, Webtoons & Animés » — alimente /animes."""
    anime_sites = get_sites_by_category("animes")
    return templates.TemplateResponse(request, "alternative_manga.html", {
        "request": request,
        "anime_sites": anime_sites,
        "faq_items": MANGA_FAQ_ITEMS,
        "total_sites": len(get_all_sites()),
        "seo": page_seo(
            request,
            title="Scans Manga VF & Animés 2026 : JapScan, Lelscan, Anime Sama & alternatives",
            description="Horaires officiels des chapitres en VF, état des sites de scans manga et d'animés bloqués en France (JapScan, Lelscan, Scantrad, Anime Sama, Voiranime) et solutions gratuites 2026.",
            path="/alternative/manga",
            extra_json_ld=[
                faq_page_json_ld(MANGA_FAQ_ITEMS),
                breadcrumb_json_ld(request, [("Alternatives", "/alternative"), ("Scans Manga & Animés", "/alternative/manga")]),
            ],
        ),
    })


F1_UFC_FAQ_ITEMS = [
    (
        "Comment regarder la Formule 1 gratuitement en français ?",
        "En France, Canal+ détient les droits exclusifs de la F1. En revanche, en Belgique (RTBF Auvio / Tipik) et en Suisse (RTS Sport), le service public diffuse gratuitement les Grands Prix en direct et en français pour les résidents de ces pays.",
    ),
    (
        "Sur quelle chaîne voir l'UFC et les grands combats de boxe en France ?",
        "Les galas UFC (Main Card dans la nuit du samedi au dimanche, généralement dès 4h00 heure de Paris) sont diffusés sur RMC Sport, tandis que les préliminaires passent sur l'UFC Fight Pass. Les grands combats internationaux de boxe sont partagés entre DAZN et RMC Sport.",
    ),
    (
        "Pourquoi F1 Live Stream, MethStreams et SportSurge bloquent-ils pendant la course ?",
        "Les blocages DNS ordonnés par l'ARCOM sont activés en temps réel au départ du Grand Prix ou au début de la Main Card UFC, et l'afflux simultané de spectateurs sature les serveurs restants.",
    ),
]


@app.get("/alternative/f1-ufc", response_class=HTMLResponse)
async def alternative_f1_ufc_page(request: Request):
    """Page pilier « Formule 1, UFC, Boxe & MotoGP » avec calendrier auto."""
    events = await get_upcoming_motorsport_combat()
    sport_sites = get_sites_by_category("sport")
    return templates.TemplateResponse(request, "alternative_f1_ufc.html", {
        "request": request,
        "events": events,
        "sport_sites": sport_sites,
        "faq_items": F1_UFC_FAQ_ITEMS,
        "total_sites": len(get_all_sites()),
        "seo": page_seo(
            request,
            title="Streaming F1, UFC & Boxe 2026 : horaires GP, chaînes gratuites & sites bloqués",
            description="Programme des prochains Grands Prix F1, MotoGP, galas UFC et combats de boxe (heure de Paris), chaînes gratuites francophones (RTBF Auvio, RTS) et état des sites bloqués (F1 Live Stream, MethStreams, SportSurge).",
            path="/alternative/f1-ufc",
            extra_json_ld=[
                faq_page_json_ld(F1_UFC_FAQ_ITEMS),
                breadcrumb_json_ld(request, [("Alternatives", "/alternative"), ("Formule 1, UFC & Boxe", "/alternative/f1-ufc")]),
            ],
        ),
    })


DDL_FAQ_ITEMS = [
    (
        "Quelle est la nouvelle adresse de DarkiWorld (ex-Darkino / Tirexo) et WawaCity en 2026 ?",
        "DarkiWorld et WawaCity changent d'adresse et de numéro en permanence car chaque nouveau domaine est inscrit en quelques jours sur la liste de blocage DNS de l'ARCOM chez Orange, SFR, Free et Bouygues Télécom. La majorité des sites qui s'affichent en tête des moteurs de recherche sont des copies malveillantes.",
    ),
    (
        "Pourquoi YggTorrent et Sharewood demandent-ils un compte ou bloquent-ils l'inscription ?",
        "Les trackers BitTorrent semi-privés comme YggTorrent et Sharewood ferment régulièrement leurs inscriptions et imposent un ratio d'envoi strict (ou payant). En outre, le protocole BitTorrent expose l'adresse IP du téléchargeur dans l'essaim P2P, contrairement au streaming direct en HTTPS dans un navigateur.",
    ),
    (
        "Où lire ou télécharger des ebooks gratuitement après le blocage de Fourtoutici et Z-Library ?",
        "Fourtoutici, Z-Library, Anna's Archive et Bookys font l'objet d'ordonnances de blocage DNS en France. Pour un accès gratuit et 100 % légal, utilisez Gallica (Bibliothèque nationale de France), le Projet Gutenberg, Open Library ou le Prêt Numérique en Bibliothèque (PNB) inclus avec votre carte de médiathèque.",
    ),
    (
        "Pourquoi remplacer le téléchargement direct (1fichier, Rapidgator) par le streaming ?",
        "Avec la disparition d'Uptobox et les quotas d'attente de 60 minutes entre deux fichiers sur les hébergeurs gratuits, lancer directement un film ou une série en HD sur NokaTV évite les liens expirés, les débrideurs payants et les fichiers infectés.",
    ),
]


@app.get("/alternative/telechargement", response_class=HTMLResponse)
async def alternative_telechargement_page(request: Request):
    """Page pilier « Téléchargement DDL, Torrents & Ebooks » — capitalise sur Tirexo/WawaCity/DarkiWorld."""
    ddl_sites = get_sites_by_category("ddl-torrent")
    return templates.TemplateResponse(request, "alternative_telechargement.html", {
        "request": request,
        "ddl_sites": ddl_sites,
        "faq_items": DDL_FAQ_ITEMS,
        "total_sites": len(get_all_sites()),
        "seo": page_seo(
            request,
            title="Sites de téléchargement bloqués 2026 : WawaCity, Tirexo, DarkiWorld, Ygg & alternatives",
            description="État des sites de téléchargement direct (DarkiWorld, Darkino, WawaCity, Tirexo), torrents (YggTorrent, Sharewood, Nyaa) et ebooks (Fourtoutici, Z-Library) bloqués en 2026 + solutions en streaming direct.",
            path="/alternative/telechargement",
            extra_json_ld=[
                faq_page_json_ld(DDL_FAQ_ITEMS),
                breadcrumb_json_ld(request, [("Alternatives", "/alternative"), ("Téléchargement DDL, Torrents & Ebooks", "/alternative/telechargement")]),
            ],
        ),
    })


IPTV_FAQ_ITEMS = [
    (
        "Pourquoi mon IPTV (Atlas Pro, Iron TV, King 365) bloque le soir chez Orange, SFR ou Free ?",
        "Depuis 2025-2026, l'ARCOM et les détenteurs de droits sportifs (LFP, Canal+, beIN) activent un blocage dynamique d'adresses IP en temps réel au coup d'envoi des matchs. Les quatre grands FAI français coupent alors l'accès aux serveurs Xtream Codes, ce qui bloque à la fois les chaînes en direct et la VOD du boîtier.",
    ),
    (
        "Pourquoi l'application IPTV Smarters Pro affiche-t-elle « Échec de connexion » ?",
        "IPTV Smarters Pro (comme TiviMate ou XCIPTV) n'est qu'un lecteur vidéo vide et ne fournit aucune chaîne. Si l'écran reste noir, le problème ne vient pas de l'application mais de l'URL du serveur de votre fournisseur qui est bloquée par votre opérateur ou hors service.",
    ),
    (
        "Par quoi remplacer Mamcin pour voir la TV et les émissions en replay sans compte ?",
        "Pluto TV permet de regarder plus de 100 chaînes et séries en direct gratuitement sans créer de compte. Pour les replays officiels des grandes chaînes françaises, France.tv, TF1+ et M6+ restent les sources gratuites de référence, et NokaTV donne accès aux films, séries, animés et dramas sans inscription.",
    ),
    (
        "Où regarder des séries turques (Dizi) en VF et VOSTFR quand Diziturk ne marche plus ?",
        "De nombreuses chaînes officielles turques (ATV, Kanal D, Star TV) publient désormais leurs épisodes complets gratuitement sur YouTube avec sous-titres, et NokaTV propose une sélection de séries internationales et dramas en VF et VOSTFR.",
    ),
]


@app.get("/alternative/iptv", response_class=HTMLResponse)
async def alternative_iptv_page(request: Request):
    """Page pilier « IPTV, Replay TV & Séries Turques » — capte les pannes IPTV du soir."""
    iptv_slugs = {
        "atlas-pro", "iron-tv", "king365", "iptv-smarters",
        "mamcin", "diziturk", "turksubfr", "xalaflix",
        "streamonsport", "livetv", "nbabite", "stream2watch",
    }
    iptv_sites = [s for s in get_all_sites() if s["slug"] in iptv_slugs]
    return templates.TemplateResponse(request, "alternative_iptv.html", {
        "request": request,
        "iptv_sites": iptv_sites,
        "faq_items": IPTV_FAQ_ITEMS,
        "total_sites": len(get_all_sites()),
        "seo": page_seo(
            request,
            title="IPTV bloquée 2026 (Orange, SFR, Free) : Atlas Pro, Iron TV, Smarters & solutions",
            description="Pourquoi votre IPTV (Atlas Pro ONTV, Iron TV Pro, King 365, IPTV Smarters) coupe à 21h chez Orange, SFR et Free ? Explications du blocage ARCOM, état de Mamcin/Diziturk et solutions VOD sans coupure.",
            path="/alternative/iptv",
            extra_json_ld=[
                faq_page_json_ld(IPTV_FAQ_ITEMS),
                breadcrumb_json_ld(request, [("Alternatives", "/alternative"), ("IPTV, Replay TV & Séries Turques", "/alternative/iptv")]),
            ],
        ),
    })


NETFLIX_FAQ_ITEMS = [
    (
        "Quelle est la meilleure alternative gratuite à Netflix sans compte en 2026 ?",
        "Pour retrouver une interface fluide similaire à Netflix avec films, séries, animés et K-Dramas en VF et VOSTFR sans créer de compte, NokaTV offre un accès direct dans le navigateur. Côté plateformes officielles financées par la publicité (AVOD), Pluto TV, Arte.tv, France.tv et Rakuten TV permettent aussi de regarder des films et séries gratuitement et légalement.",
    ),
    (
        "Existe-t-il encore un mois d'essai gratuit sur Netflix en France ?",
        "Non : Netflix a définitivement supprimé son offre d'essai gratuit de 30 jours en France, tout comme Disney+. Toute page web qui promet un mois gratuit Netflix contre une empreinte de carte bancaire est une tentative d'hameçonnage (phishing).",
    ),
    (
        "Quel site ressemble à Netflix mais gratuitement ?",
        "Les anciens clones en -flix (Coflix, Wiflix, Sadisflix, Filmoflix, Xalaflix) sont désormais bloqués par filtrage DNS chez Orange, SFR, Free et Bouygues Télécom. NokaTV reprend une navigation sombre par affiches et catégories (Films, Séries, Animés, Dramas) accessible gratuitement sans inscription.",
    ),
    (
        "Quelle alternative gratuite à Crunchyroll et Disney+ en 2026 ?",
        "Pour les animés japonais en VF et VOSTFR, la section /animes de NokaTV complète les chapitres gratuits officiels de MANGA Plus. Pour les séries et le cinéma familial, Pluto TV et France.tv proposent des catalogues gratuits sans abonnement.",
    ),
]


@app.get("/alternative/netflix", response_class=HTMLResponse)
async def alternative_netflix_page(request: Request):
    """Page pilier « Alternative à Netflix gratuite » — cible 'alternative netflix' & 'site comme netflix'."""
    flix_slugs = {
        "coflix", "wiflix", "sadisflix", "filmoflix", "xalaflix",
        "alloflix", "monflix", "gogoflix", "yopflix", "filmnetflix",
        "french-stream", "papadustream",
    }
    flix_sites = [s for s in get_all_sites() if s["slug"] in flix_slugs]
    return templates.TemplateResponse(request, "alternative_netflix.html", {
        "request": request,
        "flix_sites": flix_sites,
        "faq_items": NETFLIX_FAQ_ITEMS,
        "total_sites": len(get_all_sites()),
        "seo": page_seo(
            request,
            title="Alternative à Netflix gratuite (2026) : les meilleurs sites comme Netflix sans compte",
            description="Quelle alternative gratuite à Netflix, Disney+ et Crunchyroll en 2026 ? Comparatif des sites comme Netflix sans compte ni carte bancaire (NokaTV, Pluto TV, Arte.tv...) et état des sites en -flix.",
            path="/alternative/netflix",
            extra_json_ld=[
                faq_page_json_ld(NETFLIX_FAQ_ITEMS),
                breadcrumb_json_ld(request, [("Alternatives", "/alternative"), ("Alternative à Netflix gratuite", "/alternative/netflix")]),
            ],
        ),
    })


@app.get("/alternative/{slug}", response_class=HTMLResponse)
async def alternative_detail_page(request: Request, slug: str):
    """Page dédiée à un site spécifique (SEO chirurgical sur 'X ne marche plus').

    Générée dynamiquement pour chacun des 90+ sites répertoriés sans risque
    de plainte DMCA (zéro player/flux sur ces pages).
    """
    site = get_site(slug)
    if not site:
        raise HTTPException(status_code=404, detail="Site non répertorié dans l'observatoire")

    related_sites = get_related_sites(slug)

    name = site["name"]
    legal_names = ", ".join(site.get("legal_alternatives", []))
    site_faq = [
        (
            f"Une erreur sur {name} signifie-t-elle que le site est fermé ?",
            "Non. Une panne, une adresse modifiée, une restriction locale ou un lecteur indisponible peuvent produire la même erreur. Cette fiche ne fournit pas de test d'accès en temps réel.",
        ),
        (
            f"Comment vérifier une adresse présentée comme celle de {name} ?",
            "Un nom, un logo ou une position dans Google ne prouvent pas qu'une adresse est authentique. Ne fournissez pas de coordonnées bancaires et n'installez pas de fichier imposé par un prétendu lecteur.",
        ),
        (
            f"Quelles offres officielles comparer pour remplacer {name} ?",
            f"Pistes à comparer : {legal_names}. Le catalogue, le prix et la disponibilité varient selon le pays et les droits de diffusion. Vérifiez l'offre officielle pour le titre recherché."
            if legal_names else "Consultez le dossier de la catégorie pour comparer les offres officielles selon votre pays et les contenus recherchés.",
        ),
    ]

    # Meta description en langage de recherche utilisateur (« ne marche plus »,
    # « nouvelle adresse », « bloqué ») alignée sur le statut réel du site.
    if site.get("status_type") == "rotation":
        description = (
            f"{name} change d'adresse en permanence en 2026 : dernière adresse connue, "
            "risques des faux miroirs et alternatives stables qui fonctionnent."
        )
    else:
        description = (
            f"{name} ne marche plus en 2026 : il est bloqué en France. "
            "Nouvelle adresse, risques des faux miroirs et alternatives gratuites qui fonctionnent."
        )

    return templates.TemplateResponse(request, "alternative_detail.html", {
        "request": request,
        "site": site,
        "related_sites": related_sites,
        "category_guides": CATEGORY_GUIDES.get(site["category"], []),
        "faq_items": site_faq,
        "seo": page_seo(
            request,
            title=f"{site['name']} ne marche plus ? État du site & Alternatives (2026)",
            description=description,
            path=f"/alternative/{slug}",
            extra_json_ld=[
                faq_page_json_ld(site_faq),
                breadcrumb_json_ld(request, [("Alternatives", "/alternative"), (site["name"], f"/alternative/{slug}")]),
            ],
        ),
    })


# ---------------------------------------------------------------------------
# SEO : Sitemap.xml & Robots.txt
# ---------------------------------------------------------------------------

@app.api_route("/robots.txt", methods=["GET", "HEAD"], response_class=PlainTextResponse)
async def robots_txt(request: Request):
    """Robots.txt : tout le HTML public est crawlable, sauf l'API (JSON sans
    valeur d'indexation) qui gaspillerait le budget de crawl.

    Les pages players / recherche / ma-liste ne sont PAS bloquées ici :
    elles portent un meta noindex que Googlebot doit pouvoir lire (une page
    bloquée dans robots.txt mais indexée ne peut pas être dé-indexée).

    P0 Fix: ajoute sitemap-index pour le crawl budget des 1019 Détectée.
    V2 Fix: Allow /api/image-proxy pour que Googlebot puisse fetch les images
    et afficher rich results (Apparence vide dans GSC car /api/ bloqué).
    """
    base_url = site_origin(request)
    return (
        "User-agent: *\n"
        "Allow: /api/image-proxy\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "\n"
        f"Sitemap: {base_url}/sitemap.xml\n"
        f"Sitemap: {base_url}/sitemap-index.xml\n"
    )


# ---------------------------------------------------------------------------
# Middleware canonical domain (P0 bis - fix http/https/www chaos vu dans Pages.csv)
# ---------------------------------------------------------------------------
from fastapi.responses import RedirectResponse

@app.middleware("http")
async def canonical_domain_redirect(request: Request, call_next):
    """Force https + non-www pour consolider le jus SEO.

    Pages.csv montre 4 variantes indexées:
    http://nokatv.xyz, http://www.nokatv.xyz, https://www.nokatv.xyz, https://nokatv.xyz
    → dilution, 13 canonical mismatch, 114 canonicalisée.
    On 301 tout vers SITE_URL si défini, sinon vers https:// + host sans www.
    Exclut /api/image-proxy et health checks pour éviter boucles.
    Merge HEAD + origin/master: garde bypass localhost/test + SITE_URL logic.
    """
    path = request.url.path
    # Sécurité critique : bloquer immédiatement toute tentative d'accès à .git, .env ou fichiers sensibles
    if (
        path.startswith("/.git")
        or path.startswith("/.env")
        or "/.git/" in path
        or path.endswith((".py", ".db", ".sqlite", ".sqlite3", ".env"))
        or path in ("/service_account.json", "/.gitignore")
    ):
        return Response(status_code=404)

    # Ne pas interférer avec image-proxy et fichiers statiques
    if path.startswith("/static/") or path.startswith("/api/image-proxy") or path == "/sw.js":
        return await call_next(request)

    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    # Ne pas interférer avec le développement local ou les tests (origin/master)
    if any(local in host for local in ("localhost", "127.0.0.1", "0.0.0.0", "testserver")):
        return await call_next(request)

    raw_host = host.split(":")[0].strip().lower()

    # Porte « observatoire » (stratégie 2026-10) : le domaine ALTERNATIVES_DOMAIN
    # ne sert QUE l'observatoire des alternatives — aucun média ne doit y être
    # exposé (coquille propre : la porte coupe l'accès aux fiches/listes/players
    # même si quelqu'un devine leurs URLs sur ce domaine).
    alt_domain = (os.getenv("ALTERNATIVES_DOMAIN") or "noka-tv.xyz").strip().lower()
    if raw_host in (alt_domain, f"www.{alt_domain}"):
        if raw_host.startswith("www."):
            return RedirectResponse(f"https://{alt_domain}{path}", status_code=301)
        if path == "/":
            return RedirectResponse(f"https://{alt_domain}/alternative", status_code=301)
        alt_allowed = (
            path in ("/alternative", "/secret-story-afrique", "/secret-story-afrique/")
            or path.startswith("/alternative/")
            or path in ("/mentions-legales", "/contact", "/dmca",
                        "/robots.txt", "/sitemap.xml", "/sitemap-index.xml",
                        "/favicon.ico", "/apple-touch-icon.png")
            or path.startswith("/static/")
        )
        if not alt_allowed:
            return Response(status_code=404)
        # Marque la requête : les templates masquent la navigation média et
        # font pointer leurs liens catalogue vers le domaine principal.
        request.state.is_alternatives_domain = True
        return await call_next(request)

    # Détection des en-têtes Cloudflare et reverse proxies (évite les boucles de redirection SSL)
    is_behind_cf = bool(request.headers.get("cf-ray") or request.headers.get("cf-connecting-ip"))
    cf_visitor = request.headers.get("cf-visitor", "")
    if "https" in cf_visitor:
        scheme = "https"
    else:
        scheme = (
            request.headers.get("x-forwarded-proto")
            or request.headers.get("x-forwarded-ssl")
            or request.url.scheme
            or "https"
        ).split(",")[0].strip().lower()

    configured = (os.getenv("SITE_URL") or "").strip().rstrip("/")
    if configured:
        try:
            parsed = urllib.parse.urlparse(configured)
            canonical_host = (parsed.netloc or "").split(":")[0].strip().lower()
            canonical_scheme = parsed.scheme or "https"
        except Exception:
            canonical_host = None
            canonical_scheme = "https"

        if canonical_host:
            # Domaine racine (ex: nokatv.xyz)
            apex_domain = canonical_host[4:] if canonical_host.startswith("www.") else canonical_host

            # Domaines liés/miroirs supplémentaires déclarés via variable d'environnement (séparés par virgule)
            extra_linked = {
                d.strip().lower()
                for d in os.getenv("ALLOWED_DOMAINS", os.getenv("ALLOWED_MIRRORS", "")).split(",")
                if d.strip()
            }

            # 1. Domaine principal apex (ex: nokatv.xyz)
            if raw_host == apex_domain:
                # Si derrière Cloudflare, Cloudflare gère déjà le HTTPS : ne pas rediriger pour éviter boucle 301
                if is_behind_cf:
                    return await call_next(request)
                if scheme != canonical_scheme:
                    url = f"{canonical_scheme}://{apex_domain}{request.url.path}"
                    if request.url.query:
                        url += f"?{request.url.query}"
                    return RedirectResponse(url, status_code=301)
                return await call_next(request)

            # 2. www du domaine principal -> redirige 301 vers le domaine principal pour préserver le SEO
            if raw_host == f"www.{apex_domain}":
                url = f"{canonical_scheme}://{apex_domain}{request.url.path}"
                if request.url.query:
                    url += f"?{request.url.query}"
                return RedirectResponse(url, status_code=301)

            # 3. Tous les sous-domaines liés (ex: ww1.nokatv.xyz, ww2.nokatv.xyz, ...)
            #    ou domaines supplémentaires autorisés
            is_subdomain = raw_host.endswith(f".{apex_domain}")
            is_extra_domain = raw_host in extra_linked or any(raw_host.endswith(f".{d}") for d in extra_linked)

            if is_subdomain or is_extra_domain:
                target_host = raw_host
                # Supprimer www. devant un sous-domaine (ex: www.ww1.nokatv.xyz -> ww1.nokatv.xyz)
                if target_host.startswith("www."):
                    target_host = target_host[4:]

                # Forcer HTTPS sur ce sous-domaine uniquement hors Cloudflare pour éviter boucle
                needs_redirect = (target_host != raw_host) or (not is_behind_cf and scheme != "https")
                if needs_redirect:
                    url = f"https://{target_host}{request.url.path}"
                    if request.url.query:
                        url += f"?{request.url.query}"
                    return RedirectResponse(url, status_code=301)

                # Servi directement par l'application principale !
                return await call_next(request)

            # 4. Hôte inconnu / externe -> redirection 301 vers l'URL officielle
            url = f"{configured}{request.url.path}"
            if request.url.query:
                url += f"?{request.url.query}"
            return RedirectResponse(url, status_code=301)
    else:
        should_redirect = False
        new_host = raw_host
        new_scheme = scheme
        if raw_host.startswith("www."):
            new_host = raw_host[4:]
            should_redirect = True
        if scheme == "http":
            new_scheme = "https"
            should_redirect = True
        if should_redirect:
            url = f"{new_scheme}://{new_host}{request.url.path}"
            if request.url.query:
                url += f"?{request.url.query}"
            return RedirectResponse(url, status_code=301)

    return await call_next(request)


# ---------------------------------------------------------------------------
# PWA légère : service worker servi à la racine (scope "/" obligatoire)
# ---------------------------------------------------------------------------


@app.get("/sw.js")
async def service_worker():
    """Sert le service worker dynamique avec hachage automatique des assets du shell."""
    return Response(
        content=generate_service_worker(),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.api_route("/sitemap.xml", methods=["GET", "HEAD"])
async def sitemap_xml(request: Request):
    """Sitemap dynamique : pages statiques + fiches de contenu réelles.

    Le trafic organique d'un site de streaming vient du long tail des fiches
    (« regarder X en streaming ») : le sitemap les expose directement à
    Google au lieu d'attendre une découverte par pagination. Les slugs sont
    collectés depuis les sources et cachés 12 h (services/sitemap.py).

    P0 Fix SEO (audit 2026-09-06):
    - Ajout <lastmod> réel depuis cache updated_at (aide Google à prioriser)
    - SITEMAP_MAX_PAGES réduit à 2 par défaut pour éviter 1019 Détectée non indexée
    """
    import datetime
    base_url = site_origin(request)

    # Coque observatoire (2026-10) : sur le domaine dédié, la racine est une
    # redirection 301 vers /alternative — on ne soumet pas d'URL qui redirige.
    alt_domain = (os.getenv("ALTERNATIVES_DOMAIN") or "noka-tv.xyz").strip().lower()
    request_host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0].strip().lower()
    on_alt_domain = request_host in (alt_domain, f"www.{alt_domain}")
    if on_alt_domain:
        base_url = f"https://{alt_domain}"

    try:
        paths = await collect_sitemap_paths()
        stale = cache.get_stale("sitemap:paths")
        updated_ts = stale.get("updated_at") if stale else None
        if updated_ts:
            lastmod_date = datetime.datetime.fromtimestamp(updated_ts, tz=datetime.timezone.utc).date().isoformat()
        else:
            lastmod_date = datetime.date.today().isoformat()
    except Exception as exc:
        logger.warning("Sitemap : collecte impossible, version statique seule (%s)", exc)
        paths = list(STATIC_SITEMAP_PATHS) + list(LEGAL_SITEMAP_PATHS)
        lastmod_date = datetime.date.today().isoformat()

    # Copier la liste potentiellement cachée, sans modifier le sitemap catalogue.
    paths = list(paths)
    if on_alt_domain and "/secret-story-afrique" not in paths:
        paths.append("/secret-story-afrique")

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for path in paths:
        if on_alt_domain and path == "/":
            continue
        if path == "/":
            priority = "1.0"
        elif path == "/alternative":
            priority = "0.9"
        elif path in LEGAL_SITEMAP_PATHS:
            priority = "0.3"
        elif path.startswith("/alternative/"):
            priority = "0.8"
        else:
            priority = "0.7"
        loc = escape(base_url + path)
        if "?" in path:
            xml_lines.append(f"  <url><loc>{loc}</loc><priority>{priority}</priority></url>")
        else:
            xml_lines.append(f"  <url><loc>{loc}</loc><lastmod>{lastmod_date}</lastmod><priority>{priority}</priority></url>")

    xml_lines.append("</urlset>")
    return Response(
        content="\n".join(xml_lines),
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.api_route("/sitemap.xml/", methods=["GET", "HEAD"])
async def sitemap_xml_trailing_slash(request: Request):
    """Redirection permanente pour corriger la saisie avec slash final dans GSC."""
    return RedirectResponse(url="/sitemap.xml", status_code=301)


@app.api_route("/sitemap-index.xml", methods=["GET", "HEAD"])
async def sitemap_index(request: Request):
    """Index de sitemaps par catégorie (facilite le crawl budget pour les 1019 Détectée)."""
    import datetime
    base_url = site_origin(request)
    today = datetime.date.today().isoformat()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>{base_url}/sitemap.xml</loc><lastmod>{today}</lastmod></sitemap>
</sitemapindex>"""
    return Response(content=xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


# ---------------------------------------------------------------------------
# IndexNow : fichier de vérification de la clé (https://{hôte}/{clé}.txt)
# ---------------------------------------------------------------------------


@app.get("/{key}.txt", response_class=PlainTextResponse)
async def indexnow_key_file(key: str):
    """Sert la clé IndexNow à l'emplacement exact exigé par le protocole.

    IndexNow vérifie la propriété du domaine en demandant ce fichier
    (https://nokatv.xyz/{clé}.txt). Sans clé configurée (ou avec une clé qui
    ne correspond pas), on répond 404 : la route n'expose jamais autre chose.
    """
    expected = indexnow_key()
    if expected and key == expected:
        return expected
    raise HTTPException(status_code=404, detail="Clé inconnue")


# ---------------------------------------------------------------------------
# Proxy d'images (Contournement de la protection anti-hotlink 403)
# ---------------------------------------------------------------------------

@app.get("/api/image-proxy")
async def image_proxy(url: str = Query(...)):
    """Relaye les images distantes avec le bon Referer pour contourner les erreurs 403."""
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    # Déterminer le bon Referer selon la source
    if "voirdrama" in url:
        referer = "https://voirdrama.to/"
        client = get_voirdrama_client()
    elif "voir-anime" in url:
        referer = "https://voir-anime.to/"
        client = get_voiranime_client()
    else:
        referer = "https://coflix.wiki/"
        client = get_voirdrama_client()

    try:
        r = await client.get(
            url,
            headers={
                "Referer": referer,
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
            timeout=10.0,
        )
        if r.status_code == 200:
            content_type = r.headers.get("content-type", "image/jpeg")
            return Response(
                content=r.content,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=604800, immutable",
                },
            )
    except Exception as exc:
        logger.warning("Erreur image proxy pour %s : %s", url, exc)

    placeholder = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">'
        '<rect width="300" height="450" fill="#131622"/>'
        '<text x="50%" y="50%" font-family="sans-serif" font-size="16" fill="#64748b" text-anchor="middle" dominant-baseline="middle">'
        'Affiche indisponible'
        '</text></svg>'
    )
    return Response(content=placeholder, media_type="image/svg+xml")


# ---------------------------------------------------------------------------
# Gestion des requêtes CDN / Scripts tiers embarqués
# ---------------------------------------------------------------------------

@app.get("/cdn-cgi/{path:path}")
async def cdn_cgi_fallback(path: str):
    """Réponse silencieuse pour les scripts Cloudflare relatifs exécutés par des iframes tierces."""
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Gestion des erreurs
# ---------------------------------------------------------------------------

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    wants_html = "text/html" in (request.headers.get("accept") or "")
    is_api = request.url.path.startswith("/api")
    if wants_html and not is_api:
        status = exc.status_code
        msg = {
            404: "Cette page est introuvable.",
            502: "Le site source ne répond pas, réessaie dans un instant.",
        }.get(status, "Une erreur inattendue s'est produite.")
        return templates.TemplateResponse(
            request, "error.html",
            {"request": request, "status_code": status, "message": msg,
             "seo": page_seo(request, title=f"Erreur {status} — NokaTV", noindex=True)},
            status_code=status,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def server_error_handler(request: Request, exc: Exception):
    logger.exception("Erreur serveur non gérée sur %s : %s", request.url.path, exc)
    wants_html = "text/html" in (request.headers.get("accept") or "")
    if wants_html:
        return templates.TemplateResponse(
            request, "error.html",
            {"request": request, "status_code": 500, "message": "Erreur serveur interne.",
             "seo": page_seo(request, title="Erreur 500 — NokaTV", noindex=True)},
            status_code=500,
        )
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


# ---------------------------------------------------------------------------
# Routeurs
# ---------------------------------------------------------------------------

app.include_router(home.router)
app.include_router(detail.router)
app.include_router(player.router)
app.include_router(drama.router)
app.include_router(anime.router)
app.include_router(search.router)
