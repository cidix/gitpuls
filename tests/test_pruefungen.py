"""CI, Sicherheitswarnungen und Codezeilen (25.09.2026) über den echten Sammlerpfad mit nachgebautem GitHub und auf der Seite.
403 und «disabled» brechen keinen Lauf ab, der Zustand steht einmal gedämpft im Kopf; Lockfiles zählen nicht; ein Abbruch mitten
im Altbestand verwirft keine festgeschriebene Portion; Codezeilen kommen aus derselben Fenstermenge wie die Kachelzahl.
python3 -m tests.test_pruefungen"""
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["GITPULS_DB"] = os.path.join(tempfile.mkdtemp(), "pruef.db")
os.environ.setdefault("GITPULS_ADRESSEN", "dev@fernwerk-studio.example")
os.environ.setdefault("GITPULS_SPRACHE", "de")   # die bisherigen Prüfungen laufen auf Deutsch, die Vorgabe ist seit 25.09.2026 en
os.environ["GITPULS_FORMAT"] = "de-CH"   # Schreibweisen wie die eigene Instanz (26.09.2026); die übrigen Locales prüft tests/test_format.py
os.environ["TZ"] = "Europe/Zurich"   # die Zone setzt der Test selbst (10.10.2026, Audit S28): grün unter jeder TZ der Shell
from app import db, github, sammler, web  # noqa: E402

TZ = web.TZ
jetzt = datetime(2026, 9, 24, 18, 0, tzinfo=TZ)                   # Donnerstag
ju = jetzt.astimezone(timezone.utc)
iso = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
ICH = "dev@fernwerk-studio.example"


def fehler(status, meldung):
    e = github.GitHubFehler(f"GitHub {status}")
    e.status, e.meldung, e.global_ = status, meldung, status == 401 or "rate limit" in meldung.lower()
    return e


def commit(sha, zeit, betreff="Arbeit", eltern=1):
    return {"sha": sha, "commit": {"author": {"date": iso(zeit), "email": ICH}, "message": betreff}, "author": None, "parents": [{"sha": "p"}] * eltern}


# Nachgebautes GitHub: je Repo Commits, Actions-Antwort, Dependabot-Antwort und Commit-Details mit Dateien.
WELT = {}
GEHOLT = []


def alle_seiten(pfad, params=None, leer_bei_409=False):
    GEHOLT.append(pfad)
    if pfad == "user/repos":
        return iter([{"full_name": n, "id": i, "archived": False, "pushed_at": iso(ju), "default_branch": "main", "language": "Python",
                      "html_url": f"https://github.com/{n}"} for i, n in enumerate(WELT)])
    _, b, n, art, *rest = pfad.split("/")
    w = WELT[f"{b}/{n}"]
    if art == "commits":
        if isinstance(w["commits"], BaseException):
            raise w["commits"]
        return iter(w["commits"])
    if art == "branches":
        return iter([{"name": "main", "commit": {"sha": "k"}}])
    if art == "dependabot":
        if isinstance(w["dependabot"], BaseException):
            raise w["dependabot"]
        return iter(w["dependabot"])
    return iter([])


def holen(pfad, params=None, leer_bei_409=False):
    GEHOLT.append(pfad)
    _, b, n, art, *rest = pfad.split("/")
    w = WELT[f"{b}/{n}"]
    if art == "actions":
        if isinstance(w["actions"], BaseException):
            raise w["actions"]
        return w["actions"], {}
    if art == "dependabot":
        # Cursor wie bei GitHub: page gibt 400, die zweite Seite kommt über after aus dem Link-Header (25.09.2026).
        if "page" in (params or {}):
            raise fehler(400, "Pagination using the `page` parameter is not supported.")
        if isinstance(w["dependabot"], BaseException):
            raise w["dependabot"]
        seiten = w["dependabot"] if w["dependabot"] and isinstance(w["dependabot"][0], list) else [w["dependabot"]]
        i = int((params or {}).get("after") or 0)
        return seiten[i], ({"Link": f'<https://api.github.com/x?per_page=100&after={i + 1}>; rel="next"'} if i + 1 < len(seiten) else {})
    if art == "commits" and rest:
        d = w["details"][rest[0]]
        if isinstance(d, BaseException):
            raise d
        seiten = d if isinstance(d, list) and d and isinstance(d[0], list) else [d]
        seite = (params or {}).get("page", 1)
        eltern = next((x["parents"] for x in w["commits"] if x["sha"] == rest[0]), [{"sha": "p"}])
        return {"files": seiten[seite - 1], "parents": eltern}, ({"Link": '<x>; rel="next"'} if seite < len(seiten) else {})
    raise AssertionError(pfad)


def lauf(alles=True, frist=None):
    alt = github.alle_seiten, github.holen, sammler.jetzt, sammler.ZEILEN_FRIST_S
    github.alle_seiten, github.holen, sammler.jetzt = alle_seiten, holen, lambda: iso(ju)
    if frist is not None:
        sammler.ZEILEN_FRIST_S = frist
    try:
        sammler.main(alles=alles)
        return True
    except SystemExit:
        return False
    finally:
        github.alle_seiten, github.holen, sammler.jetzt, sammler.ZEILEN_FRIST_S = alt


def warnung(repo, nummer, paket, stufe, behoben, manifest="package-lock.json", oeko="npm", scope="runtime", cve=None):
    """Eine offene Dependabot-Warnung in der Form der API (25.09.2026 an fernwerk-studio/glyphlabs-web gelesen)."""
    return {"number": nummer, "html_url": f"https://github.com/{repo}/security/dependabot/{nummer}", "state": "open", "created_at": "2026-09-25T02:06:15Z",
            "dependency": {"package": {"ecosystem": oeko, "name": paket}, "manifest_path": manifest, "scope": scope},
            "security_advisory": {"severity": stufe, "summary": f"{paket} has a problem number {nummer}", "ghsa_id": f"GHSA-test-{nummer:04d}", "cve_id": cve},
            "security_vulnerability": {"severity": stufe, "vulnerable_version_range": f"< {behoben}" if behoben else ">= 0",
                                       "first_patched_version": {"identifier": behoben} if behoben else None}}


def datei(name, plus, minus):
    return {"filename": name, "additions": plus, "deletions": minus}


