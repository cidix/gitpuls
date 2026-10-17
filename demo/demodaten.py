"""Demodatensatz für die Screens der Landingpage gitpuls.com (09.10.2026), aufgebaut auf proben/echtdaten.py: ein erfundenes Konto
fernwerk-studio mit 14 Repos, fest gegen Donnerstag, 15.10.2026, 22:40 in Europe/Zurich gerechnet. Schreibt gitpuls.db, server.json, deploy.json
und umgebung.json (die Variablen, mit denen app/web.py den Datensatz liest) in den Zielordner; die Live-Datenbank und der VPS werden
nie berührt. Zwei Szenen über denselben Commits: «busy» mit je einem Befund jeder Art, «ruhig» mit allen Befunden erledigt.

    python3 demo/demodaten.py <zielordner> [--szene busy|ruhig]

Nichts zeigt auf das Konto des Autors, Kunden oder echte Domains: Adressen und Dienstlinks liegen unter .example (RFC 2606), der Hostname ist erfunden,
ein Anbieter wird nicht genannt."""
import argparse
import hashlib
import json
import os
import random
import sqlite3
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
ZONE = ZoneInfo("Europe/Zurich")
JETZT = datetime(2026, 10, 15, 22, 40, tzinfo=ZONE)   # Donnerstag in der Mitte des Monats
KONTO = "fernwerk-studio"
ADRESSE = "dev@fernwerk-studio.example"
SZENEN = ("busy", "ruhig")
# Schreibschutz (10.10.2026, Audit B1): geschrieben wird nur in einen leeren Ordner oder einen, den dieses Skript angelegt hat (Marke), und
# gelöscht werden nur die eigenen Dateien. Vorher löschte jeder Aufruf gitpuls.db* im Zielordner, auch im Datenordner einer echten Instanz.
MARKE = ".gitpuls-demo"
EIGENE_DATEIEN = ("gitpuls.db", "gitpuls.db-journal", "server.json", "deploy.json", "umgebung.json")

# Die 14 Repos: Name, Sprache, Dateiendung der Commits, Gewicht in der Grundlast. Die letzten drei ruhen, ihr letzter Commit steht fest.
REPOS = [("atlas-web", "Astro", ".astro", 30), ("atlas-api", "Python", ".py", 15), ("ledger", "Swift", ".swift", 15),
         ("ledger-site", "Astro", ".astro", 6), ("site-template", "Astro", ".astro", 5), ("infra", "HCL", ".tf", 5),
         ("pricewatch", "Python", ".py", 6), ("watchface", "Monkey C", ".mc", 5), ("client-portal", "TypeScript", ".ts", 6),
         ("cli-tools", "Go", ".go", 3), ("notes-sync", "TypeScript", ".ts", 4),
         ("dotfiles", "Shell", ".sh", 0), ("recipes", "Python", ".py", 0), ("pi-sensor", "C++", ".cpp", 0)]
