"""Demodatensatz (demo/demodaten.py, 09.10.2026): vollständig, beide Szenen, feste Uhr über GITPULS_JETZT, ohne die Variable die
Systemuhr. Seiten und Uhr laufen in Unterprozessen, weil db.DB und die Zone beim Import gelesen werden. python3 -m tests.test_demodaten"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("demodaten", REPO / "demo" / "demodaten.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)

tmp = Path(tempfile.mkdtemp())
for szene in demo.SZENEN:
    demo.schreiben(tmp / szene, szene)
demo.schreiben(tmp / "nochmal", "busy")
for szene in demo.SZENEN:
    for datei in ("gitpuls.db", "server.json", "deploy.json", "umgebung.json"):
        assert (tmp / szene / datei).is_file(), (szene, datei)
try:
    demo.schreiben(tmp / "falsch", "laut")
    raise AssertionError("unbekannte Szene angenommen")
except ValueError:
    pass

db = lambda szene: sqlite3.connect(tmp / szene / "gitpuls.db")
c = db("busy")
q = lambda sql, *a: c.execute(sql, a).fetchall()
lokal = lambda iso: datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(demo.ZONE)
commits = [(lokal(t), r.split("/")[1], b) for t, r, b in q("SELECT zeitstempel_utc, repo, betreff FROM commits")]
jetzt, heute = demo.JETZT, demo.JETZT.date()
montag = heute - timedelta(days=heute.weekday())

# Datensatz vollständig: Donnerstag 22:40 in der Mitte des Monats, 14 Repos, drei ohne Commit diese Woche, alles bis jetzt.
assert heute.weekday() == 3 and (jetzt.hour, jetzt.minute) == (22, 40) and 10 <= heute.day <= 20, jetzt
assert len(q("SELECT * FROM repos")) == 14 and all(r.startswith("fernwerk-studio/") for r, in q("SELECT repo FROM repos"))
woche = [x for x in commits if x[0].date() >= montag]
assert {r for _, r, _ in woche} == {r for r, *_ in demo.REPOS[:11]}, "elf Repos diese Woche, drei ruhig"
assert all(t <= jetzt for t, _, _ in commits), "kein Commit nach jetzt"
assert len(woche) == 50, len(woche)
je_tag = {d: [x for x in woche if x[0].date() == d] for d in (montag + timedelta(days=i) for i in range(4))}
assert all(4 <= len({r for _, r, _ in v}) <= 5 for v in je_tag.values()), "4 bis 5 Repos je Tag"
assert max(je_tag, key=lambda d: len(je_tag[d])) == heute, "Spitze am Donnerstag"
assert 0.53 <= sum(r == "atlas-web" for _, r, _ in woche) / 50 <= 0.58
assert all(t.hour >= 20 or 5 <= t.hour < 8 for t, _, _ in commits), "spätabends und frühmorgens"
vorwoche = [x for x in commits if montag - timedelta(days=7) <= x[0].date() and x[0] <= jetzt - timedelta(days=7)]
assert 1.25 <= len(vorwoche) / 50 <= 1.35, len(vorwoche)
dreissig = sum(heute - timedelta(days=29) <= t.date() for t, _, _ in commits)
davor = sum(heute - timedelta(days=59) <= t.date() < heute - timedelta(days=29) for t, _, _ in commits)
assert 225 <= dreissig <= 235 and 0.65 <= davor / dreissig <= 0.75, (dreissig, davor)
tage = {t.date() for t, _, _ in commits}
assert all(heute - timedelta(days=i) in tage for i in range(15)) and heute - timedelta(days=15) not in tage, "Serie 15 Tage"
assert min(tage) < heute - timedelta(days=150), "Rückblick über mehrere Monate"
zeilen = q("SELECT SUM(d.plus), SUM(d.minus) FROM commit_dateien d JOIN commits c ON c.sha = d.sha WHERE c.zeitstempel_utc >= ?",
           demo.utc(datetime.combine(montag, datetime.min.time(), demo.ZONE)))
assert zeilen == [(7000, 1800)], zeilen
sharp = {(t.date(), r) for t, r, b in commits if b == demo.SHARP}
assert len(sharp) == 3 and len({d for d, _ in sharp}) == 1, sharp
assert not q("SELECT name FROM sqlite_master WHERE name IN ('sterne', 'fremde', 'fremde_detail')"), "die Rangliste ist mit 1.1.0 entfallen"

# Nichts zeigt auf den Autor, Kunden oder echte Adressen: alle Texte der Datenbank und der JSON-Dateien durchsucht. Die Namen des Autors
# stehen seit 10.10.2026 nicht mehr hier, sondern in der Sperrliste ausserhalb des Repos (Standard Öffentliche Repos, A5): GITPULS_SPERRLISTE
# oder ~/.config/veroeffentlichung/sperrliste.txt, eine Zeichenfolge je Zeile; ohne Datei gilt nur die allgemeine Prüfung auf Adressen.
SPERRLISTE = Path(os.environ.get("GITPULS_SPERRLISTE") or Path.home() / ".config" / "veroeffentlichung" / "sperrliste.txt")
GESPERRT = [z.strip().lower() for z in SPERRLISTE.read_text().splitlines() if z.strip()] if SPERRLISTE.is_file() else []
for szene in demo.SZENEN:
    texte = [json.dumps(json.loads((tmp / szene / f).read_text())) for f in ("server.json", "deploy.json")]
    d = db(szene)
    for tabelle, in d.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        texte += [str(z) for z in d.execute(f"SELECT * FROM {tabelle}")]
    alles = "\n".join(texte).lower()
    for verboten in GESPERRT:
        assert verboten not in alles, (szene, verboten)
    import re
    assert all(a.endswith(".example") for a in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", alles)), (szene, "Adressen nur unter .example")
    # Bis 09.10.2026: halden ist ein echtes Konto, Fjordhost eine Anbietermarke, die Rangliste trug echte Projekte mit erfundenen Zahlen.
    for verboten in ("halden", "fjordhost", "astral-sh/ty", "sst/opencode", "cloudflare/vibesdk", "modelcontextprotocol/registry",
                     "browser-use/workflow-use", "google-gemini/gemini-cli", "openai/codex-universal", "microsoft/markitdown-mcp",
                     "withastro/flue", "charmbracelet/crush", "tailwindlabs/tailwindcss-motion", "pydantic/pydantic-ai-graph",
                     "vercel/workflow", "denoland/fresh-ui", "swiftlang/swift-build"):
        assert verboten not in alles, (szene, verboten)
    adressen = {a for a, in d.execute("SELECT DISTINCT autor_adresse FROM commits")}
    assert adressen == {demo.ADRESSE}, adressen

# Beide Szenen: dieselben Commits, busy mit je einem Befund jeder Art, ruhig ohne Befund; derselbe Aufruf ergibt denselben Datensatz.
alle = lambda szene: db(szene).execute("SELECT sha, repo, zeitstempel_utc, betreff FROM commits ORDER BY sha").fetchall()
assert alle("busy") == alle("ruhig") == alle("nochmal")
r = db("ruhig")
assert q("SELECT paket, stufe, scope FROM warnungen ORDER BY nummer") == [("undici", "high", "runtime"), ("esbuild", "medium", "development")]
assert all(len([f for f in w if f is None]) == 0 for w in q("SELECT * FROM warnungen")), "alle Felder befüllt"
assert q("SELECT repo, warnungen, warn_stufe FROM pruefungen WHERE warnungen > 0") == [("fernwerk-studio/client-portal", 2, "high")]
assert q("SELECT repo, ci_workflow FROM pruefungen WHERE ci = 'fehlgeschlagen'") == [("fernwerk-studio/ledger-site", "Deploy to Pages")]
zweige = q("SELECT repo, name, letzter_utc FROM zweige")
assert {z[0] for z in zweige if "/dependabot/" in "/" + z[1]} == {"fernwerk-studio/atlas-web", "fernwerk-studio/site-template"}
wartet = [z for z in zweige if not z[1].startswith("dependabot/")]
assert len(wartet) == 1 and (jetzt - lokal(wartet[0][2])).days == 4, wartet
api = json.loads((tmp / "busy" / "deploy.json").read_text())["atlas-api"]   # je Dienst seit 11.10.2026 (Codex-Audit, Befund 9)
assert api["repo"] == "fernwerk-studio/atlas-api" and api["hinter"] == 2 and len(api["commits"]) == 2 and all(x["betreff"] for x in api["commits"]), api
assert r.execute("SELECT COUNT(*) FROM warnungen").fetchone() == (0,) and r.execute("SELECT COUNT(*) FROM zweige").fetchone() == (0,)
assert r.execute("SELECT COUNT(*) FROM pruefungen WHERE ci = 'fehlgeschlagen' OR warnungen > 0").fetchone() == (0,)
assert all(e["hinter"] == 0 for e in json.loads((tmp / "ruhig" / "deploy.json").read_text()).values())

# Server: Debian 13, 2 Kerne, 4 GB, fünf Dienste mit laufendem Lauf, einem ohne Timer, atlas-api mit Containern, Läufen und Stand.
s = json.loads((tmp / "busy" / "server.json").read_text())
assert "Debian GNU/Linux 13" in s["maschine"]["os"] and s["maschine"]["vcores"] == 2 and round(s["maschine"]["ram_bytes"] / 1e9) == 4
assert len(s["dienste"]) == 5 and any(t["status"] == "activating" for d in s["dienste"] for t in d["timer"])
assert any(not d["timer"] for d in s["dienste"]) and s["zustand"]["pakete_quellen"] == {"Docker CE:trixie": 1}
assert s["zustand"]["unattended_upgrades"] and not s["zustand"]["neustart_noetig"]


def python(code, **env):
    umgebung = {k: v for k, v in os.environ.items() if k != "GITPULS_JETZT"} | env
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, env=umgebung, capture_output=True, text=True, check=True).stdout.strip()


# Feste Uhr wirkt: db.jetzt() liefert sie, die Seite rechnet ihr «vor N Min.» und Heute dagegen.
assert python("from app import db; print(db.jetzt().isoformat())", GITPULS_JETZT=demo.JETZT.isoformat()) == "2026-10-15T20:40:00+00:00"
seite = "from app import web; print(web.seite(sprache='de'))"
for szene, erwartet, fehlt in (("busy", ("vor 5 Min.", "big mid\">20</span><span class=\"lbl\">Commits heute", "client-portal", "1 Dienst hinter main"), ("Kein Handlungsbedarf",)),
                               ("ruhig", ("Kein Handlungsbedarf", ">0 Repos mit Sicherheitswarnungen<", ">0 Actions-Läufe fehlgeschlagen<", ">0 Dienste hinter main<"), ("hb-warn-",))):
    u = json.loads((tmp / szene / "umgebung.json").read_text())
    html = python(seite, **u, GITPULS_SPRACHE="de", GITPULS_FORMAT="de-CH")
    assert "Block konnte nicht gezeichnet werden" not in html, szene
    for wort in erwartet:
        assert wort in html, (szene, wort)
    for wort in fehlt:
        assert wort not in html, (szene, wort)

# Ohne GITPULS_JETZT bleibt alles wie vorher: die Systemuhr.
vorher = datetime.now(timezone.utc)
ohne = datetime.fromisoformat(python("from app import db; print(db.jetzt().isoformat())"))
assert vorher - timedelta(seconds=1) <= ohne <= datetime.now(timezone.utc), ohne
print("demodaten: Datensatz, beide Szenen, feste Uhr und Systemuhr geprüft")