# Drei Repos. a: roter Actions-Lauf, Dependabot mit zwei Warnungen; b: grüner Lauf, Dependabot ausgeschaltet; c: Token ohne
# Leserecht für beides. Commits diese Woche (ab Mo 21.09.) und in der Vorwoche bis Donnerstag, dazu ein Lockfile-Commit und ein Merge.
mo = datetime(2026, 9, 21, 9, 0, tzinfo=TZ)
WELT.update({
    # a9 liegt am Donnerstag nach «jetzt» (20:00): er zählt in keinem Fenster, auch nicht in den Codezeilen der Woche.
    "fernwerk-studio/a": {"commits": [commit("a9", mo + timedelta(days=3, hours=11), "Zukunft"), commit("a3", mo + timedelta(days=2, hours=1), "Daten und Code"), commit("a1", mo), commit("a2", mo + timedelta(days=1), "Abhängigkeiten"), commit("am", mo + timedelta(days=2), "Merge", eltern=2),
                            commit("a0", mo - timedelta(days=6))],
                "actions": {"total_count": 3, "workflow_runs": [{"name": "Tests", "conclusion": "failure", "updated_at": iso(ju - timedelta(hours=3))}]},
                "dependabot": [[warnung("fernwerk-studio/a", 2, "axios", "medium", "11.1.1")], [warnung("fernwerk-studio/a", 3, "tmp", "high", "0.2.6", scope="development", cve="CVE-2026-44705")]],   # zwei Seiten
                # a3, Regel vom 25.09.2026: Datendatei, Verzeichnis dist, Einzeldatei über 3000 Zeilen fallen weg, package.json und Code zählen.
                "details": {"a9": [datei("app/z.py", 1000, 1000)], "a1": [datei("app/x.py", 10, 2), datei("README.md", 3, 0)],
                            "a3": [datei("data/regionen/insel.json", 500, 0), datei("web/dist/app.js", 50, 0), datei("src/gross.py", 3500, 0),
                                   datei("package.json", 1, 0), datei("src/klein.py", 7, 3), datei("src/genau.py", 3000, 0)],
                            "a2": [datei("package-lock.json", 900, 400), datei("sub/yarn.lock", 50, 50), datei("go.sum", 7, 7), datei("package.json", 2, 1)],
                            "a0": [datei("app/y.py", 5, 5)]}},
    "fernwerk-studio/b": {"commits": [commit("b1", mo + timedelta(days=3, hours=-1))],
                "actions": {"total_count": 1, "workflow_runs": [{"name": "Build", "conclusion": "success", "updated_at": iso(ju - timedelta(days=1))}]},
                "dependabot": fehler(403, "Dependabot alerts are disabled for this repository."),
                # Viele Dateien: das Commit-Detail kommt seitenweise, beide Seiten zählen.
                "details": {"b1": [[datei(f"f{i}.py", 1, 0) for i in range(100)], [datei("g.py", 4, 1)]]}},
    "fernwerk-studio/c": {"commits": [commit("c1", mo - timedelta(days=5))],
                "actions": fehler(403, "Resource not accessible by personal access token"),
                "dependabot": fehler(403, "Resource not accessible by personal access token"),
                "details": {"c1": fehler(404, "Not Found")}},
})
assert lauf(), "403 und disabled brechen den Lauf nicht ab"
with db.verbinden() as c:
    p = {r["repo"]: dict(r) for r in c.execute("SELECT * FROM pruefungen")}
    # Summen je Commit aus der Rohtabelle mit der Regel, die Erwartungen unten stehen als feste Zahlen.
    z = {r["sha"]: (r["p"], r["m"], r["eltern"], r["dateien_geladen"]) for r in c.execute(
        "SELECT sha, eltern, dateien_geladen, (SELECT SUM(CASE WHEN zaehlt(pfad, plus, minus) THEN plus ELSE 0 END) FROM commit_dateien d WHERE d.sha = commits.sha) p, "
        "(SELECT SUM(CASE WHEN zaehlt(pfad, plus, minus) THEN minus ELSE 0 END) FROM commit_dateien d WHERE d.sha = commits.sha) m FROM commits")}
    roh_a3 = sorted(tuple(r) for r in c.execute("SELECT pfad, plus, minus FROM commit_dateien WHERE sha = 'a3'"))
    laeufe = c.execute("SELECT COUNT(*) FROM laeufe").fetchone()[0]
assert laeufe == 1, "die Laufzeile steht, der Lauf gilt als erfolgreich"
assert (p["fernwerk-studio/a"]["ci"], p["fernwerk-studio/a"]["ci_workflow"], p["fernwerk-studio/a"]["sicherheit"], p["fernwerk-studio/a"]["warnungen"], p["fernwerk-studio/a"]["warn_stufe"]) == ("fehlgeschlagen", "Tests", "ok", 2, "high"), p["fernwerk-studio/a"]
assert (p["fernwerk-studio/b"]["ci"], p["fernwerk-studio/b"]["sicherheit"], p["fernwerk-studio/b"]["warnungen"]) == ("ok", "aus", None), p["fernwerk-studio/b"]
# Messvertrag (03.10.2026): ohne Leserecht ist nichts gemessen, der Fehler steht getrennt vom Wert, der Lauf ist ein Teilerfolg.
assert (p["fernwerk-studio/c"]["ci"], p["fernwerk-studio/c"]["sicherheit"], p["fernwerk-studio/c"]["ci_gemessen"], p["fernwerk-studio/c"]["sicher_gemessen"]) == ("unbekannt", "unbekannt", None, None) \
    and p["fernwerk-studio/c"]["ci_fehler"] == p["fernwerk-studio/c"]["sicher_fehler"] == "403 Resource not accessible by personal access token", p["fernwerk-studio/c"]
