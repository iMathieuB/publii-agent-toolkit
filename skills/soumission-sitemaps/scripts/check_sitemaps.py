"""Valide les sitemaps d'un site avant de les soumettre à Google et Bing.

Lit robots.txt, trouve les lignes « Sitemap: », télécharge chaque sitemap (et les
sous-sitemaps d'un index), puis demande chaque URL listée comme le ferait
Googlebot, sans suivre les redirections. Une URL de sitemap doit répondre 200
directement : une redirection, une 404 ou un noindex sont des erreurs que Google
et Bing signalent ensuite dans leurs consoles.

Usage :
    python -X utf8 check_sitemaps.py https://exemple.ca
    python -X utf8 check_sitemaps.py https://exemple.ca https://exemple.ca/en/sitemap.xml

Les URL ajoutées après le site sont vérifiées même si robots.txt ne les annonce pas.
Code de sortie 0 si tout est bon, 1 sinon. Bibliothèque standard seulement.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def fetch(url: str) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with OPENER.open(req, timeout=20) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, b""
    except Exception as e:  # réseau, DNS, TLS
        return 0, {"erreur": str(e)}, b""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    site = sys.argv[1].rstrip("/")
    host = urllib.parse.urlsplit(site).netloc
    problems: list[str] = []

    status, _, body = fetch(site + "/robots.txt")
    announced = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", body.decode("utf-8", "replace")) if status == 200 else []
    print(f"robots.txt : {status}, {len(announced)} sitemap(s) annoncé(s)")
    for s in announced:
        print(f"  {s}")
    if status == 200 and re.search(r"(?im)^\s*disallow:\s*/\s*$", body.decode("utf-8", "replace")):
        problems.append("robots.txt contient « Disallow: / » : tout le site est bloqué aux robots")

    queue = list(dict.fromkeys(announced + sys.argv[2:])) or [site + "/sitemap.xml"]
    for extra in sys.argv[2:]:
        if extra not in announced:
            problems.append(f"{extra} n'est pas annoncé dans robots.txt (pas bloquant, mais Bing et Google le trouvent plus vite s'il l'est)")

    seen_maps: set[str] = set()
    urls: list[str] = []
    while queue:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        status, headers, body = fetch(sm)
        ctype = headers.get("content-type", "?")
        print(f"\n{sm}\n  {status}, {ctype}")
        if status != 200:
            problems.append(f"{sm} répond {status} {headers.get('location', headers.get('erreur', ''))}".rstrip())
            continue
        if "xml" not in ctype:
            problems.append(f"{sm} est servi en {ctype} au lieu d'un type XML")
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            problems.append(f"{sm} n'est pas du XML valide ({e})")
            continue
        if root.tag == NS + "sitemapindex":
            subs = [e.text.strip() for e in root.iter(NS + "loc") if e.text]
            print(f"  index de {len(subs)} sitemap(s)")
            queue.extend(subs)
            continue
        locs = [e.text.strip() for e in root.iter(NS + "loc") if e.text]
        print(f"  {len(locs)} URL")
        if not locs:
            problems.append(f"{sm} ne liste aucune URL")
        for u in locs:
            if urllib.parse.urlsplit(u).netloc != host:
                problems.append(f"{sm} liste {u}, qui n'est pas sur {host}")
        urls.extend(locs)

    print(f"\nURL listées : {len(urls)}")
    for u in dict.fromkeys(urls):
        status, headers, body = fetch(u)
        flags = []
        if status != 200:
            flags.append(f"{status} {headers.get('location', headers.get('erreur', ''))}".rstrip())
        if "noindex" in headers.get("x-robots-tag", "").lower():
            flags.append("X-Robots-Tag noindex")
        if re.search(rb'<meta[^>]+name=["\']robots["\'][^>]+noindex', body, re.I):
            flags.append("meta robots noindex")
        canon = re.search(rb'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', body, re.I)
        if canon and canon.group(1).decode("utf-8", "replace") != u:
            flags.append(f"canonical vers {canon.group(1).decode('utf-8', 'replace')}")
        print(f"  {'ok' if not flags else 'ÉCHEC'}  {u}" + (f"  ({'; '.join(flags)})" if flags else ""))
        if flags:
            problems.append(f"{u} : {'; '.join(flags)}")

    if problems:
        print("\nÀ corriger avant de soumettre :")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nTout est bon : les sitemaps peuvent être soumis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