RUHIG_LETZTER = {"dotfiles": date(2026, 9, 22), "recipes": date(2026, 8, 2), "pi-sensor": date(2026, 5, 20)}
BETREFFE = {
    "atlas-web": ["Add trail difficulty filter to region pages", "Fix hero image aspect ratio on Safari", "Lazy-load map tiles below the fold",
                  "Move region copy into content collections", "Tighten card grid spacing on mobile", "Add OpenGraph images per region",
                  "Replace client-side search with Pagefind", "Prefetch region links on hover", "Fix broken anchor links in footer",
                  "Add elevation chart to stage pages", "Refactor layout into BaseLayout and RegionLayout", "Update copy for autumn routes",
                  "Cache API responses at build time", "Add sitemap entries for stage pages", "Inline critical CSS for the landing page"],
    "atlas-api": ["Add pagination to /regions endpoint", "Retry webhook delivery with exponential backoff", "Index stages by elevation gain",
                  "Fix timezone handling in sync job", "Return 404 instead of 500 for unknown slugs", "Add healthcheck for the image worker",
                  "Bump uvicorn and pin hashes", "Split image pipeline into its own worker", "Log slow queries above 200 ms"],
    "ledger": ["Add widget for monthly balance", "Fix rounding in currency conversion", "Migrate store to SwiftData schema v3",
               "Support Face ID lock on launch", "Add CSV export for transactions", "Fix crash when category is deleted",
               "Group transactions by week in list", "Add haptics to quick entry"],
    "ledger-site": ["Add App Store badge and privacy page", "Update screenshots for iOS 26", "Fix dark mode contrast in pricing table",
                    "Add changelog page"],
    "site-template": ["Add sitemap and robots.txt defaults", "Document deploy to Cloudflare Pages", "Ship a 404 page with search",
                      "Add image component with AVIF fallback"],
    "infra": ["Add firewall rule for the metrics port", "Rotate backup bucket credentials", "Move DNS records into module",
              "Pin provider versions"],
    "pricewatch": ["Parse prices from JSON-LD when present", "Skip sources that answer 429 until reset", "Add Telegram alert for drops over 10 %",
                   "Store history per source"],
    "watchface": ["Add battery complication", "Reduce redraws in low-power mode", "Fix layout on 260 px displays", "Add step goal ring"],
    "client-portal": ["Add invoice download for clients", "Fix session timeout redirect", "Show project status on dashboard",
                      "Validate upload size on the server"],
    "cli-tools": ["Add --json output to ls command", "Fix config path on Linux", "Add shell completions"],
    "notes-sync": ["Handle renamed files during sync", "Add dry-run flag", "Fix conflict copy naming"],
    "dotfiles": ["Switch prompt to starship", "Add git aliases"],
    "recipes": ["Add sourdough schedule", "Fix unit conversion for grams"],
    "pi-sensor": ["Read humidity from BME280", "Post readings every five minutes"],
}
SHARP = "Bump sharp to 0.35.5"
# Diese Woche, Montag bis Donnerstag 22:40: rund 50 Commits, 4 bis 5 Repos je Tag, Spitze am Donnerstag, atlas-web rund 55 %.
WOCHE = {0: {"atlas-web": 6, "atlas-api": 2, "ledger": 1, "infra": 1},
         1: {"atlas-web": 5, "pricewatch": 2, "watchface": 2, "cli-tools": 1, "atlas-api": 1},
         2: {"atlas-web": 5, "ledger": 2, "notes-sync": 1, "client-portal": 1},
         3: {"atlas-web": 12, "atlas-api": 3, "ledger-site": 2, "site-template": 2, "ledger": 1}}
WOCHE_ZEILEN = (7000, 1800)