assert p["fernwerk-studio/a"]["ci_gemessen"] == p["fernwerk-studio/a"]["sicher_gemessen"] == p["fernwerk-studio/b"]["sicher_gemessen"] and p["fernwerk-studio/b"]["sicher_fehler"] is None, (p["fernwerk-studio/a"], p["fernwerk-studio/b"])
# Codezeilen: Lockfiles (auch in Unterordnern) zählen nicht, der Rest schon; der Merge wird nicht geholt; ein 404 ist nicht verfügbar (0);
# ein Commit-Detail über zwei Seiten zählt beide.
assert z["a1"] == (13, 2, 1, 1) and z["a2"] == (2, 1, 1, 1) and z["a0"] == (5, 5, 1, 1) and z["am"] == (None, None, 2, None), z
assert z["b1"] == (104, 1, 1, 1) and z["c1"] == (None, None, 1, 0), ("404: nicht verfügbar, nie geladen", z)
# Regel (d): alle Dateien stehen roh in der Tabelle, gezählt wird nur Code; 3000 Zeilen zählen, 3001 nicht (Grenze).
assert len(roh_a3) == 6 and z["a3"] == (3008, 3, 1, 1), (roh_a3, z["a3"])
assert [db.zaehlt(p_, a_, m_) for p_, a_, m_ in (("data/regionen/insel.json", 1, 0), ("web/dist/app.js", 1, 0), ("src/gross.py", 3001, 0), ("src/genau.py", 3000, 0),
                                                 ("package.json", 5, 5), ("tsconfig.app.json", 1, 0), ("x.geojson", 1, 0), ("a.csv", 1, 0), ("b.tsv", 1, 0), ("c.xml", 1, 0),
                                                 ("s.min.js", 1, 0), ("s.js.map", 1, 0), ("logo.svg", 1, 0), ("t/__snapshots__/a.snap", 1, 0), ("vendor/x.php", 1, 0),
                                                 ("node_modules/a/b.js", 1, 0), ("coverage/lcov.info", 1, 0), (".next/x.js", 1, 0), ("build/y.js", 1, 0),
                                                 ("sub/yarn.lock", 1, 0), ("docs/a.md", 1, 0))] == [0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
assert not any("commits/am" in g for g in GEHOLT), "der Merge wird nicht nachgeladen"

# Seite: Befunde in den Projektzeilen, Handlungsbedarf mit gemischten Arten, Hinweis auf die fehlenden Leserechte einmal gedämpft.
s = web.seite(jetzt=jetzt)
assert "blockfehler" not in s, s[s.find("blockfehler") - 200:][:400]
LISTE_A = "https://github.com/fernwerk-studio/a/security/dependabot"
# Seit 02.10.2026 ist jede Befundangabe ein Sprunglink auf ihren Eintrag im Handlungsbedarf, die Warnungen öffnen dort ihren Eintrag.
assert ('<span class="pbefund"><a href="#hb-ci-fernwerk-studio_2f_a">Actions fehlgeschlagen, Tests, vor 3 Std.</a> · <a href="#hb-warn-fernwerk-studio_2f_a">2 Sicherheitswarnungen, höchste hoch</a></span>') in s, \
    re.findall(r'<span class="pbefund">.*?</span>', s)
assert '<li id="hb-ci-fernwerk-studio_2f_a" title=' in s and '<li class="hbauf" id="hb-warn-fernwerk-studio_2f_a"><details class="hbeintrag"' in s
# Sicherheitswarnungen je Paket (25.09.2026): in der Tabelle wie von der API, im Handlungsbedarf Repo-Link auf die Liste, Zahl und
# höchste Stufe, dann die Pakete nach Stufe, je Link auf die Warnung mit Stufe und behobener Version im title.
with db.verbinden() as c:
    tab_w = [tuple(r) for r in c.execute("SELECT repo, nummer, paket, oekosystem, manifest, stufe, behoben, url FROM warnungen ORDER BY repo, nummer")]
assert tab_w == [("fernwerk-studio/a", 2, "axios", "npm", "package-lock.json", "medium", "11.1.1", "https://github.com/fernwerk-studio/a/security/dependabot/2"),
                 ("fernwerk-studio/a", 3, "tmp", "npm", "package-lock.json", "high", "0.2.6", "https://github.com/fernwerk-studio/a/security/dependabot/3")], tab_w
# Im Block Handlungsbedarf (26.09.2026) je Repo genau ein Eintrag in der Spalte der Art: sichtbar Repo als Link auf die Dependabot-Liste,
# die Pakete nach Stufe, gedämpft Stufe, Zahl und Geltungsbereich; axios wirkt zur Laufzeit. Der volle Text im title. Seit dem zweiten Lauf
# desselben Tages ist der Eintrag aufklappbar (standardmässig zu) und zeigt je Warnung wieder alle Einzelheiten wie vor 458fe35.
hb_w = s[s.index('id="handlung-warn"'):s.index('<section class="card projekte">')]
assert ('<li class="hbauf" id="hb-warn-fernwerk-studio_2f_a"><details class="hbeintrag" data-zeichen="warn:fernwerk-studio/a"><summary title="a: tmp, axios; hoch, 2 Warnungen, Laufzeit">'
        '<span class="hbtext"><a class="hbrepo" href="https://github.com/fernwerk-studio/a/security/dependabot" target="_blank" rel="noopener">a</a>'
        '<span class="hbz" translate="no">tmp, axios</span><span class="hbz hbrest">hoch, 2 Warnungen, Laufzeit</span></span>'
        '<span class="chev" aria-hidden="true"></span></summary><div class="hbdetail">') in hb_w, hb_w
assert hb_w.count('<details class="hbeintrag"') == 1 and ' open' not in hb_w, "ein Eintrag je Repo, zu ohne ?handlung="
w_bloecke = re.findall(r'<div class="hbw"><p class="hbtitel"><span translate="no">([^<]*)</span>(.*?)</p><p class="hbsumm" translate="no">([^<]*)</p>(.*?)<p class="hblinks">(.*?)</p></div>', hb_w)
assert [(p_, m_, su) for p_, m_, su, _, _ in w_bloecke] == [("tmp", '<span class="hbentw">Entwicklung</span>', "tmp has a problem number 3"),
                                                          ("axios", "", "axios has a problem number 2")], w_bloecke
felder_tmp = dict(re.findall(r'<div class="mz"><span class="mk">([^<]*)</span><span>(.*?)</span></div>', w_bloecke[0][3]))
assert felder_tmp == {"Stufe": "hoch", "Geltungsbereich": "Entwicklung, betrifft nur Build und Tests", "Ökosystem": "npm", "Manifest": '<span translate="no">package-lock.json</span>',
                      "Betroffen": "&lt; 0.2.6", "Behoben in": "0.2.6", "Geöffnet am": "25.09.2026", "GHSA": "GHSA-test-0003", "CVE": "CVE-2026-44705"}, felder_tmp
felder_axios = dict(re.findall(r'<span class="mk">([^<]*)</span><span>(.*?)</span>', w_bloecke[1][3]))
assert felder_axios["CVE"] == "keine" and felder_axios["Geltungsbereich"] == "Laufzeit" and len(felder_axios) == 9, felder_axios
assert w_bloecke[0][4] == ('<a href="https://github.com/fernwerk-studio/a/security/dependabot/3" target="_blank" rel="noopener">Warnung bei GitHub</a> · '
                           '<a href="https://github.com/advisories/GHSA-test-0003" target="_blank" rel="noopener">Advisory</a>'), w_bloecke[0][4]
assert '<p class="hblinks"><a href="https://github.com/fernwerk-studio/a/security/dependabot" target="_blank" rel="noopener">Alle Warnungen des Repos bei GitHub</a></p></div></details></li>' in hb_w
# ?handlung=warn:fernwerk-studio/a öffnet den Eintrag, ein entfallenes «…-alle» und ein fremdes Repo-Zeichen fallen weg.
s_auf = web.seite(jetzt=jetzt, handlung=web.handlung_zeichen("warn:fernwerk-studio/a,zweige-alle,warn:fernwerk-studio/<x>"))
assert '<details class="hbeintrag" data-zeichen="warn:fernwerk-studio/a" open>' in s_auf and web.handlung_zeichen("warn:fernwerk-studio/a,zweige-alle,warn:fernwerk-studio/<x>,warn") == {"warn:fernwerk-studio/a"}
kopf_w = s[s.index('<div class="n befunde">'):s.index('<div class="tiles3">')]
assert "github.com" not in kopf_w and "tmp" not in kopf_w, ("keine Repos und Pakete im Kopf", kopf_w)
# Jeder Link auf GitHub unter security öffnet in einem neuen Tab ohne Zugriff auf dieses Fenster (seit 26.09.2026 fünf: ohne die Links der entfallenen Warnungsdetails im Block).
sec = re.findall(r'<a [^>]*href="https://github.com/[^"]*/security/[^"]*"[^>]*>', s)
assert len(sec) >= 5 and all('target="_blank"' in a_ and 'rel="noopener"' in a_ for a_ in sec), [a_ for a_ in sec if 'rel="noopener"' not in a_]
# Aufklappdetail der Projektzeile: je Warnung Stufe, Paket als Link, Manifest-Pfad und behobene Version, höchste zuerst.
detail_a = s[s.index('id="p-fernwerk-studio_2f_a"'):]
detail_a = detail_a[:detail_a.index("</details>")]
assert re.findall(r'<li><span class="datum">([^<]*)</span><span><a href="([^"]*)" target="_blank" rel="noopener" title="[^"]*">([^<]*)</a> · <span translate="no">([^<]*)</span> · ([^<]*)</span></li>', detail_a) == [
    ("hoch", "https://github.com/fernwerk-studio/a/security/dependabot/3", "tmp", "package-lock.json", "behoben in 0.2.6"),
    ("mittel", "https://github.com/fernwerk-studio/a/security/dependabot/2", "axios", "package-lock.json", "behoben in 11.1.1")], detail_a[-900:]
kopf = s[s.index('<div class="n befunde">'):s.index('<div class="tiles3">')]
# Seit 03.10.2026 zählt c mit: ohne Leserecht ist nichts gemessen, das ist ein Befund (Art «messung») vor den Warnungen.
assert kopf.startswith('<div class="n befunde"><b>3</b><div class="bunter"><ul class="barten">'
                       '<li><a href="#handlung-messung">1 Repo nicht gemessen</a></li><li><a href="#handlung-warn">1 Repo mit Sicherheitswarnungen, 1 zur Laufzeit</a></li>'
                       '<li><a href="#handlung-ci">1 Actions-Lauf fehlgeschlagen</a></li></ul>'), kopf[:400]
# Die Sprunglinks treffen ihre Arten. Seit 09.10.2026 steht Server hinter main mit 0 in der gedämpften Zeile unter dem Titel, die Arten
# mit Befund teilen sich das Raster in der Reihenfolge aus BEFUND_ARTEN, je Art eine Spalte; der Block steht nach den Kacheln und vor den Projekten.
assert s.index('<div class="tiles3">') < s.index('<h3 class="titel">Handlungsbedarf</h3><p class="hbrest"><span id="handlung-server">0 Dienste hinter main</span></p><div class="hraster"><div><p class="hbart" id="handlung-messung">') \
    < s.index('<p class="hbart" id="handlung-warn">') < s.index('<p class="hbart" id="handlung-ci">') < s.index('<section class="card projekte">')
assert '<p class="hbart" id="handlung-warn">1 Repo mit Sicherheitswarnungen, 1 zur Laufzeit</p><ul class="hbliste">' in s
# Der Eintrag nennt je Messgrösse den Fehler (Daten von GitHub, nicht übersetzt) und gedämpft, dass es nie eine Messung gab.
assert ('<p class="hbart" id="handlung-messung">1 Repo nicht gemessen</p><ul class="hbliste"><li id="hb-messung-fernwerk-studio_2f_c" '
        'title="c: CI: 403 Resource not accessible by personal access token; Sicherheitswarnungen: 403 Resource not accessible by personal access token">'
        '<a class="hbrepo" href="https://github.com/fernwerk-studio/c" target="_blank" rel="noopener">c</a>'
        '<span class="hbz hbzwei">CI: <span translate="no">403 Resource not accessible by personal access token</span> <span class="hbrest">noch nie gemessen</span></span>'
        '<span class="hbz hbzwei">Sicherheitswarnungen: <span translate="no">403 Resource not accessible by personal access token</span> <span class="hbrest">noch nie gemessen</span></span></li>') in s, \
    s[s.index('id="handlung-messung"'):][:900]
assert '<a href="#hb-messung-fernwerk-studio_2f_c">nicht gemessen, CI, Sicherheitswarnungen</a>' in s, "Befundangabe der Projektzeile"
assert '<p class="hbart" id="handlung-ci">1 Actions-Lauf fehlgeschlagen</p>' in s
assert ('<li id="hb-ci-fernwerk-studio_2f_a" title="a: Tests fehlgeschlagen, vor 3 Std."><a class="hbrepo" href="https://github.com/fernwerk-studio/a/actions" target="_blank" rel="noopener">a</a>'
        '<span class="hbz">Tests fehlgeschlagen <span class="hbrest">vor 3 Std.</span></span></li>') in s
assert kopf.count('<p class="bhinweis">CI: Token ohne Leserecht Actions</p>') == 1 and kopf.count("Token ohne Leserecht Dependabot") == 1, kopf
satz = s[s.index('<div class="fenster" data-gruppe="k2" data-fenster="woche" hidden><p class="f">'):].split("</p>")[0]   # Fassung Woche (30.09.2026)
assert satz.endswith(' <span class="satz">Drei\u00a0Punkte mit Handlungsbedarf.</span>') and "4\u00a0Tagen" in satz and "75%" in satz and "Seit\u00a04\u00a0Tagen" in satz and "Actions-Lauf" not in satz and "rot" not in kopf and "glut" not in kopf, satz

# Englisch (25.09.2026): dieselbe Lage in en, jeder Block ohne Deutsch ausserhalb der Daten; Handlungsbedarf, Befundspalte und Hinweise
# mit englischem Satzbau und eigener Mehrzahl.
from tests.sprachpruefung import deutsch, text_ohne_daten  # noqa: E402
erfasst, echt_block = [], web.block
def mit_erfassung(name, fn, *a):
    h = echt_block(name, fn, *a)
    erfasst.append((name, h))
    return h
web.block = mit_erfassung
try:
    s_en = web.seite(jetzt=jetzt, sprache="en", ruhend=True)
finally:
    web.block = echt_block
namen = {"fernwerk-studio/a", "fernwerk-studio/b", "fernwerk-studio/c", "a", "b", "c", "Tests", "Build"}
assert not [(n, deutsch(text_ohne_daten(h), namen)) for n, h in erfasst if deutsch(text_ohne_daten(h), namen)], "Deutsch in einem Block"
kopf_en = s_en[s_en.index('<div class="n befunde">'):s_en.index('<div class="tiles3">')]
assert kopf_en.startswith('<div class="n befunde"><b>3</b><div class="bunter"><ul class="barten">'), kopf_en[:120]
assert '<li><a href="#handlung-messung">1 repo not measured</a></li>' in kopf_en and '<p class="hbart" id="handlung-messung">1 repo not measured</p>' in s_en \
    and '<span class="hbz hbzwei">Security alerts: <span translate="no">403 Resource not accessible by personal access token</span> <span class="hbrest">never measured</span></span>' in s_en, kopf_en
assert '<li><a href="#handlung-ci">1 failed Actions run</a></li>' in kopf_en and '<li><a href="#handlung-warn">1 repo with security alerts, 1 at runtime</a></li>' in kopf_en, kopf_en
assert '<p class="hbrest"><span id="handlung-server">0 services behind main</span></p>' in s_en, "Zeile mit 0 englisch"
assert '<summary title="a: tmp, axios; high, 2 alerts, runtime">' in s_en and "development, affects build and tests only" in s_en and '<span class="mk">Opened</span><span>Sep\u00a025,\u00a02026</span>' in s_en \
    and '<span class="hbentw">development</span>' in s_en and ">Alert on GitHub</a> · " in s_en and ">All alerts of the repo on GitHub</a>" in s_en and '<span class="hbz hbrest">high, 2 alerts, runtime</span>' in s_en, "Laufzeit englisch"
assert '<h3 class="titel">Needs attention</h3>' in s_en and '<a class="hbrepo" href="https://github.com/fernwerk-studio/a/actions" target="_blank" rel="noopener">a</a><span class="hbz">Tests failed <span class="hbrest">3 h ago</span></span>' in s_en
assert kopf_en.count('<p class="bhinweis">CI: token cannot read Actions</p>') == 1 and "Security alerts: token cannot read Dependabot" in kopf_en, kopf_en
assert '<span class="pbefund"><a href="#hb-ci-fernwerk-studio_2f_a">Actions failed, Tests, 3 h ago</a> · <a href="#hb-warn-fernwerk-studio_2f_a">2 security alerts, highest high</a></span>' in s_en, re.findall(r'<span class="pbefund">.*?</span>', s_en)
assert 'title="tmp, high, fixed in 0.2.6">tmp</a>' in s_en and 'title="axios, medium, fixed in 11.1.1">axios</a>' in s_en, "Pakete im Aufklappdetail der Projektzeile"
assert ' <span class="satz">Three\u00a0items need attention.</span></p>' in s_en

# Codezeilen der Wochenkachel gegen eigenes SQL über die Rohzeilen, ohne Merges, aus denselben Grenzen wie die Kachelzahl. Sichtbar steht
# seit dem 25.09.2026 nur die laufende Woche in Worten, einzeilig; die Vorwoche zum selben Zeitpunkt steht im title vor den Ausschlüssen.
with db.verbinden() as c:
    roh = c.execute("SELECT zeitstempel_utc, (SELECT SUM(CASE WHEN zaehlt(pfad, plus, minus) THEN plus ELSE 0 END) FROM commit_dateien d WHERE d.sha = commits.sha), "
                    "(SELECT SUM(CASE WHEN zaehlt(pfad, plus, minus) THEN minus ELSE 0 END) FROM commit_dateien d WHERE d.sha = commits.sha), eltern, repo FROM commits").fetchall()
w0, v0 = datetime(2026, 9, 21, tzinfo=TZ).astimezone(timezone.utc), datetime(2026, 9, 14, tzinfo=TZ).astimezone(timezone.utc)
v1 = v0 + (ju - datetime(2026, 9, 24, tzinfo=TZ).astimezone(timezone.utc)) + timedelta(days=3)
im = lambda a, b, repo=None: [r for r in roh if a <= datetime.fromisoformat(r[0].replace("Z", "+00:00")) < b and (r[3] or 1) <= 1 and repo in (None, r[4])]
summe = lambda zeilen: (sum(r[1] or 0 for r in zeilen), sum(r[2] or 0 for r in zeilen))
woche, vorwoche = im(w0, ju + timedelta(seconds=1)), im(v0, v1)
# a1 13/2, a2 2/1 (nur package.json), a3 3’008/3 (klein.py, genau.py mit genau 3000, package.json), b1 104/1; Vorwoche a0 5/5.
assert (summe(woche), summe(vorwoche)) == ((3127, 7), (5, 5)), (summe(woche), summe(vorwoche))
# Ausgeschlossen diese Woche: a2 package-lock 1300, yarn.lock 100, go.sum 14; a3 insel.json 500, dist 50, gross.py 3500: 5’464 in 6 Dateien.
# Seit 26.09.2026 «+N / −M Zeilen»: das Plus in Akzentfarbe (.zplus), das Minus gedämpft (.zminus), echtes Minuszeichen.
# Seit 30.09.2026 die Kachel über die letzten 7 Tage (Fr 18.09. bis jetzt): dieselben Commits wie die Woche ab Montag; die sieben Tage
# davor (11.09. bis 17.09.) enthalten wie die Vorwoche nur a0 mit Zeilen, der title nennt sie als «7 Tage davor».
assert all(datetime(2026, 9, 18, tzinfo=TZ) <= datetime.fromisoformat(r[0].replace("Z", "+00:00")) for r in woche)
assert (f'<span class="delta bezug zeilen" title="7 Tage davor: +{summe(vorwoche)[0]} / \u2212{summe(vorwoche)[1]} Zeilen\n'
        f'letzte 7 Tage ausgeschlossen: 5’464 Zeilen in 6 Dateien (Daten, Lockfiles, Erzeugtes, Einzeldateien über 3’000 Zeilen); 1 Commit ohne '
        f'Zeilenangabe"><span class="zplus">+3’127</span> / <span class="zminus">\u22127</span> Zeilen</span>') in s, re.findall(r'<span class="delta bezug zeilen".*?</span> Zeilen</span>', s, re.S)
# Raster im Detail: laufende Woche je Repo sichtbar, die Vorwoche im title, gegen die Vorwoche zum selben Zeitpunkt mit der Zeitgrenze der
# Kachel (v1, seit 25.09.2026 auch für die Projektzeile); a0 liegt darin, b hat dort nichts.
for repo, (pw, mw) in (("fernwerk-studio/a", ("3’023", "6")), ("fernwerk-studio/b", ("104", "1"))):
    pv, mv = summe(im(v0, v1, repo))
    assert (f'<dt>Zeilen</dt><dd><span title="Vorwoche zum selben Zeitpunkt: +{pv} / \u2212{mv} Zeilen"><span class="zplus">+{pw}</span> / '
            f'<span class="zminus">\u2212{mw}</span></span></dd>') in s, (repo, pv, mv, re.findall(r'<dt>Zeilen</dt><dd>.*?</dd>', s))
assert summe(im(v0, v1, "fernwerk-studio/a")) == (5, 5) and summe(im(v0, v1, "fernwerk-studio/b")) == (0, 0)
from app.sprache import gilt, t  # noqa: E402
with gilt("en"):
    assert (t("zeilen", "+40,269 / \u22129,504"), web.zeilen_text(1, 0)) == ("+40,269 / \u22129,504 lines", "+1 / \u22120 lines")
with gilt("de"):
    assert (t("zeilen", "+40’269 / \u22129’504"), web.zeilen_text(1, 0)) == ("+40’269 / \u22129’504 Zeilen", "+1 / \u22120 Zeilen")

# Solange der Altbestand im Fenster fehlt: «Zeilen werden nachgeladen» statt einer Zahl, in Kachel und Detail.
with db.verbinden(schreibend=True) as c:
    c.execute("UPDATE commits SET dateien_geladen = NULL WHERE sha = 'a1'"); c.commit()
s2 = web.seite(jetzt=jetzt)
assert '<span class="delta bezug zeilen">Zeilen werden nachgeladen</span>' in s2 and s2.count("Zeilen werden nachgeladen") == 2 \
    and s2.count("<dt>Zeilen</dt><dd>werden nachgeladen</dd>") == 1, "Kachel (Fenster 7 Tage und Woche) und Detail von a"

# Abbruch mitten im Altbestand: 45 offene Commits, nach dem 45. Aufruf ein harter Fehler. Die zwei festgeschriebenen Portionen
# (40 Commits) bleiben, nur die laufende Portion geht verloren; ein 502 des Repos überspringt dessen übrige Commits (Audit 25.09.2026).
with db.verbinden(schreibend=True) as c:
    c.execute("DELETE FROM commits")
    c.executemany("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, eltern) VALUES (?, 'fernwerk-studio/a', ?, ?, 1)",
                  [(f"alt{i:02d}", iso(ju - timedelta(days=1, minutes=i)), ICH) for i in range(45)])
    c.commit()
WELT["fernwerk-studio/a"]["details"].update({f"alt{i:02d}": [datei("x.py", 1, 1)] for i in range(45)})
aufrufe = {"n": 0}
echt_holen = holen
def holen_bricht(pfad, params=None, leer_bei_409=False):
    aufrufe["n"] += 1
    if aufrufe["n"] > 44:
        raise RuntimeError("Prozess abgebrochen")
    return echt_holen(pfad, params)
alt_h = github.holen
github.holen = holen_bricht
try:
    c = db.verbinden(schreibend=True)
    try:
        sammler.zeilen_nachladen(c, jetzt_=ju)
        raise AssertionError("der Abbruch muss durchschlagen")
    except RuntimeError:
        pass
    finally:
        c.close()
finally:
    github.holen = alt_h
with db.verbinden() as c:
    geladen = c.execute("SELECT COUNT(*) FROM commits WHERE dateien_geladen = 1").fetchone()[0]
    zeilen_40 = c.execute("SELECT COUNT(*) FROM commit_dateien WHERE sha LIKE 'alt%'").fetchone()[0]
assert geladen == 40 == zeilen_40, ("zwei Portionen zu 20 festgeschrieben, samt ihren Dateizeilen", geladen, zeilen_40)
WELT["fernwerk-studio/a"]["details"]["alt40"] = fehler(502, "Bad Gateway")
github.holen = holen
try:
    c = db.verbinden(schreibend=True)
    n, rest, _ = sammler.zeilen_nachladen(c, jetzt_=ju)
    c.close()
finally:
    github.holen = alt_h
with db.verbinden() as c:
    fehler_a = c.execute("SELECT fehler FROM repos WHERE repo = 'fernwerk-studio/a'").fetchone()[0]
assert (n, rest, fehler_a) == (0, 5, "502 Bad Gateway"), ("alt40 scheitert mit 502: Repo übersprungen und vermerkt, nichts verloren", n, rest, fehler_a)
# Keine offene Schreibsperre, während geholt wird (25.09.2026): ein zweiter Schreiber mit 0.1 s Wartezeit schreibt mitten im Holen
# einer Portion, ohne «database is locked». Vorher hielt das Nachladen die Sperre, solange eine Portion über das Netz kam.
import sqlite3 as _sq  # noqa: E402
fremd = []
def holen_mit_schreiber(pfad, params=None, leer_bei_409=False):
    k = _sq.connect(db.DB, timeout=0.1)
    k.execute("INSERT OR REPLACE INTO laeufe (gestartet_utc, beendet_utc, neu) VALUES ('2000-01-01T00:00:00Z', '2000-01-01T00:00:01Z', 0)")
    k.commit(); k.close()
    fremd.append(pfad)
    return holen(pfad, params)
WELT["fernwerk-studio/a"]["details"]["alt40"] = [datei("x.py", 1, 1)]
github.holen = holen_mit_schreiber
try:
    c = db.verbinden(schreibend=True)
    n_s, rest_s, _ = sammler.zeilen_nachladen(c, jetzt_=ju)
    c.close()
finally:
    github.holen = alt_h
assert (n_s, rest_s) == (5, 0) and len(fremd) == 5, ("fünf Commits geholt, der zweite Schreiber kam jedes Mal durch", n_s, rest_s, fremd)
# Zeitbudget: mit Frist 0 lädt ein Lauf nichts und scheitert nicht.
c = db.verbinden(schreibend=True)
assert sammler.zeilen_nachladen(c, frist_s=-1, jetzt_=ju) == (0, 0, None)
c.close()

# Handlungsbedarf, Randfälle direkt über befunde_html: 0, nur Zweige, nur roter Actions-Lauf; jeweils ohne Blockfehler im Seitenkopf.
k_ci = {"repo": "fernwerk-studio/x", "name": "x", "branch": "main", "ci": {"workflow": "CI", "alter": "vor 2 Std."}, "warn": None, "ci_zustand": "fehlgeschlagen", "sicher_zustand": "aus"}
k_ok = {"repo": "fernwerk-studio/y", "name": "y", "branch": "main", "ci": None, "warn": None, "ci_zustand": "ok", "sicher_zustand": "ok"}
zweig = ([{"repo": "fernwerk-studio/y", "name": "y"}], {"zweig": "f", "ahead": 1, "behind": 0, "seit": 1, "branch": "main"})
assert web.befunde_html([], [k_ok]) == '<div class="n befunde"><b>0</b><div class="bunter"><span>Kein Handlungsbedarf</span></div></div>'
k_zweig = dict(k_ok, befunde=[zweig[1]])
assert web.befunde_html([zweig], [k_zweig]) == ('<div class="n befunde"><b>1</b><div class="bunter"><ul class="barten">'
                                                 '<li><a href="#handlung-zweige">1 Zweig wartet auf main</a></li></ul></div></div>')
nur_ci = web.befunde_html([], [k_ci, k_ok])
assert nur_ci.startswith('<div class="n befunde"><b>1</b><div class="bunter"><ul class="barten"><li><a href="#handlung-ci">1 Actions-Lauf fehlgeschlagen</a></li></ul>'), nur_ci
assert "Dependabot in keinem Repo" not in nur_ci and "github.com" not in nur_ci, nur_ci
zwei_ci = web.befunde_html([], [k_ci, dict(k_ci, repo="fernwerk-studio/z", name="z")])
assert '<b>2</b><div class="bunter"><ul class="barten"><li><a href="#handlung-ci">2 Actions-Läufe fehlgeschlagen</a></li></ul>' in zwei_ci and "Sicherheitswarnungen: Dependabot in keinem Repo eingeschaltet" in zwei_ci, zwei_ci
# Der Block ordnet die Sicherheitswarnungen nach Stufe, nicht nach Namen: z9 (kritisch) vor a1 (niedrig).
echt_pj = web.projekte
web.projekte = lambda c_, h_, j_: [dict(k_ok, repo=f"fernwerk-studio/{n}", name=n, url=f"https://github.com/fernwerk-studio/{n}", dependabot=f"https://github.com/fernwerk-studio/{n}/security/dependabot",
                                        warnliste=[], befunde=[], warn={"n": 1, "stufe": st}) for n, st in (("a1", "low"), ("z9", "critical"))]
try:
    hbx = web.handlung_block(None, None, datetime.now(timezone.utc))
finally:
    web.projekte = echt_pj
assert hbx.index('>z9</a>') < hbx.index('>a1</a>') and '<span class="hbz hbrest">kritisch, 1 Warnung</span>' in hbx, hbx
# Geltungsbereich je Repo: nur Entwicklung gedämpft ohne Zusatz im Kopf; ein fehlender Geltungsbereich zählt als Laufzeit.
w_ = lambda scope: {"paket": "p", "stufe": "high", "behoben": "1", "url": "https://github.com/fernwerk-studio/x/security/dependabot/1", "nummer": 1, "summary": "s", "scope": scope,
                    "oekosystem": "npm", "manifest": "m", "geoeffnet": "2026-09-25T02:06:15Z", "bereich": "< 1", "ghsa": "GHSA-x", "cve": None}
karte_ = lambda n, scopes: dict(k_ok, repo=f"fernwerk-studio/{n}", name=n, url=f"https://github.com/fernwerk-studio/{n}", dependabot=f"https://github.com/fernwerk-studio/{n}/security/dependabot",
                                warnliste=[w_(sc) for sc in scopes], befunde=[], warn={"n": len(scopes), "stufe": "high"})
web.projekte = lambda c_, h_, j_: [karte_("entw", ["development", "development"]), karte_("ohne", [None])]
try:
    hby = web.handlung_block(None, None, datetime.now(timezone.utc))
finally:
    web.projekte = echt_pj
assert '<span class="hbz hbrest">hoch, 2 Warnungen, nur Entwicklung</span>' in hby and '<span class="hbz hbrest">hoch, 1 Warnung, Laufzeit</span>' in hby, hby
# Sortierung (25.09.2026): Laufzeit vor Entwicklung, dann Stufe; ein Laufzeit-Repo mit niedriger Stufe steht vor einem kritischen,
# das nur die Entwicklung betrifft.
lauf_niedrig = dict(karte_("lauf", ["runtime"]), warn={"n": 1, "stufe": "low"})
entw_krit = dict(karte_("krit", ["development"]), warn={"n": 1, "stufe": "critical"})
web.projekte = lambda c_, h_, j_: [entw_krit, lauf_niedrig]
try:
    hbz = web.handlung_block(None, None, datetime.now(timezone.utc))
finally:
    web.projekte = echt_pj
assert hbz.index('>lauf</a>') < hbz.index('>krit</a>'), "Laufzeit vor Entwicklung"
assert "zur Laufzeit" not in web.befunde_html([], [karte_("entw", ["development"])]) and ", 1 zur Laufzeit</a>" in web.befunde_html([], [karte_("ohne", [None])])
# Seit 04.10.2026 eine Kopfzeile je Art: «2 Repos mit Sicherheitswarnungen, 1 zur Laufzeit», N zählt die Repos mit einer Warnung
# ausserhalb der Entwicklung, nur bei N > 0 (art_text, dieselbe Angabe wie der Spaltentitel im Block).
kz = web.befunde_html([], [karte_("ohne", [None, "runtime", "development"]), karte_("entw", ["development"])])
assert '<ul class="barten"><li><a href="#handlung-warn">2 Repos mit Sicherheitswarnungen, 1 zur Laufzeit</a></li></ul>' in kz, kz
with web.gilt("en"), web.fmt.gilt(web.fmt.waehlen("en")):
    kz_en = web.befunde_html([], [karte_("ohne", ["runtime"])])
assert '<li><a href="#handlung-warn">1 repo with security alerts, 1 at runtime</a></li>' in kz_en, kz_en
# Stress (25.09.2026): 40 Zweige, 30 Repos mit Warnungen, 10 rote Läufe ergeben in der Kopfspalte drei Zeilen, eine je Art, in der
# Reihenfolge der Arten; die Höhe hängt nie an der Zahl der Befunde (wieder seit 04.10.2026).
stress = [dict(k_ok, repo=f"fernwerk-studio/s{i}", name=f"s{i}", befunde=[zweig[1]] * (2 if i < 10 else 1) if i < 30 else [],
               warn={"n": 3, "stufe": "high"} if i < 30 else None, ci=k_ci["ci"] if i < 10 else None) for i in range(30)]
st = web.befunde_html([], stress)
assert st.startswith('<div class="n befunde"><b>80</b><div class="bunter">') and st.count("<li>") == 3, st
assert re.findall(r'<li><a href="([^"]*)">([^<]*)</a></li>', st) == [("#handlung-warn", "30 Repos mit Sicherheitswarnungen"), ("#handlung-ci", "10 Actions-Läufe fehlgeschlagen"),
                                                                    ("#handlung-zweige", "40 Zweige warten auf main")], st
# Migration (25.09.2026): eine Datenbank im alten Schema mit den Spalten des zurückgenommenen Teil C und den zwei Summenspalten verliert
# sie beim nächsten schreibenden Start, die Daten der übrigen Spalten bleiben.
import sqlite3  # noqa: E402
alt_db = Path(tempfile.mkdtemp()) / "alt.db"
k = sqlite3.connect(alt_db)
k.execute("CREATE TABLE commits (sha TEXT PRIMARY KEY, repo TEXT NOT NULL, zeitstempel_utc TEXT NOT NULL, autor_adresse TEXT NOT NULL, autor_login TEXT, betreff TEXT, "
          "eltern INTEGER, herkunft INTEGER NOT NULL DEFAULT 0, zweig TEXT, squash_pr INTEGER, dublette_von TEXT, zeilen_plus INTEGER, zeilen_minus INTEGER) WITHOUT ROWID")
k.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff, eltern, zeilen_plus) VALUES ('m1', 'x/y', '2026-09-20T10:00:00Z', 'a@b', 'Alt', 1, 12)")
k.commit(); k.close()
haupt_db, db.DB = db.DB, alt_db
db.verbinden(schreibend=True).close()
with db.verbinden() as c:
    spalten = [r[1] for r in c.execute("PRAGMA table_info(commits)")]
    zeile_m = dict(c.execute("SELECT * FROM commits").fetchone())
