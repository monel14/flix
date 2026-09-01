"""Tests du parser FrenchStream (scraper/frenchstream_parser.py) — sans réseau."""

from scraper.frenchstream_parser import (
    episode_servers,
    normalize_title,
    parse_frenchstream_category,
    parse_frenchstream_episodes,
    parse_frenchstream_fiche_title,
    parse_frenchstream_poster,
    parse_frenchstream_search,
    parse_frenchstream_sitemap,
    pick_best_match,
    slugify,
)

CATEGORY_HTML = """
<div id='dle-content'>
  <div class="short"><a class="short-poster img-box with-mask"
     href="/index.php?newsid=15128446" alt="The First Jasmine - Saison 1">
  <a class="short-poster" href="/index.php?newsid=15135861" alt="Comme un Rat - Saison 1">
  <a class="short-poster" href="/index.php?newsid=15100001" alt="Dream to You - Saison 2 (2025)">
</div>
"""

SEARCH_HTML = """
<div class="asp_content">
  <h3><a class="asp_res_url" href="https://french-stream.one/films/15128446-the-first-jasmine-streaming-complet.html">The First Jasmine</a></h3>
  <h3><a class="asp_res_url" href="https://french-stream.one/films/7795-squid-game-streaming-complet.html">Squid Game</a></h3>
  <h3><a class="asp_res_url" href="https://french-stream.one/films/1007-squid-game-le-defi-streaming-complet.html">Squid Game : Le défi</a></h3>
</div>
"""

EPISODES_JSON = {
    "vf": {},
    "vostfr": {
        "1": {"uqload": "https://uqload.vc/embed-abc.html", "vidzy": "https://vidzy.cc/embed-def.html"},
        "2": {"uqload": "https://uqload.vc/embed-ghi.html"},
    },
    "vo": {},
    "info": {"1": {"title": "Épisode 1", "synopsis": "", "poster": ""}},
}


def test_normalize_title():
    assert normalize_title("The First Jasmine - Saison 1") == "The First Jasmine"
    assert normalize_title("Dream to You - Saison 2 (2025)") == "Dream to You"
    assert normalize_title("Big - Saison 1") == "Big"


def test_slugify():
    assert slugify("The First Jasmine") == "the-first-jasmine"
    assert slugify("À demain au bureau !") == "a-demain-au-bureau"
    assert slugify("L'École des sorciers") == "l-ecole-des-sorciers"


def test_parse_category():
    items = parse_frenchstream_category(CATEGORY_HTML)
    assert len(items) == 3
    assert items[0] == {"newsid": 15128446, "title": "The First Jasmine", "slug": "the-first-jasmine"}
    assert items[1]["title"] == "Comme un Rat"
    assert items[2]["title"] == "Dream to You"  # année retirée


def test_parse_search():
    items = parse_frenchstream_search(SEARCH_HTML)
    assert len(items) == 3
    assert items[0]["newsid"] == 15128446
    assert items[0]["slug"] == "the-first-jasmine"


def test_pick_best_match():
    items = parse_frenchstream_search(SEARCH_HTML)
    best = pick_best_match(items, "the-first-jasmine")
    assert best and best["newsid"] == 15128446

    # Fallback par préfixe
    best2 = pick_best_match(items, "squid-game")
    assert best2 and best2["newsid"] == 7795

    # Aucun match pertinent
    assert pick_best_match(items, "totalement-inconnu") is None


def test_pick_best_match_refuse_token_unique():
    """Un seul token partagé (même rare) doit être refusé : ça évite les faux
    positifs comme 20th-century-boys -> mid-century-modern (via « century »)."""
    index = [
        {"newsid": 15120410, "slug": "mid-century-modern"},
        {"newsid": 15127013, "slug": "us-against-the-world-four-years-with-the-mens-national-soccer-team"},
    ]
    # « century » seul partagé -> refusé
    assert pick_best_match(index, "20th-century-boys-1-beginning-of-the-end") is None
    # « world » seul partagé -> refusé
    assert pick_best_match(index, "0-1-world") is None
    # deux tokens partagés d'un coup -> accepté
    index2 = [{"newsid": 1, "slug": "death-robots-detectives"}, {"newsid": 2, "slug": "totally-unrelated"}]
    best = pick_best_match(index2, "love-death-robots")
    assert best and best["newsid"] == 1


