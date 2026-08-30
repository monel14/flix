from fastapi.testclient import TestClient

from main import app
from scraper.coflix_parser import parse_coflix_search, _extract_coflix_version
from scraper.voiranime_parser import parse_voiranime_search, _extract_anime_version
from scraper.voirdrama_parser import parse_voirdrama_search, _extract_drama_version

client = TestClient(app)


def test_coflix_version_extraction():
    assert _extract_coflix_version(slug="inception-vf", title="Inception") == "VF"
    assert _extract_coflix_version(slug="avatar-2-vostfr", title="Avatar 2") == "VOSTFR"
    assert _extract_coflix_version(slug="oppenheimer-truefrench", title="Oppenheimer") == "TRUEFRENCH"
    assert _extract_coflix_version(slug="dune-multi", title="Dune") == "MULTI"


def test_anime_version_extraction():
    assert _extract_anime_version(slug="solo-leveling-vostfr", title="Solo Leveling") == "VOSTFR"
    assert _extract_anime_version(slug="naruto-vf", title="Naruto VF") == "VF"


def test_drama_version_extraction():
    assert _extract_drama_version(slug="squid-game-vostfr", title="Squid Game") == "VOSTFR"
    assert _extract_drama_version(slug="all-of-us-are-dead-vf", title="All Of Us Are Dead VF") == "VF"


def test_search_page_renders_version_badges():
    html_sample = """
    <div class="item">
        <a class="poster" href="/film/inception-vf"><img src="/poster.jpg"></a>
        <a class="name d-title">Inception</a>
        <div class="version">VF</div>
    </div>
    """
    items = parse_coflix_search(html_sample)
    assert len(items) == 1
    assert items[0]["version"] == "VF"
    assert items[0]["slug"] == "inception-vf"