db.DB = haupt_db
assert not set(spalten) & {"herkunft", "zweig", "squash_pr", "dublette_von", "zeilen_plus", "zeilen_minus"} and "dateien_geladen" in spalten, spalten
assert zeile_m["betreff"] == "Alt" and zeile_m["eltern"] == 1 and zeile_m["dateien_geladen"] is None, zeile_m
# Blockisolation: bricht der Block Handlungsbedarf (seit 04.10.2026 über eine Ausnahme im Block selbst, die Obergrenze ist entfallen),
# trägt nur er seine Fehlerzeile, der Rest der Seite steht; bricht der Detailteil der Warnungen, nur der Projektstand des einen Repos.
echt_hb, web.handlung_block = web.handlung_block, lambda *a: 1 / 0
try:
    s_b = web.seite(jetzt=jetzt)
finally:
    web.handlung_block = echt_hb
assert re.findall(r'class="blockfehler glut" data-block="([^"]*)"', s_b) == ["Handlungsbedarf"] and 'class="n befunde"' in s_b and 'class="card projekte"' in s_b
echt_pt, web.paket_title = web.paket_title, lambda w: 1 / 0
try:
    s_b = web.seite(jetzt=jetzt)
finally:
    web.paket_title = echt_pt
assert sorted(set(re.findall(r'class="blockfehler glut" data-block="([^"]*)"', s_b))) == ["Projektstand a"], re.findall(r'data-block="([^"]*)"', s_b)

