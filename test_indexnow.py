"""Tests IndexNow : notification immédiate des nouvelles fiches aux moteurs.

Le protocole IndexNow (relayé vers Bing, Seznam, Naver, Yandex) évite d'attendre
le prochain crawl pour les URL fraîches. Ces tests verrouillent :
- le no-op silencieux sans clé configurée (aucun réseau, aucune régression) ;
- le payload exact envoyé (host, key, keyLocation, urlList) ;
- le fichier de vérification `/{clé}.txt` servi par l'application ;
- le déclenchement automatique après chaque publication Telegram réussie.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
from services import indexnow
from services.indexnow import (
    _host_of,
    indexnow_key,
    key_location,
    submit_urls,
)

client = TestClient(app)


# ── Service (comportement sans réseau) ──────────────────────────────────────

def test_sans_cle_submit_est_un_noop_silencieux(monkeypatch):
    monkeypatch.delenv("INDEXNOW_KEY", raising=False)

    async def scenario():
        return await submit_urls(["https://nokatv.xyz/film/x"], site_url="https://nokatv.xyz")

    ok, message = asyncio.run(scenario())
    assert ok is False
    assert "clé IndexNow non configurée" in message


def test_host_et_key_location_sont_bien_formes(monkeypatch):
    monkeypatch.setenv("SITE_URL", "https://nokatv.xyz")
    assert _host_of("https://nokatv.xyz") == "nokatv.xyz"
    assert _host_of("https://nokatv.xyz/") == "nokatv.xyz"
    assert key_location("https://nokatv.xyz", "abc123") == "https://nokatv.xyz/abc123.txt"


def test_payload_envoye_respecte_le_protocole(monkeypatch):
    monkeypatch.setenv("INDEXNOW_KEY", "cle-de-test")
    captured: dict = {}

    async def fake_post(client, url, *, json=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(200, request=httpx.Request("POST", url))

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args, **kwargs):
            return None

        async def post(self, url, *, json=None, **kwargs):
            return await fake_post(self, url, json=json, **kwargs)

    monkeypatch.setattr(indexnow.httpx, "AsyncClient", FakeAsyncClient)

    async def scenario():
        return await submit_urls(
            ["https://nokatv.xyz/film/the-last-sunrise-vf"],
            site_url="https://nokatv.xyz",
        )

    ok, message = asyncio.run(scenario())
    assert ok is True
    assert captured["url"] == "https://api.indexnow.org/indexnow"
    payload = captured["json"]
    assert payload["host"] == "nokatv.xyz"
    assert payload["key"] == "cle-de-test"
    assert payload["keyLocation"] == "https://nokatv.xyz/cle-de-test.txt"
    assert payload["urlList"] == ["https://nokatv.xyz/film/the-last-sunrise-vf"]


def test_payload_filtre_les_urls_invalides(monkeypatch):
    monkeypatch.setenv("INDEXNOW_KEY", "cle")
    captured: dict = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args, **kwargs):
            return None

        async def post(self, url, *, json=None, **kwargs):
            captured["json"] = json
            return httpx.Response(202, request=httpx.Request("POST", url))

    monkeypatch.setattr(indexnow.httpx, "AsyncClient", FakeAsyncClient)

    async def scenario():
        return await submit_urls(
            ["https://nokatv.xyz/film/x", "pas-une-url", ""],
            site_url="https://nokatv.xyz",
        )

    ok, message = asyncio.run(scenario())
    assert ok is True
    assert captured["json"]["urlList"] == ["https://nokatv.xyz/film/x"]


# ── Fichier de vérification de la clé ───────────────────────────────────────

def test_fichier_cle_servi_a_la_racine(monkeypatch):
    monkeypatch.setenv("INDEXNOW_KEY", "cle-racine-42")
    r = client.get("/cle-racine-42.txt")
    assert r.status_code == 200
    assert r.text.strip() == "cle-racine-42"
    # Une clé inconnue ne révèle rien
    r2 = client.get("/cle-inconnue.txt")
    assert r2.status_code == 404


# ── Déclenchement par le publisher Telegram ─────────────────────────────────

def test_publisher_notifie_indexnow_apres_envoi_reussi(tmp_path):
    from services.telegram_publisher import (
        CATEGORY_FILMS,
        Publication,
        TelegramPublisher,
        TelegramSender,
        TelegramSettings,
        TelegramPublicationStore,
    )

    class FakeSender(TelegramSender):
        async def send(self, channel_id, post):
            return "msg-1"

    notified: list[list[str]] = []

    async def fake_submitter(urls):
        notified.append(list(urls))
        return True, "ok"

    store = TelegramPublicationStore(tmp_path / "telegram.db")
    settings = TelegramSettings(
        enabled=True,
        bot_token="test-token-never-real",
        site_url="https://nokatv.xyz",
        channels={
            CATEGORY_FILMS: "@nokatv_films",
            "series": "@nokatv_series",
            "animes": "@nokatv_manga",
            "animation": "@nokatv_animation",
        },
        request_retries=1,
        retry_base_seconds=0.01,
        lease_seconds=60,
    )
    publisher = TelegramPublisher(
        settings, store=store, sender=FakeSender(),
        indexnow_submitter=fake_submitter,
    )

    def make_publication(key: str) -> Publication:
        return Publication(
            category=CATEGORY_FILMS,
            key=key,
            title="L'Odyssée",
            target_path="/film/lodyssee-vf",
            image="/static/poster.jpg",
            subtitle="",
            version="VF",
            kind="content",
        )

    async def scenario():
        # 1er passage : crée la baseline (rien n'est envoyé).
        store.register_discoveries(
            CATEGORY_FILMS, [make_publication("film:initial").to_post(settings)]
        )
        # 2e passage : la nouveauté passe en file 'pending' -> envoi.
        store.register_discoveries(
            CATEGORY_FILMS, [make_publication("film:new").to_post(settings)]
        )
        report = await publisher.flush_due()
        return report

    report = asyncio.run(scenario())
    assert report.sent == 1
    assert notified, "IndexNow doit avoir été appelé après un envoi réussi"
    assert "https://nokatv.xyz/film/lodyssee-vf" in notified[0]
