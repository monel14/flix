#!/usr/bin/env python3
"""Script de vérification de l'état d'avancement de la migration canonique Google.

Usage :
    python3 scripts/check_migration_status.py
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

import jwt

CREDENTIALS_PATH = os.getenv(
    "GOOGLE_APPLICATION_CREDENTIALS",
    str(Path(__file__).parent.parent / "service_account.json")
)

GSC_API_BASE = "https://www.googleapis.com/webmasters/v3"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPES = "https://www.googleapis.com/auth/webmasters.readonly"


def get_token() -> str:
    if not os.path.exists(CREDENTIALS_PATH):
        print(f"Erreur : Clé de compte de service introuvable à {CREDENTIALS_PATH}")
        sys.exit(1)

    with open(CREDENTIALS_PATH, "r", encoding="utf-8") as f:
        sa = json.load(f)

    now = int(time.time())
    payload = {
        "iss": sa["client_email"],
        "scope": SCOPES,
        "aud": TOKEN_ENDPOINT,
        "exp": now + 3600,
        "iat": now,
    }
    signed_jwt = jwt.encode(payload, sa["private_key"], algorithm="RS256")
    data = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": signed_jwt,
    }).encode("utf-8")

    req = urllib.request.Request(TOKEN_ENDPOINT, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))["access_token"]


def check_sitemap(token: str, site: str) -> dict:
    encoded_site = urllib.parse.quote(site, safe="")
    url = f"{GSC_API_BASE}/sites/{encoded_site}/sitemaps"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            sitemaps = data.get("sitemap", [])
            for sm in sitemaps:
                if "sitemap.xml" in sm.get("path", ""):
                    return sm
            return sitemaps[0] if sitemaps else {}
    except Exception as e:
        return {"error": str(e)}


def check_url_variants_distribution(token: str, site: str) -> dict[str, dict[str, int]]:
    encoded_site = urllib.parse.quote(site, safe="")
    url = f"{GSC_API_BASE}/sites/{encoded_site}/searchAnalytics/query"

    end_date = datetime.date.today().isoformat()
    start_date = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    payload = json.dumps({
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["page"],
        "rowLimit": 5000
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST"
    )
    results: dict[str, dict[str, int]] = {
        "canonical": {"pages": 0, "clicks": 0, "impressions": 0},
        "legacy_www": {"pages": 0, "clicks": 0, "impressions": 0},
        "legacy_http": {"pages": 0, "clicks": 0, "impressions": 0},
    }

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            rows = json.loads(resp.read().decode("utf-8")).get("rows", [])
            for r in rows:
                p = r["keys"][0]
                clicks = int(r.get("clicks", 0))
                impr = int(r.get("impressions", 0))

                if p.startswith("https://nokatv.xyz"):
                    results["canonical"]["pages"] += 1
                    results["canonical"]["clicks"] += clicks
                    results["canonical"]["impressions"] += impr
                elif "www.nokatv.xyz" in p:
                    results["legacy_www"]["pages"] += 1
                    results["legacy_www"]["clicks"] += clicks
                    results["legacy_www"]["impressions"] += impr
                elif p.startswith("http://"):
                    results["legacy_http"]["pages"] += 1
                    results["legacy_http"]["clicks"] += clicks
                    results["legacy_http"]["impressions"] += impr
    except Exception as exc:
        print(f"Erreur lors de l'analyse des variantes : {exc}")

    return results


def main():
    print("🔍 Analyse de l'état de la migration canonique...")
    token = get_token()
    site = "sc-domain:nokatv.xyz"

    # 1. État du Sitemap
    sm = check_sitemap(token, site)
    print("\n" + "=" * 60)
    print("1. ÉTAT DU SITEMAP DANS GOOGLE")
    print("=" * 60)
    if not sm or "error" in sm:
        print("⚠️ Impossible de lire les informations du sitemap.")
    else:
        is_pending = sm.get("isPending", False)
        submitted = sm.get("lastSubmitted", "Inconnue")
        downloaded = sm.get("lastDownloaded", "Pas encore exploré")
        errors = sm.get("errors", "0")
        warnings = sm.get("warnings", "0")

        if is_pending:
            print("⏳ STATUT : EN COURS DE TRAITEMENT (isPending = True)")
            print("   Googlebot a reçu votre sitemap et le dépile actuellement.")
        else:
            print("✅ STATUT : TERMINÉ & VALIDÉ (Réussite)")
            print("   Googlebot a fini de lire et valider votre sitemap !")

        print(f"   Dernière soumission : {submitted}")
        print(f"   Dernier téléchargement : {downloaded}")
        print(f"   Erreurs : {errors} | Avertissements : {warnings}")

    # 2. Répartition du trafic (Derniers 7 jours)
    dist = check_url_variants_distribution(token, site)
    total_impr = (
        dist["canonical"]["impressions"]
        + dist["legacy_www"]["impressions"]
        + dist["legacy_http"]["impressions"]
    )
    canon_impr = dist["canonical"]["impressions"]
    pct_consolidated = (canon_impr / total_impr * 100) if total_impr > 0 else 0.0

    print("\n" + "=" * 60)
    print("2. RÉPARTITION DES PAGES & CONSOLIDATION 301 (7 derniers jours)")
    print("=" * 60)
    print(f"• URLs propres (https://nokatv.xyz) : {dist['canonical']['pages']} pages | {dist['canonical']['clicks']} clics | {dist['canonical']['impressions']} impressions")
    print(f"• Anciennes URLs WWW (à purger)     : {dist['legacy_www']['pages']} pages | {dist['legacy_www']['clicks']} clics | {dist['legacy_www']['impressions']} impressions")
    print(f"• Anciennes URLs HTTP (à purger)    : {dist['legacy_http']['pages']} pages | {dist['legacy_http']['clicks']} clics | {dist['legacy_http']['impressions']} impressions")

    print("\n" + "-" * 60)
    print(f"📊 TAUX DE CONSOLIDATION DU TRAFIC : {pct_consolidated:.1f} %")
    bars = int(pct_consolidated // 5)
    progress_bar = "█" * bars + "░" * (20 - bars)
    print(f"[{progress_bar}] {pct_consolidated:.1f} %")
    print("-" * 60)

    if pct_consolidated >= 90 and not sm.get("isPending", False):
        print("\n🎉 MIGRATION COMPLÈTE : Google a entièrement consolidé votre trafic sur l'URL canonique propre !")
    else:
        print("\n⏳ MIGRATION EN COURS : Googlebot transfère progressivement l'autorité des anciennes pages vers https://nokatv.xyz.")
        print("   Re-lancez ce script dans 2 à 4 jours pour suivre la montée du pourcentage.")


if __name__ == "__main__":
    main()
