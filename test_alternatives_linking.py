"""Vérifie la couverture du maillage sans démarrer les scrapers."""
import unittest
from collections import Counter
from services.alternatives_data import (
    CATEGORY_GUIDES, get_all_sites, get_related_sites, get_sites_by_category,
)


class AlternativesLinkingTests(unittest.TestCase):
    def test_all_sites_receive_category_links(self):
        incoming = Counter()
        for site in get_all_sites():
            related = get_related_sites(site['slug'])
            expected = min(8, len(get_sites_by_category(site['category'])) - 1)
            self.assertEqual(len(related), expected)
            self.assertEqual(related, get_related_sites(site['slug']))
            self.assertEqual(len({s['slug'] for s in related}), expected)
            for peer in related:
                self.assertNotEqual(site['slug'], peer['slug'])
                self.assertEqual(site['category'], peer['category'])
                incoming[peer['slug']] += 1
            self.assertIn(site['category'], CATEGORY_GUIDES)
        for site in get_all_sites():
            expected = min(8, len(get_sites_by_category(site['category'])) - 1)
            self.assertEqual(incoming[site['slug']], expected)

    def test_invalid_slug_and_limits(self):
        self.assertEqual(get_related_sites('absent'), [])
        slug = get_all_sites()[0]['slug']
        self.assertEqual(get_related_sites(slug, 0), [])
        self.assertEqual(get_related_sites(slug, -1), [])
        self.assertLessEqual(len(get_related_sites(slug, 10000)), len(get_all_sites()) - 1)


if __name__ == '__main__':
    unittest.main()
