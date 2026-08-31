"""Tests IndexNow essentiels : no-op sûr sans clé + payload conforme au protocole.

Le protocole IndexNow (relayé vers Bing, Seznam, Naver, Yandex) évite d'attendre
le prochain crawl pour les URL fraîches. Tests verrouillés ici :

1. Sans clé configurée, le service est un no-op silencieux (aucun réseau,
   aucune régression — comportement sûr pour les tests et envs sans config).
2. Avec clé, le POST respecte le protocole : endpoint, host, key, keyLocation,
   urlList.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from services import indexnow
from services.indexnow import submit_urls


def test_sans_cle_submit_est_un_noop_silencieux(monkeypatch):
    monkeypatch.delenv("INDEXNOW_KEY", raising=False)
    monkeypatch.setattr(indexnow, "KEY_FILE", Path("/tmp/does-not-exist-indexnow-key.txt"))

    async def scenario():
        return await submit_urls(["https://nokatv.xyz/film/x"], site_url="https://nokatv.xyz")

    ok, message = asyncio.run(scenario())
    assert ok is False
    assert "clé IndexNow non configurée" in message


def test_payload_envoye_respecte_le_protocole(monkeypatch):
    monkeypatch.setenv("INDEXNOW_KEY", "cle-de-test")
    captured: dict = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args, **kwargs):
            return None

        async def post(self, url, *, json=None, **kwargs):
            captured["url"] = url
            captured["json"] = json
            return httpx.Response(200, request=httpx.Request("POST", url))

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
