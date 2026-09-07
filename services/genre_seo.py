"""SEO dédié aux pages filtrées par genre (goldmine vue dans Pages.csv).

Pages.csv montre que /animes?genre=chinese&version=vostfr rank pos 8.13 avec 135 imp
mais CTR 6% car title générique. Ce module fournit titres/descriptions uniques par genre.
"""

GENRE_LABELS = {
    # Animes
    "chinese": "Donghua",
    "ecchi": "Ecchi",
    "mahou-shoujo": "Mahou Shoujo",
    "action": "Action",
    "aventure": "Aventure",
    "comédie": "Comédie",
    "comedie": "Comédie",
    "drame": "Drame",
    "fantasy": "Fantasy",
    "romance": "Romance",
    "sci-fi": "Sci-Fi",
    "science-fiction": "Science-Fiction",
    "thriller": "Thriller",
    "horror": "Horreur",
    "mystère": "Mystère",
    "mystere": "Mystère",
    "supernatural": "Surnaturel",
    "sports": "Sport",
    "mecha": "Mecha",
    "music": "Musique",
    "psychological": "Psychologique",
    "slice-of-life": "Slice of Life",
    # Films/Séries
    "animation": "Animation",
    "crime": "Crime",
    "famille": "Famille",
    "familial": "Familial",
    "guerre": "Guerre",
    "western": "Western",
    "documentaire": "Documentaire",
    "histoire": "Histoire",
    # Dramas
    "arts-martiaux": "Arts Martiaux",
    "judiciaire": "Judiciaire",
    "militaire": "Militaire",
    "médical": "Médical",
    "medical": "Médical",
    "wuxia": "Wuxia",
    "xianxia": "Xianxia",
    "jeunesse": "Jeunesse",
    "amitié": "Amitié",
    "amitie": "Amitié",
    "contexte-scolaire": "Scolaire",
    "vie-quotidienne": "Vie Quotidienne",
    "tokusatsu": "Tokusatsu",
    "aventure": "Aventure",
}

GENRE_DESCRIPTIONS = {
    "chinese": "Les donghua (animés chinois) les plus populaires en VOSTFR HD. BTTH, Soul Land, Perfect World, Tales of Demons and Gods... Liste complète mise à jour quotidiennement.",
    "ecchi": "Animés ecchi en VOSTFR HD non censurés. Sélection des meilleurs titres ecchi, harem et fanservice en streaming gratuit.",
    "action": "Films et animés d'action en streaming VF/VOSTFR HD. Combats, arts martiaux, super-héros et blockbusters.",
    "romance": "Romance en streaming VF/VOSTFR. Dramas romantiques, comédies romantiques et animés shoujo.",
    "thriller": "Thrillers et polars en streaming. Suspense, enquêtes et retournements de situation.",
    "fantasy": "Fantasy et isekai en streaming VOSTFR. Mondes magiques, réincarnation et aventures épiques.",
    "horror": "Horreur et épouvante en streaming. Films et animés d'horreur VF/VOSTFR.",
}

def get_genre_label(slug: str) -> str:
    """Label lisible pour un slug genre."""
    s = (slug or "").lower().strip()
    return GENRE_LABELS.get(s, s.replace("-", " ").title())

def get_genre_seo(genre_slug: str, section: str = "films", version: str = "all") -> dict:
    """Retourne title/description/H1 optimisés pour une page genre."""
    label = get_genre_label(genre_slug)
    version_upper = (version or "all").upper()
    version_label = ""
    if version and version.lower() != "all":
        version_label = f" {version_upper}"

    # Section label
    section_labels = {
        "films": "Films",
        "series": "Séries",
        "dramas": "K-Dramas",
        "animes": "Animés",
        "animes": "Animés",
    }
    section_label = section_labels.get(section, "Films")

    # Title SEO: inclut mot-clé exact de la requête (ex: "Donghua VOSTFR")
    # Requêtes.csv montre que users cherchent "donghua vostfr", "ecchi vostfr" directement
    if genre_slug.lower() == "chinese":
        title = f"Donghua VOSTFR — Animés Chinois en Streaming Gratuit HD — NokaTV"
    elif version and version.lower() != "all":
        title = f"{label}{version_label} en Streaming Gratuit HD — {section_label} — NokaTV"
    else:
        title = f"{label} en Streaming VF/VOSTFR HD Gratuit — {section_label} — NokaTV"

    desc_base = GENRE_DESCRIPTIONS.get(genre_slug.lower(), f"Regardez les meilleurs {label} en streaming VF et VOSTFR HD gratuit sur NokaTV.")
    if version and version.lower() != "all":
        description = f"{label}{version_label} en streaming HD gratuit. {desc_base}"
    else:
        description = f"{label} en streaming VF/VOSTFR HD gratuit. {desc_base}"

    h1 = f"{label}{version_label} en Streaming"

    return {
        "label": label,
        "title": title,
        "description": description[:160],
        "h1": h1,
    }