# Geschlossene Warnungen verschwinden beim nächsten Lauf (25.09.2026): tmp ist behoben, nur axios bleibt. Schaltet jemand Dependabot aus,
# ist die Liste leer. Messvertrag seit 03.10.2026 (Codex-Audit, ersetzt «ohne Messung keine Pakete auf der Seite»): scheitert die Abfrage
# (403 ohne Leserecht, 502, 404), bleibt der letzte Stand in Tabelle UND Seite, mit dem Alter des letzten Erfolgs als eigener Befund
# «nicht gemessen»; nie «Kein Handlungsbedarf». Dasselbe für CI: ein bekannter roter Lauf bleibt rot.
nur = lambda: [tuple(r) for r in db.verbinden().execute("SELECT repo, nummer, paket FROM warnungen ORDER BY repo, nummer")]
pr_a = lambda: dict(db.verbinden().execute("SELECT * FROM pruefungen WHERE repo = 'fernwerk-studio/a'").fetchone())
WELT["fernwerk-studio/a"]["dependabot"] = [warnung("fernwerk-studio/a", 2, "axios", "medium", "11.1.1")]
assert lauf() and nur() == [("fernwerk-studio/a", 2, "axios")], nur()
gemessen_vorher = pr_a()
ci_vorher = WELT["fernwerk-studio/a"]["actions"]
for status, meldung in ((403, "Resource not accessible by personal access token"), (502, "Bad Gateway"), (404, "Not Found")):
    WELT["fernwerk-studio/a"]["dependabot"] = WELT["fernwerk-studio/a"]["actions"] = fehler(status, meldung)
    assert lauf() and nur() == [("fernwerk-studio/a", 2, "axios")], (status, "der letzte Stand bleibt, auch bei 404", nur())
    p_ = pr_a()
    assert (p_["ci"], p_["ci_workflow"], p_["warnungen"], p_["warn_stufe"], p_["ci_gemessen"], p_["sicher_gemessen"]) == \
        ("fehlgeschlagen", "Tests", 1, "medium", gemessen_vorher["ci_gemessen"], gemessen_vorher["sicher_gemessen"]), (status, p_)
    assert p_["ci_fehler"] == p_["sicher_fehler"] == f"{status} {meldung}", (status, p_)
    assert json.loads(sammler.LAUF.read_text())["teilweise"] >= 2, (status, "a und c: der Lauf ist ein Teilerfolg", sammler.LAUF.read_text())
    s_f = web.seite(jetzt=jetzt)
    kopf_f = s_f[s_f.index('<div class="n befunde">'):s_f.index('<div class="tiles3">')]
    eintrag_f = s_f[s_f.index('id="hb-messung-fernwerk-studio_2f_a"'):].split("</li>")[0]
    assert "Kein Handlungsbedarf" not in kopf_f and 'href="#handlung-messung"' in kopf_f and 'id="hb-messung-fernwerk-studio_2f_a"' in s_f and "security/dependabot/2" in s_f and 'id="hb-warn-fernwerk-studio_2f_a"' in s_f \
        and 'id="hb-ci-fernwerk-studio_2f_a"' in s_f, (status, kopf_f[:500])
    assert f'CI: <span translate="no">{status} {meldung}</span> <span class="hbrest">letzter Erfolg ' in eintrag_f \
        and f'Sicherheitswarnungen: <span translate="no">{status} {meldung}</span>' in eintrag_f, (status, eintrag_f)
