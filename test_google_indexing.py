"""Tests pour le service Google Indexing API."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
from services import google_indexing
from services.google_indexing import publish_url_to_google, publish_urls_to_google


def test_invalid_url_rejected():
    ok, msg = publish_url_to_google("not-a-valid-url")
    assert ok is False
    assert "URL invalide" in msg


def test_missing_credentials(monkeypatch):
    monkeypatch.setattr(google_indexing, "CREDENTIALS_PATH", "/tmp/non-existent-sa.json")
    google_indexing._token_cache["token"] = ""
    google_indexing._token_cache["expires_at"] = 0.0

    ok, msg = publish_url_to_google("https://nokatv.xyz/film/test")
    assert ok is False
    assert "Impossible de s'authentifier" in msg


def test_successful_publish(monkeypatch):
    monkeypatch.setattr(google_indexing, "_get_access_token", lambda: "fake_token_123")

    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=fake_resp):
        ok, msg = publish_url_to_google("https://nokatv.xyz/film/lodyssee-vf")
        assert ok is True
        assert "Google notifié avec succès" in msg


def test_publish_urls_batch(monkeypatch):
    monkeypatch.setattr(google_indexing, "publish_url_to_google", lambda u, action="URL_UPDATED": (True, f"OK {u}"))
    urls = ["https://nokatv.xyz/film/a", "https://nokatv.xyz/film/b"]
    results = publish_urls_to_google(urls)
    assert len(results) == 2
    assert results[0][1] is True
    assert results[1][1] is True
