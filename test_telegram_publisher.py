"""Tests de la file et du client de publication Telegram.

Aucun token ni appel réseau réel : httpx.MockTransport et des collecteurs
injectés vérifient l'idempotence, la baseline, les retries et le format des
messages.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import ClassVar
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx
import pytest

from scripts.publish_telegram import seconds_until_next_run
from services import telegram_publisher
from services.telegram_publisher import (
    CATEGORY_ANIMATION,
    CATEGORY_ANIMES,
    CATEGORY_FILMS,
    CATEGORY_SERIES,
    CachedRecord,
    ClaimedPost,
    DiscoveryBatch,
    Publication,
    TelegramBotClient,
    TelegramConfigurationError,
    TelegramPublicationStore,
    TelegramPublisher,
    TelegramPublishError,
    TelegramSettings,
    _cached_snapshot,
    _collect_paginated_items,
    _episode_publications_from_animes,
    _episode_publications_from_coflix,
    _movie_publications,
    _split_fresh_cached_animes,
    _split_fresh_cached_series,
)


def settings(
    *,
    channels: dict[str, str] | None = None,
    enabled: bool = True,
    discovery_mode: str = "hybrid",
) -> TelegramSettings:
    return TelegramSettings(
        enabled=enabled,
        bot_token="test-token-never-real",
        site_url="https://nokatv.example",
        channels=channels or {
            CATEGORY_FILMS: "@nokatv_films",
            CATEGORY_SERIES: "@nokatv_series",
            CATEGORY_ANIMES: "@nokatv_manga",
            CATEGORY_ANIMATION: "@nokatv_animation",
        },
        request_retries=2,
        retry_base_seconds=0.01,
        lease_seconds=60,
        discovery_mode=discovery_mode,
    )


def make_publication(
    category: str = CATEGORY_FILMS,
    key: str = "test:one",
    *,
    target_path: str | None = None,
    image: str = "/static/poster.jpg",
    kind: str = "content",
) -> Publication:
    if target_path is None:
        target_path = "/anime/titre-vostfr" if category == CATEGORY_ANIMES else "/film/titre-vf"
    return Publication(
        category=category,
        key=key,
        title="Titre <non interprété>",
        target_path=target_path,
        image=image,
        subtitle="Épisode 2" if kind == "episode" else "",
        version="VF & VOSTFR",
        kind=kind,
    )


def make_post(
    category: str = CATEGORY_FILMS,
    key: str = "test:one",
    *,
    target_path: str | None = None,
    image: str = "/static/poster.jpg",
    kind: str = "content",
):
    post = make_publication(category, key, target_path=target_path, image=image, kind=kind).to_post(settings())
    assert post is not None
    return post


def claim_one(store: TelegramPublicationStore, post, *, image: str | None = None, timestamp: float = 10.0) -> ClaimedPost:
    store.register_discoveries(post.category, [post], timestamp=timestamp)
    # Première insertion = baseline ; la seconde crée une vraie nouveauté.
    post_image = image if image is not None else (post.image_url or "/static/poster.jpg")
    newer = make_post(post.category, post.key + ":new", target_path=post.target_url.replace("https://nokatv.example", ""), image=post_image)
    store.register_discoveries(post.category, [post, newer], timestamp=timestamp + 1)
    claimed = store.claim_due([post.category], timestamp=timestamp + 2)
    assert len(claimed) == 1
    return claimed[0]


def test_publication_ne_partage_que_la_fiche_locale_et_echappe_la_legende():
    post = make_post()
    assert post.target_url == "https://nokatv.example/film/titre-vf"
    assert post.image_url == "https://nokatv.example/static/poster.jpg"
    assert "&lt;non interprété&gt;" in post.caption
    assert "VF &amp; VOSTFR" in post.caption
    assert "NOUVEAU FILM" in post.caption
    assert "Audio :" in post.caption
    assert "Qualité :" in post.caption
    assert "Bon visionnage sur NokaTV" in post.caption


def test_publication_rich_caption_formatting():
    # Film avec genres, année et synopsis
    film_pub = Publication(
        category=CATEGORY_FILMS,
        key="film:rich",
        title="Inception",
        target_path="/film/inception-vf",
        genres=["Action", "Science-Fiction"],
        year="2010",
        version="VF",
        quality="4K HDR",
        synopsis="Un voleur qui s'infiltre dans les rêves est chargé d'implanter une idée.",
    )
    post = film_pub.to_post(settings())
    assert post is not None
    assert "✨ <b>NOUVEAU FILM</b> ✨" in post.caption
    assert "🎬 <b>Inception (2010)</b>" in post.caption
    assert "🏷️ <b>Genre :</b> Action, Science-Fiction" in post.caption
    assert "🔊 <b>Audio :</b> VF" in post.caption
    assert "📺 <b>Qualité :</b> 4K HDR" in post.caption
    assert "<blockquote>Un voleur qui s'infiltre dans les rêves est chargé d'implanter une idée.</blockquote>" in post.caption
    assert "🍿 <i>Bon visionnage sur NokaTV !</i>" in post.caption
    assert post.button_text == "🍿 Voir le film"

    # Anime episode
    anime_pub = Publication(
        category=CATEGORY_ANIMES,
        key="anime:ep",
        title="Solo Leveling",
        subtitle="Épisode 12",
        target_path="/anime/solo-leveling-vostfr",
        genres=["Action", "Fantasy"],
        version="VOSTFR",
        synopsis="Dans un monde où des portails s'ouvrent vers des donjons...",
        kind="episode",
    )
    anime_post = anime_pub.to_post(settings())
    assert anime_post is not None
    assert "✨ <b>NOUVEL ÉPISODE D’ANIMÉ</b> ✨" in anime_post.caption
    assert "🎌 <b>Solo Leveling</b>" in anime_post.caption
    assert "📍 <b>Épisode 12</b>" in anime_post.caption
    assert "🏷️ <b>Genre :</b> Action, Fantasy" in anime_post.caption
    assert "🔊 <b>Audio :</b> VOSTFR" in anime_post.caption
    assert "📺 <b>Qualité :</b> HD" in anime_post.caption
    assert "<blockquote>Dans un monde où des portails s'ouvrent vers des donjons...</blockquote>" in anime_post.caption
    assert anime_post.button_text == "⚡ Regarder l'épisode"

    # Une URL externe ne peut pas devenir le bouton d'un post, même si elle
    # arrivait d'une source malformée.
    external_target = Publication(
        category=CATEGORY_FILMS,
        key="bad-target",
        title="Test",
        target_path="https://source.example/embed/123",
    )
    assert external_target.to_post(settings()) is None

    # Seules les affiches HTTPS publiques ou les assets NokaTV sont transmis à
    # Telegram : une URL HTTP externe ne devient pas un téléchargement distant.
    http_image = make_publication(key="http-image", image="http://source.example/poster.jpg")
    assert http_image.to_post(settings()).image_url == ""

    # Les données de source ne peuvent ni faire sortir le bouton de la fiche,
    # ni faire dépasser la limite de caption Telegram après échappement HTML.
    malformed_path = Publication(
        category=CATEGORY_FILMS,
        key="bad-path",
        title="Test",
        target_path="/film/titre-vf?iframe=https://source.example/embed",
    )
    assert malformed_path.to_post(settings()) is None
    assert Publication(
        category=CATEGORY_FILMS,
        key="wrong-route",
        title="Test",
        target_path="/anime/titre-vostfr",
    ).to_post(settings()) is None
    long_caption = Publication(
        category=CATEGORY_FILMS,
        key="long-caption",
        title="&" * 500,
        target_path="/film/titre-vf",
        subtitle="&" * 500,
        version="&" * 500,
    ).to_post(settings()).caption
    assert len(long_caption) < 1024


def test_mode_hybride_est_le_collecteur_par_defaut_et_complete_reste_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_DISCOVERY_MODE", raising=False)
    assert TelegramSettings.from_environment().discovery_mode == "hybrid"
    monkeypatch.setenv("TELEGRAM_DISCOVERY_MODE", "complete")
    assert TelegramSettings.from_environment().discovery_mode == "complete"
    monkeypatch.setenv("TELEGRAM_DISCOVERY_MODE", "unexpected")
    assert TelegramSettings.from_environment().discovery_mode == "hybrid"

    hybrid = TelegramPublisher(settings(), store=TelegramPublicationStore(tmp_path / "hybrid.db"))
    complete = TelegramPublisher(
        settings(discovery_mode="complete"), store=TelegramPublicationStore(tmp_path / "complete.db")
    )
    assert hybrid.collector is telegram_publisher.collect_default_publications
    assert complete.collector is telegram_publisher.collect_complete_publications


def test_discovery_batch_preserve_la_position_historique_des_erreurs():
    batch = DiscoveryBatch({}, {CATEGORY_FILMS: ["erreur existante"]})
    assert batch.errors == {CATEGORY_FILMS: ["erreur existante"]}
    assert batch.baseline_publications[CATEGORY_FILMS] == []


def test_store_cree_une_baseline_puis_ne_met_en_file_que_les_nouveautes(tmp_path):
    store = TelegramPublicationStore(tmp_path / "telegram.db", lease_seconds=60)
    first = make_post(key="movie:one")
    second = make_post(key="movie:two")

    empty = store.register_discoveries(CATEGORY_FILMS, [], timestamp=1)
    assert empty.baseline_created is False
    assert store.is_baselined(CATEGORY_FILMS) is False

    baseline = store.register_discoveries(CATEGORY_FILMS, [first], timestamp=10)
    assert baseline.baseline_created is True
    assert baseline.baseline_count == 1
    assert store.claim_due([CATEGORY_FILMS], timestamp=11) == []

    registration = store.register_discoveries(CATEGORY_FILMS, [first, second], timestamp=12)
    assert registration.baseline_created is False
    assert registration.queued_count == 1

    claimed = store.claim_due([CATEGORY_FILMS], timestamp=13)
    assert len(claimed) == 1
    assert claimed[0].key == "movie:two"
    assert claimed[0].attempts == 1

    assert store.mark_sent(claimed[0], 42, timestamp=14) is True
    row = store.state_for(CATEGORY_FILMS, "movie:two")
    assert row is not None
    assert row["state"] == "sent"
    assert row["telegram_message_id"] == "42"
    assert store.claim_due([CATEGORY_FILMS], timestamp=15) == []


def test_store_persiste_et_reserve_les_retries_de_collecte_source(tmp_path):
    store = TelegramPublicationStore(tmp_path / "telegram.db", lease_seconds=60)
    store.schedule_discovery_retry(["Liste source indisponible"], timestamp=10)
    assert store.discovery_retry_due_at() == 310
    assert store.claim_discovery_retry(timestamp=309) is None

    token = store.claim_discovery_retry(timestamp=310, lease_seconds=60)
    assert token
    assert store.claim_discovery_retry(timestamp=310, lease_seconds=60) is None
    # Le lease de collecte ne peut être plus court qu'une heure, même si celui
    # des messages Telegram est volontairement petit dans ce test.
    assert store.discovery_retry_due_at() == 3910
    assert store.complete_discovery_retry(token, ["Toujours indisponible"], timestamp=311) is True
    assert store.discovery_retry_due_at() == 911

    next_token = store.claim_discovery_retry(timestamp=911, lease_seconds=60)
    assert next_token
    assert store.complete_discovery_retry(next_token, [], timestamp=912) is True
    assert store.discovery_retry_due_at() is None


def test_deux_stores_ne_reservent_pas_le_meme_post(tmp_path):
    database = tmp_path / "telegram.db"
    first_store = TelegramPublicationStore(database, lease_seconds=60)
    second_store = TelegramPublicationStore(database, lease_seconds=60)
    baseline = make_post(key="multi:baseline")
    new_post = make_post(key="multi:new")
    first_store.register_discoveries(CATEGORY_FILMS, [baseline], timestamp=1)
    first_store.register_discoveries(CATEGORY_FILMS, [baseline, new_post], timestamp=2)

    first_claim = first_store.claim_due([CATEGORY_FILMS], timestamp=3, limit=1)
    second_claim = second_store.claim_due([CATEGORY_FILMS], timestamp=3, limit=1)
    assert [post.key for post in first_claim] == ["multi:new"]
    assert second_claim == []

    # Après une vraie expiration de lease, un autre processus peut reprendre
    # l'élément ; l'ancien token ne peut alors plus le confirmer comme envoyé.
    recovered = second_store.claim_due([CATEGORY_FILMS], timestamp=64, limit=1)
    assert [post.key for post in recovered] == ["multi:new"]
    assert first_store.mark_sent(first_claim[0], 1, timestamp=65) is False


def test_store_reessaie_une_publication_apres_echec_et_ne_perd_pas_son_lease(tmp_path):
    store = TelegramPublicationStore(tmp_path / "telegram.db", lease_seconds=60)
    claimed = claim_one(store, make_post(key="retry:old"), timestamp=100)

    assert store.mark_retry(
        claimed,
        TelegramPublishError("erreur temporaire"),
        retry_base_seconds=10,
        timestamp=103,
    ) is True
    row = store.state_for(CATEGORY_FILMS, claimed.key)
    assert row is not None
    assert row["state"] == "retry"
    assert row["next_attempt_at"] == 113
    assert store.next_due_at([CATEGORY_FILMS]) == 113
    assert store.claim_due([CATEGORY_FILMS], timestamp=112) == []

    retried = store.claim_due([CATEGORY_FILMS], timestamp=113)
    assert len(retried) == 1
    assert retried[0].key == claimed.key
    assert retried[0].attempts == 2
    # Un worker obsolète ne peut plus marquer le message envoyé avec son ancien
    # lease token après qu'un nouveau worker l'a récupéré.
    assert store.mark_sent(claimed, 99, timestamp=114) is False
    assert store.mark_sent(retried[0], 100, timestamp=114) is True


def test_publisher_baseline_deduplication_et_envoi_des_nouveautes(tmp_path):
    async def scenario():
        store = TelegramPublicationStore(tmp_path / "telegram.db")
        first = make_publication(key="film:initial")
        second = make_publication(key="film:new")
        batches = [
            DiscoveryBatch(publications={CATEGORY_FILMS: [first]}),
            DiscoveryBatch(publications={CATEGORY_FILMS: [first, second]}),
        ]

        async def collector():
            return batches.pop(0)

        class Sender:
            def __init__(self):
                self.sent: list[tuple[str, str]] = []

            async def send(self, channel_id, post):
                self.sent.append((channel_id, post.key))
                return 123

        sender = Sender()
        publisher = TelegramPublisher(settings(), store=store, collector=collector, sender=sender)

        first_report = await publisher.run()
        assert first_report.baselined == {CATEGORY_FILMS: 1}
        assert first_report.sent == 0
        assert sender.sent == []

        second_report = await publisher.run()
        assert second_report.queued == {CATEGORY_FILMS: 1}
        assert second_report.sent == 1
        assert sender.sent == [("@nokatv_films", "film:new")]

    asyncio.run(scenario())


def test_flush_retries_rejoue_une_collecte_source_en_echec_persistante(tmp_path):
    async def scenario():
        store = TelegramPublicationStore(tmp_path / "telegram.db")
        # L'échec est déjà arrivé lors d'un --once précédent ; il est maintenant
        # dû afin que le cron --flush-retries le reprenne.
        store.schedule_discovery_retry(["Source indisponible"], timestamp=time.time() - 301)
        batches = [DiscoveryBatch(publications={CATEGORY_FILMS: [make_publication(key="source:baseline")]})]

        async def collector():
            return batches.pop(0)

        class Sender:
            async def send(self, _channel_id, _post):
                return 1

        report = await TelegramPublisher(
            settings(), store=store, collector=collector, sender=Sender()
        ).flush_due()
        assert report.baselined == {CATEGORY_FILMS: 1}
        assert batches == []
        assert store.discovery_retry_due_at() is None

    asyncio.run(scenario())


def test_mode_hybride_baseline_le_cache_mais_ne_poste_que_la_liste_source_apres(tmp_path):
    async def scenario():
        store = TelegramPublicationStore(tmp_path / "telegram.db")
        source_initial = make_publication(key="source:initial")
        source_new = make_publication(key="source:new")
        cache_initial = make_publication(key="cache:initial")
        cache_arrived_later = make_publication(key="cache:old-opened-later")
        batches = [
            DiscoveryBatch(
                publications={CATEGORY_FILMS: [source_initial]},
                baseline_publications={CATEGORY_FILMS: [cache_initial]},
            ),
            DiscoveryBatch(
                publications={CATEGORY_FILMS: [source_initial, source_new]},
                baseline_publications={CATEGORY_FILMS: [cache_initial, cache_arrived_later]},
            ),
        ]

        async def collector():
            return batches.pop(0)

        class Sender:
            def __init__(self):
                self.keys = []

            async def send(self, _channel_id, post):
                self.keys.append(post.key)
                return 12

        sender = Sender()
        publisher = TelegramPublisher(settings(), store=store, collector=collector, sender=sender)
        first = await publisher.run()
        assert first.baselined == {CATEGORY_FILMS: 2}
        second = await publisher.run()
        assert second.queued == {CATEGORY_FILMS: 1}
        assert sender.keys == ["source:new"]
        assert store.state_for(CATEGORY_FILMS, "cache:old-opened-later") is None

    asyncio.run(scenario())


def test_snapshot_cache_et_fiches_fraiches_evitent_des_requetes_detail():
    now = time.time()

    class CacheBackend:
        values: ClassVar[dict[str, dict]] = {
            "home:movies:1": {
                "data": {"items": [{"slug": "film-local-vf", "title": "Film local"}]},
                "expires": now + 60,
                "updated_at": now,
            },
            "list:movies:animation:1": {
                "data": {"items": [{"slug": "anime-film-vf", "title": "Film animé"}]},
                "expires": now + 60,
                "updated_at": now,
            },
            "detail:serie-locale-vf": {
                "data": {
                    "slug": "serie-locale-vf",
                    "type": "series",
                    "movie_id": "20",
                    "title": "Série locale",
                    "episodes": [{"episode_id": "201", "season": "1", "number": "1", "title": "Épisode 1"}],
                },
                "expires": now + 60,
                "updated_at": now,
            },
            "detail:anime:anime-local-vostfr": {
                "data": {
                    "slug": "anime-local-vostfr",
                    "title": "Animé local",
                    "episodes": [{"episode_id": "anime-local-01-vostfr", "number": "1", "title": "Épisode 1"}],
                },
                "expires": now + 60,
                "updated_at": now,
            },
        }

        def get_keys_by_prefix(self, prefix):
            return [key for key in self.values if key.startswith(prefix)]

        def get_stale(self, key):
            return self.values[key]

    snapshot = _cached_snapshot(CacheBackend())
    assert [item["slug"] for item in snapshot.movies] == ["film-local-vf"]
    assert [item["slug"] for item in snapshot.animation_movies] == ["anime-film-vf"]
    assert set(snapshot.series_details) == {"serie-locale"}
    assert set(snapshot.anime_details) == {"anime-local"}

    series, missing_series = _split_fresh_cached_series(
        [{"slug": "serie-locale-vostfr", "title": "Série locale"}], snapshot.series_details
    )
    assert [post.key for post in series] == ["coflix:series:serie-locale:s1:e1"]
    assert missing_series == []

    animes, missing_animes = _split_fresh_cached_animes(
        [{"slug": "anime-local-vf", "title": "Animé local"}], snapshot.anime_details
    )
    assert [post.key for post in animes] == ["voiranime:anime:anime-local:anime-local-01"]
    assert missing_animes == []


def test_fiche_cache_expiree_secourt_un_echec_detail_source_sans_masquer_l_erreur():
    async def scenario():
        expired = time.time() - 1
        series_cache = {
            "serie-locale": CachedRecord(
                "detail:serie-locale-vf",
                {
                    "slug": "serie-locale-vf",
                    "type": "series",
                    "movie_id": "20",
                    "title": "Série locale",
                    "episodes": [{"episode_id": "201", "season": "1", "number": "1", "title": "Épisode 1"}],
                },
                expired,
                expired - 60,
            )
        }
        anime_cache = {
            "anime-local": CachedRecord(
                "detail:anime:anime-local-vostfr",
                {
                    "slug": "anime-local-vostfr",
                    "title": "Animé local",
                    "episodes": [{"episode_id": "anime-local-01-vostfr", "number": "1", "title": "Épisode 1"}],
                },
                expired,
                expired - 60,
            )
        }

        async def unavailable(*_args, **_kwargs):
            raise RuntimeError("source indisponible")

        series, series_errors = await _episode_publications_from_coflix(
            [{"slug": "serie-locale-vf", "title": "Série locale"}],
            coflix_get_html=unavailable,
            coflix_get_json=unavailable,
            parse_detail=lambda *_args: {},
            parse_episodes=lambda _payload: [],
            fallback_details=series_cache,
        )
        animes, anime_errors = await _episode_publications_from_animes(
            [{"slug": "anime-local-vf", "title": "Animé local"}],
            voiranime_get_html=unavailable,
            parse_detail=lambda *_args: {},
            fallback_details=anime_cache,
        )
        assert [post.key for post in series] == ["coflix:series:serie-locale:s1:e1"]
        assert [post.key for post in animes] == ["voiranime:anime:anime-local:anime-local-01"]
        assert "cache expiré utilisé" in series_errors[0]
        assert "cache expiré utilisé" in anime_errors[0]

    asyncio.run(scenario())


def test_publisher_masque_le_token_dans_les_erreurs_persistantes(tmp_path):
    async def scenario():
        store = TelegramPublicationStore(tmp_path / "telegram.db")
        first = make_publication(key="secret:initial")
        second = make_publication(key="secret:new")
        batches = [
            DiscoveryBatch(publications={CATEGORY_FILMS: [first]}),
            DiscoveryBatch(publications={CATEGORY_FILMS: [first, second]}),
        ]

        async def collector():
            return batches.pop(0)

        class Sender:
            async def send(self, _channel_id, _post):
                raise RuntimeError("request failed for test-token-never-real")

        publisher = TelegramPublisher(settings(), store=store, collector=collector, sender=Sender())
        await publisher.run()
        report = await publisher.run()
        row = store.state_for(CATEGORY_FILMS, "secret:new")
        assert row is not None
        assert "test-token-never-real" not in row["last_error"]
        assert all("test-token-never-real" not in error for error in report.errors)
        assert "[secret masqué]" in row["last_error"]

    asyncio.run(scenario())


def test_publisher_ne_baseline_pas_un_flux_episode_partiel(tmp_path):
    async def scenario():
        store = TelegramPublicationStore(tmp_path / "telegram.db")
        batch = DiscoveryBatch(
            publications={CATEGORY_SERIES: [make_publication(CATEGORY_SERIES, "series:ep", kind="episode")]},
            errors={CATEGORY_SERIES: ["une fiche source est indisponible"]},
        )

        async def collector():
            return batch

        publisher = TelegramPublisher(
            settings(),
            store=store,
            collector=collector,
        )
        report = await publisher.run()
        assert CATEGORY_SERIES in report.skipped
        assert store.is_baselined(CATEGORY_SERIES) is False
        # La panne source est aussi persistée : le cron --flush-retries pourra
        # rejouer la collecte après le backoff, plutôt que d'attendre midi.
        assert store.discovery_retry_due_at() is not None
        assert store.discovery_retry_due_at() > time.time()

    asyncio.run(scenario())


def test_client_telegram_envoie_photo_caption_bouton_sans_reseau_reel(tmp_path):
    async def scenario():
        store = TelegramPublicationStore(tmp_path / "telegram.db")
        claimed = claim_one(store, make_post(key="client:photo"), timestamp=1)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        bot = TelegramBotClient(settings(), client=client)
        message_id = await bot.send("@nokatv_films", claimed)
        await client.aclose()

        assert message_id == 7
        assert len(requests) == 1
        assert requests[0].url.path.endswith("/sendPhoto")
        data = parse_qs(requests[0].content.decode())
        assert data["chat_id"] == ["@nokatv_films"]
        assert data["caption"] == [claimed.caption]
        assert "text" not in data
        assert data["photo"] == [claimed.image_url]
        assert "https://nokatv.example/film/titre-vf" in data["reply_markup"][0]

    asyncio.run(scenario())


def test_client_telegram_retombe_sur_texte_si_telegram_refuse_seulement_l_affiche(tmp_path):
    async def scenario():
        store = TelegramPublicationStore(tmp_path / "telegram.db")
        claimed = claim_one(store, make_post(key="client:fallback"), timestamp=1)
        methods: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            methods.append(request.url.path.rsplit("/", 1)[-1])
            if methods[-1] == "sendPhoto":
                return httpx.Response(
                    400,
                    json={"ok": False, "error_code": 400, "description": "Bad Request: failed to get HTTP URL content"},
                )
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 8}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        bot = TelegramBotClient(settings(), client=client)
        message_id = await bot.send("@nokatv_films", claimed)
        await client.aclose()

        assert message_id == 8
        assert methods == ["sendPhoto", "sendMessage"]

    asyncio.run(scenario())


def test_store_reserve_un_message_a_la_fois_et_renouvelle_le_lease(tmp_path):
    store = TelegramPublicationStore(tmp_path / "telegram.db", lease_seconds=60)
    baseline = make_post(key="lease:baseline")
    first = make_post(key="lease:first")
    second = make_post(key="lease:second")
    store.register_discoveries(CATEGORY_FILMS, [baseline], timestamp=1)
    store.register_discoveries(CATEGORY_FILMS, [baseline, first, second], timestamp=2)

    claimed = store.claim_due([CATEGORY_FILMS], timestamp=3, limit=1)
    assert [post.key for post in claimed] == ["lease:first"]
    assert store.renew_lease(claimed[0], timestamp=10) is True
    state = store.state_for(CATEGORY_FILMS, "lease:first")
    assert state is not None
    assert state["lease_until"] == 70

    # Le second message reste disponible pour une réservation distincte : le
    # publisher ne laisse pas un gros lot expirer pendant les envois lents.
    next_claimed = store.claim_due([CATEGORY_FILMS], timestamp=11, limit=1)
    assert [post.key for post in next_claimed] == ["lease:second"]


def test_client_telegram_respecte_exactement_retry_after(tmp_path):
    async def scenario():
        store = TelegramPublicationStore(tmp_path / "telegram.db")
        claimed = claim_one(store, make_post(key="client:rate-limit"), timestamp=1)
        calls = 0
        delays: list[float] = []

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(
                    429,
                    json={"ok": False, "error_code": 429, "parameters": {"retry_after": 123}},
                )
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

        async def fake_sleep(delay: float) -> None:
            delays.append(delay)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        bot = TelegramBotClient(settings(), client=client, sleep=fake_sleep)
        assert await bot.send("@nokatv_films", claimed) == 9
        await client.aclose()
        assert calls == 2
        assert delays == [123]

    asyncio.run(scenario())


def test_collecte_paginee_parcourt_toutes_les_pages_et_signale_les_echecs():
    async def scenario():
        visited: list[str] = []

        async def fetch(path):
            visited.append(path)
            if path.endswith("page=5"):
                raise RuntimeError("source momentanément indisponible")
            return path

        def parse(html):
            page = 1 if html == "/catalogue/" else int(html.rsplit("=", 1)[1])
            return [{"slug": f"titre-{page}"}]

        items, errors = await _collect_paginated_items(
            fetch_html=fetch,
            parse_list=parse,
            get_last_page=lambda _html: 7,
            first_path="/catalogue/",
            page_path=lambda page: f"/catalogue/?page={page}",
            source_label="Catalogue test",
        )
        # Pas de plafond arbitraire : la dernière page déclarée est sollicitée.
        assert set(visited) == {"/catalogue/", *[f"/catalogue/?page={page}" for page in range(2, 8)]}
        assert {item["slug"] for item in items} == {"titre-1", "titre-2", "titre-3", "titre-4", "titre-6", "titre-7"}
        assert len(errors) == 1
        assert "page 5" in errors[0]

    asyncio.run(scenario())


def test_collecteur_hybride_utilise_la_page_recente_voiranime_et_non_le_catalogue(monkeypatch):
    async def scenario():
        from scraper import (
            coflix_client,
            coflix_parser,
            voiranime_client,
            voiranime_parser,
        )

        coflix_paths: list[str] = []
        anime_paths: list[str] = []

        async def fake_coflix(path, *_args, **_kwargs):
            coflix_paths.append(path)
            return ""

        async def fake_anime(path, *_args, **_kwargs):
            anime_paths.append(path)
            return ""

        monkeypatch.setattr(telegram_publisher, "_cached_snapshot", lambda: telegram_publisher.CachedSnapshot())
        monkeypatch.setattr(coflix_client, "coflix_get_html", fake_coflix)
        monkeypatch.setattr(coflix_parser, "parse_coflix_list", lambda _html, _kind: [])
        monkeypatch.setattr(voiranime_client, "voiranime_get_html", fake_anime)
        monkeypatch.setattr(voiranime_parser, "parse_voiranime_list", lambda _html: [])

        batch = await telegram_publisher.collect_default_publications()
        assert batch.errors == {}
        assert set(coflix_paths) == {"/movies/", "/movies/animation/", "/series/"}
        assert anime_paths == ["/"]

    asyncio.run(scenario())


def test_collecteurs_creent_des_posts_episode_et_classement_films_sans_lecteur():
    async def scenario():
        async def coflix_html(path):
            assert path == "/film/serie-test-vf"
            return "detail"

        async def coflix_json(path, params):
            assert path == "/ajax/episode/list-episode"
            assert params == {"movieId": "42"}
            return {"html": "episodes"}

        def coflix_detail(html, slug):
            assert html == "detail" and slug == "serie-test-vf"
            return {
                "type": "series",
                "movie_id": "42",
                "title": "Série Test",
                "image": "/static/serie.jpg",
                "version": "VF",
            }

        def coflix_episodes(payload):
            assert payload == {"html": "episodes"}
            return [{"episode_id": "900", "season": "1", "number": "2", "title": "Épisode 2"}]

        series, series_errors = await _episode_publications_from_coflix(
            [{"slug": "serie-test-vf", "title": "Série Test", "image": "/static/card.jpg", "version": "VF"}],
            coflix_get_html=coflix_html,
            coflix_get_json=coflix_json,
            parse_detail=coflix_detail,
            parse_episodes=coflix_episodes,
        )
        assert series_errors == []
        assert len(series) == 1
        assert series[0].category == CATEGORY_SERIES
        assert series[0].key == "coflix:series:serie-test:s1:e2"
        assert series[0].target_path == "/film/serie-test-vf"
        assert series[0].kind == "episode"

        async def anime_html(path):
            assert path == "/anime/anime-test-vostfr/"
            return "anime-detail"

        def anime_detail(html, slug):
            assert html == "anime-detail" and slug == "anime-test-vostfr"
            return {
                "title": "Animé Test",
                "image": "/api/image-proxy?url=https%3A%2F%2Fposter.example%2Fa.jpg",
                "version": "VOSTFR",
                "episodes": [{"episode_id": "anime-test-03-vostfr", "number": "3", "title": "Épisode 3", "version": "VOSTFR"}],
            }

        animes, anime_errors = await _episode_publications_from_animes(
            [{"slug": "anime-test-vostfr", "title": "Animé Test", "image": "", "version": "VOSTFR"}],
            voiranime_get_html=anime_html,
            parse_detail=anime_detail,
        )
        assert anime_errors == []
        assert len(animes) == 1
        assert animes[0].category == CATEGORY_ANIMES
        assert animes[0].key == "voiranime:anime:anime-test:anime-test-03"
        assert animes[0].target_path == "/anime/anime-test-vostfr"

        movies = _movie_publications(
            [{"slug": "film-animation-vf", "title": "Film animé", "image": "", "version": "VF"}],
            "animation",
        )
        assert movies[0].key == "coflix:animation:film-animation"
        assert movies[0].target_path == "/film/film-animation-vf"

    asyncio.run(scenario())


def test_planificateur_attend_le_prochain_midi_africa_lagos():
    tz = ZoneInfo("Africa/Lagos")
    disabled = TelegramSettings(enabled=False, bot_token="", site_url="https://nokatv.example", channels={})
    assert seconds_until_next_run(
        disabled, now=datetime(2026, 8, 28, 11, 59, 30, tzinfo=tz)
    ) == 30
    # À midi exact, le prochain passage est celui du lendemain, pas un doublon
    # déclenché au redémarrage du conteneur.
    assert seconds_until_next_run(
        disabled, now=datetime(2026, 8, 28, 12, 0, 0, tzinfo=tz)
    ) == 24 * 60 * 60


def test_configuration_refuse_un_envoi_active_sans_token():
    invalid = TelegramSettings(
        enabled=True,
        bot_token="",
        site_url="https://nokatv.example",
        channels={CATEGORY_FILMS: "@nokatv_films"},
    )
    with pytest.raises(TelegramConfigurationError, match="TELEGRAM_BOT_TOKEN"):
        invalid.validate_for_publish()


def test_configuration_exige_les_quatre_canaux_lorsque_la_publication_est_active():
    incomplete = TelegramSettings(
        enabled=True,
        bot_token="test-token-never-real",
        site_url="https://nokatv.example",
        channels={CATEGORY_FILMS: "@nokatv_films"},
    )
    with pytest.raises(TelegramConfigurationError, match="TELEGRAM_CHANNEL_SERIES"):
        incomplete.validate_for_publish()


def test_client_telegram_telecharge_image_proxiee_et_envoie_multipart(tmp_path):
    async def scenario():
        store = TelegramPublicationStore(tmp_path / "telegram.db")
        proxied_post = make_post(
            category=CATEGORY_ANIMES,
            key="anime:multipart-test",
            image="/api/image-proxy?url=https%3A%2F%2Fvoir-anime.to%2Fwp-content%2Fuploads%2Ftest.jpg",
            kind="episode",
        )
        claimed = claim_one(store, proxied_post, timestamp=1)

        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if "voir-anime.to" in str(request.url):
                assert request.headers.get("referer") == "https://voir-anime.to/"
                return httpx.Response(200, content=b"\xff\xd8\xff\xe0" + b"fake-jpeg-data", headers={"content-type": "image/jpeg"})
            if request.url.path.endswith("/sendPhoto"):
                return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
            return httpx.Response(404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        bot = TelegramBotClient(settings(), client=client)
        message_id = await bot.send("@nokatv_manga", claimed)
        await client.aclose()

        assert message_id == 42
        assert len(requests) == 2
        # Première requête : téléchargement de l'image avec Referer
        assert "voir-anime.to" in str(requests[0].url)
        # Deuxième requête : sendPhoto en multipart
        assert requests[1].url.path.endswith("/sendPhoto")
        assert "multipart/form-data" in requests[1].headers.get("content-type", "")

    asyncio.run(scenario())


def test_store_queue_baseline_items_et_reset_category(tmp_path):
    store = TelegramPublicationStore(tmp_path / "telegram.db")
    post1 = make_post(category=CATEGORY_ANIMATION, key="anim:one")
    post2 = make_post(category=CATEGORY_FILMS, key="film:one")

    store.register_discoveries(CATEGORY_ANIMATION, [post1], timestamp=1)
    store.register_discoveries(CATEGORY_FILMS, [post2], timestamp=1)

    assert store.is_baselined(CATEGORY_ANIMATION) is True
    assert store.is_baselined(CATEGORY_FILMS) is True

    # Pas de messages dus en baseline initiale
    assert store.claim_due([CATEGORY_ANIMATION], timestamp=2) == []

    # Queue uniquement les films d'animation
    queued = store.queue_baseline_items(CATEGORY_ANIMATION)
    assert queued == 1

    claimed = store.claim_due([CATEGORY_ANIMATION], timestamp=3)
    assert len(claimed) == 1
    assert claimed[0].key == "anim:one"

    # Réinitialisation de la catégorie
    store.reset_category(CATEGORY_ANIMATION)
    assert store.is_baselined(CATEGORY_ANIMATION) is False
    assert store.is_baselined(CATEGORY_FILMS) is True
    assert store.state_for(CATEGORY_ANIMATION, "anim:one") is None


def test_cli_publish_baseline_et_reset_baseline(tmp_path, monkeypatch):
    async def scenario():
        from scripts.publish_telegram import run_publish_baseline, run_reset_baseline

        database_path = tmp_path / "telegram.db"
        store = TelegramPublicationStore(database_path)
        post = make_post(category=CATEGORY_ANIMATION, key="anim:cli-test")
        store.register_discoveries(CATEGORY_ANIMATION, [post], timestamp=1)
        assert store.is_baselined(CATEGORY_ANIMATION) is True

        class MockSender:
            def __init__(self):
                self.sent: list[tuple[str, str]] = []

            async def send(self, channel_id, post):
                self.sent.append((channel_id, post.key))
                return 99

        sender = MockSender()
        monkeypatch.setattr(
            "scripts.publish_telegram.TelegramPublicationStore",
            lambda *args, **kwargs: store,
        )

        test_settings = settings()
        publisher = TelegramPublisher(test_settings, store=store, sender=sender)
        monkeypatch.setattr(
            "scripts.publish_telegram.TelegramPublisher",
            lambda *args, **kwargs: publisher,
        )

        exit_code = await run_publish_baseline(test_settings, "animation", dry_run=False, as_json=True)
        assert exit_code == 0
        assert sender.sent == [("@nokatv_animation", "anim:cli-test")]

        reset_code = run_reset_baseline(test_settings, "animation")
        assert reset_code == 0
        assert store.is_baselined(CATEGORY_ANIMATION) is False

    asyncio.run(scenario())