WELT["fernwerk-studio/a"]["actions"] = ci_vorher
WELT["fernwerk-studio/a"]["dependabot"] = fehler(403, "Dependabot alerts are disabled for this repository.")
assert lauf() and nur() == [], ("Dependabot aus: leer", nur())
assert pr_a()["sicherheit"] == "aus" and pr_a()["sicher_fehler"] is None and pr_a()["ci_fehler"] is None, pr_a()
# Teilerfolg (26.09.2026, Codex-Review): ein Repo, dessen Commit-Abfrage mit 403 scheitert, während die übrigen gelingen. Der Lauf endet mit
# Exit 0, ist aber als Teilerfolg gespeichert (laeufe.teilweise, lauf.json), und die Seite sagt es an drei Stellen: in der Alterszeile, als
# eigene Art im Handlung­sbedarf (Code, Meldung, seit wann) samt Kopfzusammenfassung, und als Befund in der Projektzeile. Die anderen Repos
# laden weiter. Ein Lauf ohne Fehler macht den Befund wieder weg.
# c wird hier wieder lesbar: seit 03.10.2026 zählt auch ein Prüffehler als Teilerfolg, dieser Abschnitt prüft nur den Abruffehler von zu.
WELT["fernwerk-studio/c"]["actions"], WELT["fernwerk-studio/c"]["dependabot"] = {"total_count": 0, "workflow_runs": []}, []
WELT["fernwerk-studio/zu"] = {"commits": fehler(403, "Resource not accessible by integration"), "actions": {"total_count": 0, "workflow_runs": []},
                    "dependabot": [], "details": {}}
