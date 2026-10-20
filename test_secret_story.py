import os
import unittest
from unittest.mock import AsyncMock, patch

from bs4 import BeautifulSoup
from starlette.testclient import TestClient
import main
from routes import home
from services.secret_story import get_secret_story


class SecretStoryTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_page_and_domain_gate(self):
        r = self.client.get('https://nokatv.xyz/secret-story-afrique')
        self.assertEqual(r.status_code, 200)
        s = BeautifulSoup(r.text, 'html.parser')
        self.assertEqual(len(s.find_all('h1')), 1)
        self.assertIn('noindex', s.select_one('meta[name="robots"]')['content'])
        self.assertIsNone(s.find('iframe'))  # chargement au clic seulement
        self.assertIn('youtube-nocookie.com/embed/_ZqZmDeWmDI', r.text)
        for anchor in ['lecteur', 'horaires', 'candidats', 'rediffusions']:
            self.assertIsNotNone(s.find(id=anchor))
        self.assertEqual(self.client.get('https://noka-tv.xyz/secret-story-afrique').status_code, 404)
        self.assertNotIn('/secret-story-afrique', self.client.get('https://nokatv.xyz/sitemap.xml').text)

    def test_configuration_can_disable_or_replace(self):
        with patch.dict(os.environ, {'SECRET_STORY_ENABLED': 'false'}):
            self.assertEqual(self.client.get('https://nokatv.xyz/secret-story-afrique').status_code, 404)
        with patch.dict(os.environ, {'SECRET_STORY_YOUTUBE_ID': 'bad"<id'}):
            self.assertFalse(get_secret_story()['enabled'])
        with patch.dict(os.environ, {'SECRET_STORY_YOUTUBE_ID': 'abcdefghijk'}):
            self.assertIn('/embed/abcdefghijk?', get_secret_story()['embed_url'])

    def test_promotion_preserves_hero_and_has_no_iframe(self):
        original = [{'title': 'Film témoin', 'slug': 'film-temoin', 'image': '', 'version': 'VF', 'year': '2026'}]
        async def cached(key, *args):
            if key == 'home:hero': return original
            if key in ['home:movies:1', 'home:series:1', 'home:movies:animation:1']:
                return {'items': [], 'last_page': 1}
            return []
        with patch.object(home.cache, 'get_or_set', side_effect=cached):
            r = self.client.get('https://nokatv.xyz/')
            self.assertEqual(r.status_code, 200)
            s = BeautifulSoup(r.text, 'html.parser')
            self.assertEqual(len(s.select('.hero-slide')), 2)
            self.assertEqual(len(s.find_all('h1')), 1)
            self.assertIsNotNone(s.select_one('a[href="/film/film-temoin"]'))
            self.assertEqual(len(s.select('#secret-story-rail-title')), 1)
            self.assertIsNone(s.find('iframe'))
            self.assertEqual(len(original), 1)
            with patch.dict(os.environ, {'SECRET_STORY_ENABLED': 'false'}):
                off = BeautifulSoup(self.client.get('https://nokatv.xyz/').text, 'html.parser')
                self.assertEqual(len(off.select('.hero-slide')), 1)
                self.assertIsNone(off.find(id='secret-story-rail-title'))


if __name__ == '__main__':
    unittest.main()