def utc(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lokal(tag, stunde, minute=0):
    return datetime.combine(tag, time(stunde, minute), ZONE)


def schreibzeiten(rnd, tag, n, bis=None):
    """n Zeitpunkte eines Einzelentwicklers, der spätabends und frühmorgens arbeitet: 05:30 bis 07:59 und 20:00 bis 23:50."""
    aus = []
    while len(aus) < n:
        t = lokal(tag, 5, 30) + timedelta(minutes=rnd.randint(0, 149)) if rnd.random() < .35 else lokal(tag, 20) + timedelta(minutes=rnd.randint(0, 230))
        if bis is None or t < bis:
            aus.append(t)
    return sorted(aus)


def tageszahlen(rnd, von, bis, summe, lueckenlos=False):
    """Commits je Tag von `von` bis `bis` (beide eingeschlossen), zusammen genau `summe`; ohne `lueckenlos` bleibt rund jeder vierte Tag leer."""
    tage = [von + timedelta(days=i) for i in range((bis - von).days + 1)]
    aktiv = [d for d in tage if lueckenlos or rnd.random() > .25]
    gewicht = {d: rnd.uniform(.4, 1.6) for d in aktiv}
    zahlen = {d: (1 if lueckenlos else 0) for d in tage}
    rest = summe - sum(zahlen.values())
    for d in rnd.choices(aktiv, weights=[gewicht[d] for d in aktiv], k=rest):
        zahlen[d] += 1
    return zahlen


def plan(rnd):
    """Liste (Zeitpunkt, Repo, Betreff) über die ganze Historie ab 04.05.2026. Fenster und Ziele: diese Woche 50, Vorwoche bis Donnerstag
    22:40 rund 65 (+30 %), 30 Tage 230, die 30 Tage davor rund 160 (−30 %), Serie 15 Tage (01. bis 15.10., der 30.09. leer)."""
    heute = JETZT.date()
    montag = heute - timedelta(days=heute.weekday())
    je_tag = {}
    je_tag |= tageszahlen(rnd, date(2026, 5, 4), date(2026, 6, 7), 120)
    je_tag |= tageszahlen(rnd, date(2026, 6, 8), date(2026, 6, 28), 150, lueckenlos=True)   # Rekordserie 21 Tage
    je_tag |= tageszahlen(rnd, date(2026, 6, 29), date(2026, 7, 19), 130)
    je_tag |= tageszahlen(rnd, date(2026, 7, 20), date(2026, 7, 26), 96, lueckenlos=True)    # Rekordwoche
    je_tag |= tageszahlen(rnd, date(2026, 7, 27), date(2026, 8, 16), 150)
    je_tag |= tageszahlen(rnd, date(2026, 8, 17), date(2026, 9, 15), 161)                   # die 30 Tage vor dem 30-Tage-Fenster
    je_tag |= tageszahlen(rnd, date(2026, 9, 16), date(2026, 9, 29), 73)
    je_tag[date(2026, 9, 30)] = 0
    je_tag |= tageszahlen(rnd, date(2026, 10, 1), date(2026, 10, 4), 32, lueckenlos=True)
    je_tag[date(2026, 7, 23)] = 34                                                         # Rekordtag
    gewichte = [g for _, _, _, g in REPOS]
    namen = [r for r, _, _, _ in REPOS]
    plaene = []
    for tag, n in sorted(je_tag.items()):
        if not n:
            continue
        repos = rnd.choices(namen, weights=gewichte, k=rnd.randint(1, min(4, n)))
        for i, t in enumerate(schreibzeiten(rnd, tag, n)):
            plaene.append((t, repos[i % len(repos)]))
    # Vorwoche: Montag bis Donnerstag 22:40 genau 65, danach bis Sonntag 10 (je Tag mindestens einer, für die Serie).
    vor = montag - timedelta(days=7)
    for tag, n in tageszahlen(rnd, vor, vor + timedelta(days=3), 65, lueckenlos=True).items():
        repos = rnd.sample(namen[:11], 4)
        repos[0] = "atlas-web"
        for i, t in enumerate(schreibzeiten(rnd, tag, n, bis=lokal(vor + timedelta(days=3), 22, 40) if tag == vor + timedelta(days=3) else None)):
            plaene.append((t, repos[i % 4]))
    for tag, n in ((vor + timedelta(days=4), 4), (vor + timedelta(days=5), 3), (vor + timedelta(days=6), 3)):
        for t in schreibzeiten(rnd, tag, n):
            plaene.append((t, rnd.choice(["atlas-web", "ledger", "atlas-api"])))
    # Die ruhenden Repos: ihr letzter Commit am festen Tag, davor ein paar ältere.
    for repo, letzter in RUHIG_LETZTER.items():
        for k in range(4):
            plaene.append((lokal(letzter - timedelta(days=9 * k), 21, 10 + k), repo))
    # Diese Woche fest nach WOCHE, Donnerstag bis 22:35.
    for wt, repos in WOCHE.items():
        tag = montag + timedelta(days=wt)
        n = sum(repos.values())
        zeiten = schreibzeiten(rnd, tag, n, bis=JETZT - timedelta(minutes=5) if tag == heute else None)
        folge = [r for r, k in repos.items() for _ in range(k)]
        rnd.shuffle(folge)
        plaene += list(zip(zeiten, folge))
    plaene.sort()
    # Betreffe: je Repo der Reihe nach aus der Liste, der Sammelcommit am Donnerstag in drei Repos.
    zaehler = {r: 0 for r in namen}
    aus = []
    sharp = {"atlas-web", "ledger-site", "site-template"}
    for t, repo in plaene:
        if t.date() == heute and repo in sharp:
            sharp.discard(repo)
            aus.append((t, repo, SHARP))
            continue
        liste = BETREFFE[repo]
        aus.append((t, repo, liste[zaehler[repo] % len(liste)]))
        zaehler[repo] += 1
    return aus


def schreiben(ziel, szene="busy"):
    if szene not in SZENEN:
        raise ValueError(f"Szene {szene!r}, erlaubt: {', '.join(SZENEN)}")
    ziel = Path(ziel)
    if ziel.exists() and any(ziel.iterdir()) and not (ziel / MARKE).is_file():
        raise SystemExit(f"{ziel}: nicht leer und nicht von demo/demodaten.py angelegt. Der Demodatensatz schreibt nur in einen leeren "
                         f"oder eigenen Ordner und löscht dort nichts Fremdes.")
    ziel.mkdir(parents=True, exist_ok=True)
    (ziel / MARKE).touch()
    dbpfad = ziel / "gitpuls.db"
    for name in EIGENE_DATEIEN:
        (ziel / name).unlink(missing_ok=True)
    os.environ["GITPULS_DB"] = str(dbpfad)   # vor dem Import: db.DB liest die Variable beim Laden
    sys.path.insert(0, str(REPO))
    from app import db as appdb
    appdb.DB = dbpfad
    c = appdb.verbinden(schreibend=True)
    rnd = random.Random(15102026)
    busy = szene == "busy"
    heute, montag = JETZT.date(), JETZT.date() - timedelta(days=JETZT.weekday())
    jetzt_u = JETZT.astimezone(timezone.utc)

    commits = plan(rnd)
    woche = [i for i, (t, _, b) in enumerate(commits) if t.date() >= montag and b != SHARP]   # der Sammelcommit ändert nur package.json
    plus_w = [rnd.uniform(.3, 1.7) for _ in woche]
    minus_w = [rnd.uniform(.2, 1.8) for _ in woche]
    zeilen = {}
    ziel_w = [z - sum(1 for t, _, b in commits if t.date() >= montag and b == SHARP) for z in WOCHE_ZEILEN]   # je Sammelcommit +1 / −1
    for k, i in enumerate(woche):   # die Woche auf genau +7'000 / −1'800, Rest der Historie frei
        zeilen[i] = (round(ziel_w[0] * plus_w[k] / sum(plus_w)), round(ziel_w[1] * minus_w[k] / sum(minus_w)))
    rest = (ziel_w[0] - sum(p for p, _ in zeilen.values()), ziel_w[1] - sum(m for _, m in zeilen.values()))
    zeilen[woche[-1]] = (zeilen[woche[-1]][0] + rest[0], zeilen[woche[-1]][1] + rest[1])
    endung = {r: e for r, _, e, _ in REPOS}
    shas = {}
    for i, (t, repo, betreff) in enumerate(commits):
        sha = hashlib.sha1(f"{KONTO}/{repo}/{i}".encode()).hexdigest()
        shas.setdefault(repo, []).append((sha, betreff))
        c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, autor_login, betreff, eltern, dateien_geladen) VALUES (?,?,?,?,?,?,1,1)",
                  (sha, f"{KONTO}/{repo}", utc(t), ADRESSE, KONTO, betreff))
        plus, minus = zeilen.get(i, (rnd.randint(10, 260), rnd.randint(0, 90)))
        if betreff == SHARP:
            c.execute("INSERT INTO commit_dateien VALUES (?,?,?,?)", (sha, "package.json", 1, 1))
            continue
        teile = rnd.randint(1, 3)
        for j in range(teile):
            c.execute("INSERT INTO commit_dateien VALUES (?,?,?,?)", (sha, f"src/{repo.replace('-', '_')}_{j}{endung[repo]}",
                                                                      plus // teile + (plus % teile if j == 0 else 0), minus // teile + (minus % teile if j == 0 else 0)))

    for k in range(4):   # Sammellauf stündlich, der letzte vor 6 Minuten
        start = jetzt_u - timedelta(minutes=6 + 60 * k)
        c.execute("INSERT INTO laeufe (gestartet_utc, beendet_utc, neu, teilweise) VALUES (?,?,?,0)", (utc(start), utc(start + timedelta(seconds=41)), 3 if k == 0 else 1))
    gesehen = utc(jetzt_u - timedelta(minutes=6))
    for i, (repo, sprache, _, _) in enumerate(REPOS):
        letzter = max((t for t, r, _ in commits if r == repo), default=JETZT)
        c.execute("INSERT INTO repos (repo, default_branch, sprache, pushed_at, gesehen, id, html_url, abgerufen_utc, abruf_branch) VALUES (?,?,?,?,?,?,?,?,?)",
                  (f"{KONTO}/{repo}", "main", sprache, utc(letzter), gesehen, 810_000_000 + i * 7919, f"https://github.com/{KONTO}/{repo}", gesehen, "main"))

    # Prüfungen: Actions in den Repos mit Pipeline, Dependabot überall an. busy: ledger-site rot, client-portal mit zwei Warnungen.
    ci_repos = {"atlas-web": "Build", "atlas-api": "Tests", "ledger": "Xcode Cloud", "ledger-site": "Deploy to Pages", "site-template": "Build",
                "client-portal": "CI", "cli-tools": "Release", "pricewatch": "Tests"}
    for repo, *_ in REPOS:
        rot = busy and repo == "ledger-site"
        warn = busy and repo == "client-portal"
        ci = "fehlgeschlagen" if rot else "ok" if repo in ci_repos else "keine"
        c.execute("INSERT INTO pruefungen (repo, ci, ci_workflow, ci_utc, sicherheit, warnungen, warn_stufe, gesehen, ci_gemessen, sicher_gemessen) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (f"{KONTO}/{repo}", ci, ci_repos.get(repo), utc(JETZT - timedelta(minutes=48)) if repo in ci_repos else None, "ok",
                   2 if warn else 0, "high" if warn else None, gesehen, gesehen, gesehen))
    if busy:
        for nummer, paket, stufe, scope, summary, behoben, bereich, tage, ghsa, cve in (
                (14, "undici", "high", "runtime", "Unbounded decompression chain in fetch responses leads to memory exhaustion", "7.16.2",
                 ">= 7.0.0, < 7.16.2", 2, "GHSA-4r9c-m2xq-p8hw", "CVE-2026-41208"),
                (15, "esbuild", "medium", "development", "Development server accepts requests from any origin", "0.25.4",
                 "<= 0.25.3", 9, "GHSA-6v2x-q7jf-3c8m", "CVE-2026-38817")):
            c.execute("INSERT INTO warnungen (repo, nummer, paket, oekosystem, manifest, stufe, behoben, url, summary, scope, geoeffnet, bereich, ghsa, cve) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (f"{KONTO}/client-portal", nummer, paket, "npm", "package-lock.json", stufe, behoben,
                       f"https://github.com/{KONTO}/client-portal/security/dependabot/{nummer}", summary, scope, utc(JETZT - timedelta(days=tage)), bereich, ghsa, cve))
        # Ein Zweig wartet seit 4 Tagen, Dependabot-Gruppen in atlas-web und site-template.
        for repo, name, ahead, behind, alter in (("ledger", "feature/widget-timeline", 4, 2, timedelta(days=4, hours=1)),
                                                 ("atlas-web", "dependabot/npm_and_yarn/astro-1f3c5a7e9b", 1, 0, timedelta(days=1, hours=3)),
                                                 ("atlas-web", "dependabot/npm_and_yarn/dev-dependencies-8c2e4a6b0d", 1, 0, timedelta(days=2, hours=5)),
                                                 ("site-template", "dependabot/npm_and_yarn/dev-dependencies-3b5d7f9a1c", 1, 0, timedelta(days=2, hours=5))):
            c.execute("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc, kopf_sha) VALUES (?,?,?,?,?,?)",
                      (f"{KONTO}/{repo}", name, ahead, behind, utc(JETZT - alter), hashlib.sha1(name.encode()).hexdigest()))

    c.commit()
    c.close()

    # Server: atlas-api läuft im busy-Fall zwei Commits hinter main, mit den Betreffen der zwei jüngsten Commits.
    api = shas["atlas-api"]
    hinter = 2 if busy else 0
    dienst_deploy = {}
    for repo in ("atlas-api", "pricewatch", "client-portal", "notes-sync"):
        s = shas[repo]
        stand = s[-1 - hinter] if repo == "atlas-api" else s[-1]
        dienst_deploy[f"{KONTO}/{repo}"] = {"repo": f"{KONTO}/{repo}", "tag": stand[0][:7], "origin_main": s[-1][0][:7], "hinter": hinter if repo == "atlas-api" else 0,
                                            "gleich": not (repo == "atlas-api" and hinter),
                                            "bildrelevant": hinter if repo == "atlas-api" else 0,
                                            "commits": [{"sha": x[:7], "betreff": b} for x, b in reversed(api[-hinter:])] if repo == "atlas-api" and hinter else [],
                                            "gemessen": (JETZT - timedelta(minutes=10)).isoformat(timespec="seconds")}
    # deploy.json je Dienst wie tools/deploy-stand.sh seit 11.10.2026 (Codex-Audit, Befund 9); die Dienste heissen wie ihre Repos
    (ziel / "deploy.json").write_text(json.dumps({k.split("/", 1)[1]: v for k, v in dienst_deploy.items()}, ensure_ascii=False, indent=1))
    (ziel / "server.json").write_text(json.dumps(server(dienst_deploy), ensure_ascii=False, indent=1))
    (ziel / "umgebung.json").write_text(json.dumps({"GITPULS_DB": str(dbpfad), "GITPULS_JETZT": JETZT.isoformat(), "TZ": ZONE.key,
                                                    "GITPULS_ADRESSEN": ADRESSE}, indent=1))
    return {"commits": len(commits), "repos": len(REPOS), "szene": szene}