WELT["fernwerk-studio/b"]["commits"].append(commit("b2", mo + timedelta(days=3, hours=2), "Neu trotz Sperre nebenan"))
WELT["fernwerk-studio/b"]["details"]["b2"] = [datei("app/neu.py", 4, 1)]
# Zuerst der Weg über die Laufzeile allein: frische Laufzeilen (die Testuhr ist fest, sonst scheitert die Zeile an UNIQUE) und ein Nachladen,
# das scheitert, damit die spätere Aktualisierung nach dem Nachladen den Wert nicht nachträglich setzt.
with db.verbinden(schreibend=True) as c:
    c.execute("DELETE FROM laeufe"); c.commit()
echt_nl = sammler.zeilen_nachladen
sammler.zeilen_nachladen = lambda c, **k: 1 / 0
try:
    assert lauf()
finally:
    sammler.zeilen_nachladen = echt_nl
with db.verbinden() as c:
    assert [tuple(r) for r in c.execute("SELECT teilweise FROM laeufe")] == [(1,)], "Teilerfolg in der Laufzeile selbst"
# Dann der volle Lauf mit Nachladen.
assert lauf()
with db.verbinden() as c:
    teil = c.execute("SELECT teilweise FROM laeufe ORDER BY beendet_utc DESC LIMIT 1").fetchone()[0]
    zu = tuple(c.execute("SELECT fehler, fehler_utc FROM repos WHERE repo = 'fernwerk-studio/zu'").fetchone())
    b2 = c.execute("SELECT COUNT(*) FROM commits WHERE sha = 'b2'").fetchone()[0]