def test_pick_best_match_prefix():
    """Préfixe valide : 6ixtynin9 vs 6ixtynin9-la-srie."""
    index = [{"newsid": 15114866, "slug": "6ixtynin9-la-srie"}]
    best = pick_best_match(index, "6ixtynin9")
    assert best and best["newsid"] == 15114866


SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://french-stream.one/13241-taxi-driver-1976.html</loc></url>
  <url><loc>https://french-stream.one/15107191-taxi-driver-saison-1.html</loc></url>
  <url><loc>https://french-stream.one/15113544-taxi-driver-saison-2.html</loc></url>
  <url><loc>https://french-stream.one/15123462-taxi-driver-saison-3-2021.html</loc></url>
  <url><loc>https://french-stream.one/15128446-the-first-jasmine-saison-1-2026.html</loc></url>
  <url><loc>https://french-stream.one/7795-squid-game-saison-2-2021.html</loc></url>
</urlset>
"""


FICHE_HTML = """<div id="serie-data"
    data-newsid="15135861"
    data-title="Comme un Rat - Saison 1"
    data-affiche="https://image.tmdb.org/t/p/w500/faNbrtYCNsZJzLWEKNBkYdIT2gm.jpg"
    style="display:none;"></div>"""


def test_parse_poster_et_titre_fiche():
    assert parse_frenchstream_poster(FICHE_HTML) == "https://image.tmdb.org/t/p/w500/faNbrtYCNsZJzLWEKNBkYdIT2gm.jpg"
    assert parse_frenchstream_fiche_title(FICHE_HTML) == "Comme un Rat - Saison 1"
    # HTML sans fiche -> None
    assert parse_frenchstream_poster("<html></html>") is None
    assert parse_frenchstream_fiche_title("<html></html>") is None


def test_parse_sitemap():
    index = parse_frenchstream_sitemap(SITEMAP_XML)
    # Le film (sans "saison") est exclu
    assert 13241 not in index.values()
    # Slug normalisé -> newsid ; priorité à la saison 1 pour taxi-driver
    assert index["taxi-driver"] == 15107191
    assert index["the-first-jasmine"] == 15128446
    assert index["squid-game"] == 7795


def test_parse_episodes_and_episode_servers():
    parsed = parse_frenchstream_episodes(EPISODES_JSON)
    assert set(parsed.keys()) == {"vf", "vostfr", "vo"}
    assert 1 in parsed["vostfr"] and 2 in parsed["vostfr"]

    servers = episode_servers(parsed, "1")
    assert len(servers) == 2
    assert servers[0]["server_name"] == "uqload"
    assert servers[0]["server_link"].startswith("https://uqload.vc/")

    # VOSTFR préféré, puis VF, puis VO
    assert episode_servers(parsed, "2")[0]["server_name"] == "uqload"
    assert episode_servers(parsed, "99") == []


def test_episode_servers_apres_roundtrip_json():
    """Les clés d'épisodes deviennent des chaînes après stockage JSON (cache) :
    episode_servers doit gérer les deux formes."""
    parsed = parse_frenchstream_episodes(EPISODES_JSON)
    # Simule le round-trip par le cache SQLite (json.dumps puis json.loads)
    after_cache = __import__("json").loads(__import__("json").dumps(parsed))

    servers = episode_servers(after_cache, "1")
    assert len(servers) == 2
    assert servers[0]["server_name"] == "uqload"

    servers2 = episode_servers(after_cache, "2")
    assert len(servers2) == 1
    assert servers2[0]["server_name"] == "uqload"