def server(dienst_deploy):
    """server.json im Format von tools/server-stand: eine VPS mit Debian 13, 2 Kernen und 4 GB, fünf Dienste, Firewall vollständig,
    Sicherheitsupdates automatisch, ein Update von Hand (Docker CE), kein Neustart nötig."""
    z = lambda **k: utc(JETZT - timedelta(**k))
    boot = JETZT - timedelta(days=23, hours=4)

    def timer(unit, letzter, naechster, dauer_s=4):
        return {"unit": unit, "timer_aktiv": "active", "status": "inactive", "gestartet_utc": utc(letzter - timedelta(seconds=dauer_s)),
                "letzter_utc": utc(letzter), "ergebnis": "success", "exit": 0, "naechster_utc": utc(naechster)}

    def container(name, bild, seit):
        return {"name": name, "status": "running", "seit_utc": utc(seit), "bild": bild, "neustarts": 0, "gesund": "healthy"}

    stunde = JETZT.replace(minute=0)
    api = dienst_deploy[f"{KONTO}/atlas-api"]
    dienste = [
        {"name": "atlas-api", "zweck": "REST API for the atlas site: search index, image pipeline, webhooks", "link": "https://api.atlas.fernwerk-studio.example/",
         "repo": f"{KONTO}/atlas-api", "checkout": "/srv/atlas-api", "zeilen": [],
         "container": [container("atlas-api-app-1", f"atlas-api:{api['tag']}", JETZT - timedelta(days=1, hours=2)),
                       container("atlas-api-worker-1", f"atlas-api:{api['tag']}", JETZT - timedelta(days=1, hours=2))],
         "timer": [timer("atlas-api-reindex", stunde + timedelta(seconds=38), stunde + timedelta(hours=1), 36),
                   timer("atlas-api-backup", JETZT.replace(hour=3, minute=12), JETZT.replace(hour=3, minute=10) + timedelta(days=1), 120)],
         "deploy": api},
        {"name": "pricewatch", "zweck": "Price tracker bot, checks due sources every ten minutes", "link": "https://prices.fernwerk-studio.example/",
         "repo": f"{KONTO}/pricewatch", "checkout": "/srv/pricewatch", "zeilen": [],
         "container": [container("pricewatch-bot-1", f"pricewatch:{dienst_deploy[f'{KONTO}/pricewatch']['tag']}", JETZT - timedelta(days=6))],
         "timer": [{"unit": "pricewatch-run", "timer_aktiv": "active", "status": "activating", "gestartet_utc": z(seconds=48),
                    "letzter_utc": z(minutes=10), "ergebnis": "success", "exit": 0, "naechster_utc": None}],
         "deploy": dienst_deploy[f"{KONTO}/pricewatch"]},
        {"name": "client-portal", "zweck": "Client portal: invoices, project status, file uploads", "link": "https://portal.fernwerk-studio.example/",
         "repo": f"{KONTO}/client-portal", "checkout": "/srv/client-portal", "zeilen": [],
         "container": [container("client-portal-web-1", f"client-portal:{dienst_deploy[f'{KONTO}/client-portal']['tag']}", JETZT - timedelta(days=1, hours=14))],
         "timer": [], "deploy": dienst_deploy[f"{KONTO}/client-portal"]},
        {"name": "notes-sync", "zweck": "Syncs Markdown notes between the laptop and a private Git remote", "link": "https://notes.fernwerk-studio.example/",
         "repo": f"{KONTO}/notes-sync", "checkout": "/srv/notes-sync", "zeilen": [],
         "container": [container("notes-sync-1", f"notes-sync:{dienst_deploy[f'{KONTO}/notes-sync']['tag']}", JETZT - timedelta(days=1, hours=23))],
         "timer": [timer("notes-sync-pull", JETZT.replace(minute=30, second=6), JETZT.replace(minute=45))],
         "deploy": dienst_deploy[f"{KONTO}/notes-sync"]},
        {"name": "gitpuls", "zweck": "This dashboard: GitHub activity and server state", "link": "https://gitpuls.fernwerk-studio.example/",
         "repo": None, "checkout": "/srv/gitpuls", "zeilen": [],
         "container": [container("gitpuls-gitpuls-1", "gitpuls:4e1b7d2", JETZT - timedelta(days=3, hours=5))],
         "timer": [timer("gitpuls-sammler", JETZT - timedelta(minutes=5, seconds=19), stunde + timedelta(hours=1, minutes=34), 41),
                   timer("gitpuls-server", JETZT - timedelta(minutes=2), JETZT + timedelta(minutes=3), 1)],
         "deploy": {"tag": "4e1b7d2", "origin_main": "4e1b7d2", "hinter": 0, "bildrelevant": 0, "commits": [], "gemessen": z(minutes=10)}},
    ]
    return {
        "erhoben_utc": z(minutes=2),
        "maschine": {"hostname": "fernwerk-studio-01", "produkt": "VPS, Helsinki", "vcores": 2, "ram_bytes": 4_013_948_928,
                     "platte_bytes": 79_456_894_976, "os": "Debian GNU/Linux 13 (trixie)", "kernel": "6.12.48+deb13-amd64"},
        "zustand": {"boot_utc": utc(boot), "load": [0.21, 0.17, 0.12], "ram_belegt_bytes": 1_684_000_768, "ram_verfuegbar_bytes": 2_329_948_160,
                    "platte_belegt_bytes": 18_870_000_640, "platte_frei_bytes": 56_820_000_768, "pakete_upgradebar": 1,
                    "pakete_quellen": {"Docker CE:trixie": 1}, "quellen_auto": {"Docker CE:trixie": False},
                    "neustart_noetig": False, "unattended_upgrades": True},
        "installiert": {"docker": "29.8.1", "containerd": "2.3.4", "compose": "5.5.1", "tailscale": "1.104.2", "serve_ports": [443, 8443, 8444, 8445]},
        "firewall": {"art": "iptables", "ufw": None, "familien": {"IPv4": {"INPUT": 7, "DOCKER-USER": 4}, "IPv6": {"INPUT": 7, "DOCKER-USER": 4}},
                     "nicht_gemessen": [], "persistent": True, "wie_gespeichert": True, "gespeichert_utc": utc(boot - timedelta(days=2)), "seit_neustart": True,
                     "nicht_verglichen": ["nat", "raw"]},   # wie tools/server-stand auf einem Docker-Host seit 0e425fc
        "dienste": dienste,
    }


if __name__ == "__main__":
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("ziel")
    a.add_argument("--szene", choices=SZENEN, default="busy")
    arg = a.parse_args()
    r = schreiben(arg.ziel, arg.szene)
    print(f"{arg.ziel}: {r['commits']} Commits, {r['repos']} Repos, Szene {r['szene']}, jetzt {JETZT.isoformat()}")