lj = json.loads(sammler.LAUF.read_text())
assert teil == 1 and zu == ("403 Resource not accessible by integration", iso(ju)) and b2 == 1 and lj["exit"] == 0 and lj["teilweise"] == 1, (teil, zu, b2, lj)
for sprache_, stand_, art_, kurz_ in (("de", ", Teilerfolg: 1 Repo nicht abrufbar", "1 Repo nicht abrufbar", "nicht abrufbar, 403"),
                                      ("en", ", partial: 1 repo could not be fetched", "1 repo could not be fetched", "not fetched, 403")):
    s_t = web.seite(jetzt=jetzt, sprache=sprache_, ruhend=True)
    stand_t = re.search(r'<span class="stamp lbl"><span id="laufinfo"></span><span>([^<]*)</span>', s_t).group(1)
    assert stand_t.endswith(stand_), (sprache_, stand_t)
    assert f'<p class="hbart" id="handlung-abruf">{art_}</p><ul class="hbliste"><li id="hb-abruf-fernwerk-studio_2f_zu" title="zu: 403 Resource not accessible by integration, ' in s_t, sprache_
    assert '<span translate="no">403 Resource not accessible by integration</span>' in s_t and f'<li><a href="#handlung-abruf">{art_}</a></li>' in s_t, sprache_
    zeile_zu = s_t[s_t.index('data-repo="fernwerk-studio/zu"'):]
    assert f'<span class="pbefund"><a href="#hb-abruf-fernwerk-studio_2f_zu">{kurz_}</a></span>' in zeile_zu[:zeile_zu.index("</summary>")], (sprache_, zeile_zu[:600])
    assert s_t.count("blockfehler") == 0
WELT["fernwerk-studio/zu"]["commits"] = []
assert lauf()
with db.verbinden() as c:
    assert c.execute("SELECT teilweise FROM laeufe ORDER BY beendet_utc DESC LIMIT 1").fetchone()[0] == 0
    assert c.execute("SELECT fehler FROM repos WHERE repo = 'fernwerk-studio/zu'").fetchone()[0] is None
s_t = web.seite(jetzt=jetzt)
assert "Teilerfolg" not in s_t and 'id="handlung-abruf"' not in s_t and "nicht abrufbar" not in s_t
print("ok: 403 und disabled als Zustand ohne Abbruch; Handlungsbedarf 0, Zweige, CI, gemischt; Lockfiles und Merges ohne Codezeilen;"
      " Commit-Detail über zwei Seiten; Portionen überleben einen Abbruch; «Zeilen werden nachgeladen»; Wochenzeile gegen eigenes SQL")
