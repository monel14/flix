#!/usr/bin/env python3
"""Script de récupération et d'affichage des statistiques Google Search Console.

Usage :
    python3 scripts/fetch_gsc_stats.py [--site-url https://nokatv.xyz/] [--days 14] [--dimension date|query|page]
    python3 scripts/fetch_gsc_stats.py --queries
    python3 scripts/fetch_gsc_stats.py --pages
"""
from __future__ import annotations

import argparse
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


def list_accessible_sites(token: str) -> list[str]:
    url = f"{GSC_API_BASE}/sites"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [s["siteUrl"] for s in data.get("siteEntry", [])]
    except urllib.error.HTTPError as exc:
        print(f"Erreur lors de la récupération des propriétés GSC : {exc.code} {exc.read().decode()}")
        return []


def query_analytics(token: str, site_url: str, dimension: str = "date", days: int = 14, limit: int = 25) -> dict:
    encoded_site = urllib.parse.quote(site_url, safe="")
    url = f"{GSC_API_BASE}/sites/{encoded_site}/searchAnalytics/query"

    end_date = datetime.date.today().isoformat()
    start_date = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()

    payload = json.dumps({
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": [dimension],
        "rowLimit": limit
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"Erreur API Search Console ({exc.code}) pour {site_url} :")
        print(exc.read().decode("utf-8", errors="ignore"))
        return {}


def main():
    parser = argparse.ArgumentParser(description="Affiche les stats Google Search Console")
    parser.add_argument("--site-url", default=os.getenv("SITE_URL", "https://nokatv.xyz/"), help="URL de la propriété")
    parser.add_argument("--days", type=int, default=14, help="Nombre de jours d'historique")
    parser.add_argument("--dimension", choices=["date", "query", "page"], default="date", help="Dimension d'analyse")
    parser.add_argument("--queries", action="store_true", help="Raccourci pour afficher les top requêtes")
    parser.add_argument("--pages", action="store_true", help="Raccourci pour afficher les top pages")
    parser.add_argument("--limit", type=int, default=20, help="Nombre de lignes max")
    args = parser.parse_args()

    dim = "query" if args.queries else ("page" if args.pages else args.dimension)

    print("🔑 Connexion à Google Search Console...")
    token = get_token()

    sites = list_accessible_sites(token)
    if not sites:
        print("\n⚠️ Aucun site accessible trouvé pour ce compte de service.")
        return

    target_site = args.site_url
    if target_site not in sites:
        alt = target_site.rstrip("/") if target_site.endswith("/") else target_site + "/"
        if alt in sites:
            target_site = alt
        elif any("nokatv" in s for s in sites):
            target_site = [s for s in sites if "nokatv" in s][0]

    dim_label = "Dates" if dim == "date" else ("Mots-clés / Requêtes" if dim == "query" else "Pages")
    print(f"📊 Propriété : {target_site} | Dimension : {dim_label} ({args.days} derniers jours)")

    data = query_analytics(token, target_site, dimension=dim, days=args.days, limit=args.limit)
    rows = data.get("rows", [])
    if not rows:
        print("Aucune donnée retournée pour cette période.")
        return

    # Tri par date si dimension=date, sinon par clics décroissants
    if dim == "date":
        rows = sorted(rows, key=lambda x: x["keys"][0])
    else:
        rows = sorted(rows, key=lambda x: x.get("clicks", 0), reverse=True)

    header_col = "Date" if dim == "date" else ("Requête" if dim == "query" else "Page")
    print(f"\n| {header_col:<45} | Clics | Impressions | CTR | Pos |")
    print("|" + "-" * 47 + "|:---:|:---:|:---:|:---:|")
    for r in rows:
        val = r["keys"][0]
        if len(val) > 45:
            val = val[:42] + "..."
        clicks = int(r.get("clicks", 0))
        impressions = int(r.get("impressions", 0))
        ctr = f"{r.get('ctr', 0) * 100:.1f}%"
        pos = f"{r.get('position', 0):.1f}"
        print(f"| {val:<45} | {clicks:>5} | {impressions:>11} | {ctr:>5} | {pos:>3} |")


if __name__ == "__main__":
    main()
