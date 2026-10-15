"""Kleinste Prüfung: die Laufzeile ist Buchführung und reisst das Werk nicht mit. python3 -m tests.test_sammler"""
import os
import tempfile

os.environ["GITPULS_DB"] = os.path.join(tempfile.mkdtemp(), "probe.db")   # vor dem Import, db.DB liest die Variable beim Laden
os.environ.setdefault("GITPULS_ADRESSEN", "alt@fernwerk-studio.example,mail@fernwerk-studio.example,dev@fernwerk-studio.example")   # kein Autor-Default mehr im Code (Codex-Befund 20.09.2026)
os.environ.setdefault("GITPULS_SPRACHE", "de")   # die bisherigen Prüfungen laufen auf Deutsch, die Vorgabe ist seit 25.09.2026 en
os.environ["TZ"] = "Europe/Zurich"   # die Zone setzt der Test selbst (10.10.2026, Audit S28): grün unter jeder TZ der Shell
from app import db, sammler  # noqa: E402

c = db.verbinden(schreibend=True)
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES ('a', 'x/y', '2026-09-08T00:00:00Z', 'dev@fernwerk-studio.example')")
c.commit()
# Fünf Spalten wie auf dem VPS seit 4b3d201: die ausdrückliche Spaltenliste trägt trotzdem ein
c.execute("ALTER TABLE laeufe ADD COLUMN zustand TEXT")
c.execute("ALTER TABLE laeufe ADD COLUMN token_bis TEXT")
sammler.laufzeile(c, "2026-09-08T10:00:00Z", 1)
assert c.execute("SELECT neu FROM laeufe").fetchone()[0] == 1
# Absichtlich kaputte Buchführung: kein Abbruch, die Commits bleiben
c.execute("CREATE TRIGGER kaputt BEFORE INSERT ON laeufe BEGIN SELECT RAISE(ABORT, 'absichtlich kaputt'); END")
sammler.laufzeile(c, "2026-09-08T11:00:00Z", 0)
assert c.execute("SELECT COUNT(*) FROM laeufe").fetchone()[0] == 1
assert c.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 1
c.execute("DROP TRIGGER kaputt"); c.commit()

# Integration Karenz über den Sammlerpfad (tests/sammlerprobe.py). Ein weiterhin gelistetes Repo ohne Push über
# mehr als 24 Stunden und fünf Läufe bleibt sichtbar (Karte oder Ruhezeile) und in der Zählzeile; ein nicht mehr
# gelistetes verschwindet nach 24 Stunden; ein abgebrochener Lauf setzt die Stempel für alle gelisteten Repos in
# einem Schritt und verschiebt die Grenze nicht, weil die Karenz gegen den letzten erfolgreichen Lauf rechnet.
from datetime import datetime, timedelta, timezone  # noqa: E402
from app import web  # noqa: E402
from tests.sammlerprobe import lauf  # noqa: E402

t0 = datetime.now(timezone.utc)
z = lambda h: (t0 - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")   # fest ab t0, sonst wandert die Grenze um Sekunden
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES ('r', 'x/ruhig', ?, 'dev@fernwerk-studio.example')", (z(20 * 24),))
c.commit(); c.close()
assert lauf(z(30), ["x/ruhig", "x/weg", "x/y"])
s = web.seite()
assert "x/ruhig" in s and "x/weg" in s and s.count("<details class=\"prow") == 3
for h in (24, 18, 12, 6):
    assert lauf(z(h), ["x/ruhig", "x/y"])
    s = web.seite()
    assert "x/ruhig" in s, f"ruhig ohne Push nach {30 - h} Stunden und Läufen weg"
    assert ("x/weg" in s) == (h >= 6), f"weg fehlt seit {30 - h} Stunden: sichtbar, solange gesehen nicht älter als Grenze ist (genau 24 h: noch da)"
assert lauf(z(1), ["x/ruhig", "x/y"])
s = web.seite()
tage_ruhig = (datetime.now(web.TZ).date() - datetime.fromisoformat(z(20 * 24).replace("Z", "+00:00")).astimezone(web.TZ).date()).days
assert '<details class="prow gedaempft" data-repo="x/ruhig" id="p-x_2f_ruhig">' in s and f'<span class="palter">vor {tage_ruhig} Tagen, ruht</span>' in s and "x/weg" not in s and s.count("<details class=\"prow") == 2
with db.verbinden() as c2:
    assert dict(c2.execute("SELECT repo, gesehen FROM repos")) == {"x/ruhig": z(1), "x/y": z(1), "x/weg": z(30)}
    assert web.karenzgrenze(c2) == z(25)
# Abgebrochener Lauf: Zweigabruf von x/a scheitert. gesehen von a UND b tragen den neuen Stempel, die Grenze bleibt
# beim letzten erfolgreichen Lauf; kein gelistetes Repo fällt weg. Ein erfolgreicher Lauf 26 Stunden später räumt weg.
assert lauf(z(60), ["x/a", "x/b", "x/weg2"])
assert not lauf(z(0.5), ["x/a", "x/b"], fehler_bei="x/a")
s = web.seite()
with db.verbinden() as c2:
    st = dict(c2.execute("SELECT repo, gesehen FROM repos"))
    assert st["x/a"] == st["x/b"] == z(0.5), st
    assert web.karenzgrenze(c2) == z(25), web.karenzgrenze(c2)
assert "x/a" in s and "x/b" in s, "abgebrochener Lauf lässt kein gelistetes Repo verschwinden"
assert lauf(z(0.1), ["x/a", "x/b"])
s = web.seite()
assert "x/a" in s and "x/b" in s and "x/weg2" not in s and "x/weg" not in s
# Paginierung wie app/github.py::alle_seiten. (a) Erfolgreicher Lauf über zwei Seiten: alle Repos beider Seiten
# tragen den Laufzeitpunkt, genau eine laeufe-Zeile kommt dazu. (b) GitHubFehler nach Seite 1: kein Stempel
# verändert, keine laeufe-Zeile, eine neue Verbindung schreibt sofort, und die Repos der ersten Seite stehen
# unverändert auf der Seite, keines als verschwunden.
with db.verbinden() as c2:
    laeufe_vorher = c2.execute("SELECT COUNT(*) FROM laeufe").fetchone()[0]
assert lauf(z(0.05), ["x/a", "x/b", "x/c", "x/d"], seiten=2)
with db.verbinden() as c2:
    st = dict(c2.execute("SELECT repo, gesehen FROM repos"))
    assert all(st[r] == z(0.05) for r in ("x/a", "x/b", "x/c", "x/d")), st
    assert c2.execute("SELECT COUNT(*) FROM laeufe").fetchone()[0] == laeufe_vorher + 1
assert not lauf(z(0.02), ["x/a", "x/b", "x/c", "x/d"], seiten=2, fehler_nach_seite=1), "Abbruch nach Seite 1 ist kein Erfolg"
with db.verbinden() as c2:
    assert dict(c2.execute("SELECT repo, gesehen FROM repos")) == st, "kein Stempel verändert"
    assert c2.execute("SELECT COUNT(*) FROM laeufe").fetchone()[0] == laeufe_vorher + 1, "keine laeufe-Zeile"
    assert web.karenzgrenze(c2) == (datetime.strptime(z(0.05), "%Y-%m-%dT%H:%M:%SZ") - timedelta(hours=web.KARENZ_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
c = db.verbinden(schreibend=True)   # die abgebrochene Verbindung hält keine Sperre mehr
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES ('w', 'x/a', ?, 'dev@fernwerk-studio.example')", (z(2),))
c.commit(); c.close()
s = web.seite()
assert all(f"github.com/x/{r}" in s for r in "abcd") and "x/weg" not in s, "erste Seite unverändert sichtbar, keines verschwunden"
# Umbenennung auf GitHub: dieselbe ID unter neuem Namen. Danach eine Karte unter dem neuen Namen mit voller Historie,
# Commit-Zahl und Kachel-Repozahl unverändert, nach mehr als 24 Stunden nichts verschwunden.
c = db.verbinden(schreibend=True)
for sha, h in (("u1", 20 * 24), ("u2", 9 * 24), ("u3", 2)):
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES (?, 'x/alt', ?, 'dev@fernwerk-studio.example')", (sha, z(h)))
c.execute("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc) VALUES ('x/alt', 'wip', 1, 0, ?)", (z(3),))
c.commit(); c.close()
assert lauf(z(0.09), ["x/a", "x/b", "x/alt"], ids={"x/alt": 4242})
with db.verbinden() as c2:
    vorher_n = c2.execute("SELECT COUNT(*) FROM commits WHERE repo = 'x/alt'").fetchone()[0]
    jz = datetime.now(web.TZ)
    vorher_kachel = web.kacheldaten(c2, jz.date(), jz)[2]["zeile2"]
    vorher_repos = c2.execute("SELECT COUNT(*) FROM repos WHERE gesehen >= ?", (web.karenzgrenze(c2),)).fetchone()[0]
s = web.seite()
assert "github.com/x/alt" in s and vorher_n == 3
assert lauf(z(0.08), ["x/a", "x/b", "x/neu"], ids={"x/neu": 4242})
with db.verbinden() as c2:
    assert c2.execute("SELECT COUNT(*) FROM commits WHERE repo = 'x/neu'").fetchone()[0] == 3 and c2.execute("SELECT COUNT(*) FROM commits WHERE repo = 'x/alt'").fetchone()[0] == 0
    assert dict(c2.execute("SELECT repo, id FROM repos WHERE id = 4242")) == {"x/neu": 4242}
    # zweige räumt der Lauf für jedes gelistete Repo ab und holt sie neu (das nachgebaute GitHub hat keine): den
    # Umzug der Zweige belegt umbenennen() direkt
c = db.verbinden(schreibend=True)
c.execute("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc) VALUES ('y/alt', 'wip', 1, 0, ?)", (z(3),))
c.execute("INSERT INTO repos (repo, default_branch, sprache, pushed_at, gesehen, id) VALUES ('y/alt', 'main', NULL, NULL, ?, 7)", (z(3),))
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES ('y1', 'y/alt', ?, 'dev@fernwerk-studio.example')", (z(3),))
sammler.umbenennen(c, "y/alt", "y/neu")
assert c.execute("SELECT repo FROM zweige WHERE name = 'wip'").fetchone()[0] == "y/neu"
assert tuple(c.execute("SELECT repo, id FROM repos WHERE id = 7").fetchone()) == ("y/neu", 7) and c.execute("SELECT repo FROM commits WHERE sha = 'y1'").fetchone()[0] == "y/neu"
c.execute("DELETE FROM zweige"); c.execute("DELETE FROM repos WHERE repo = 'y/neu'"); c.execute("DELETE FROM commits WHERE sha = 'y1'"); c.commit(); c.close()
with db.verbinden() as c2:
    assert web.kacheldaten(c2, jz.date(), jz)[2]["zeile2"] == vorher_kachel, "Kachel-Repozahl unverändert"
    assert c2.execute("SELECT COUNT(*) FROM repos WHERE gesehen >= ?", (web.karenzgrenze(c2),)).fetchone()[0] == vorher_repos, "Zählung unverändert"
s = web.seite()
zeile = s[s.index('data-repo="x/neu"'):]
zeile = zeile[:zeile.index("</details>")]
assert "x/alt" not in s and 'class="chart mini"' in zeile and "Achse x" not in zeile, "Projektzeile unter neuem Namen mit Kurve im Detail"
assert zeile.count("<li>") >= 3, "die Historie steht unter dem neuen Namen im Detail der Zeile"
assert lauf(z(-26), ["x/a", "x/b", "x/neu"], ids={"x/neu": 4242})   # 26 h später (Laufzeit in der Zukunft): der alte Name wäre jenseits der Karenz
s = web.seite()
assert "github.com/x/neu" in s and "x/alt" not in s and s.count('<details class="prow') >= 1
with db.verbinden() as c2:
    assert c2.execute("SELECT COUNT(*) FROM commits WHERE repo = 'x/neu'").fetchone()[0] == 3
# Bestand ohne ID (Datenbank vor dieser Spalte, wie der VPS am 11.09.2026 mit 48 Commits unter fernwerk-studio/wochenlog
# und 23 unter fernwerk-studio/trainingslog): der alte Name ist nicht mehr gelistet, GitHub leitet ihn auf den neuen weiter, die
# ID passt zum gelisteten Repo, der Sammler führt beide Zeilen zusammen.
c = db.verbinden(schreibend=True)
c.execute("INSERT INTO repos (repo, default_branch, sprache, pushed_at, gesehen, id) VALUES ('x/vorher', 'main', NULL, NULL, ?, NULL)", (z(5),))
c.execute("INSERT INTO repos (repo, default_branch, sprache, pushed_at, gesehen, id) VALUES ('x/nachher', 'main', NULL, NULL, ?, NULL)", (z(4),))
for sha, h, repo in (("v1", 30 * 24, "x/vorher"), ("v2", 8 * 24, "x/vorher"), ("n1", 1, "x/nachher")):
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES (?, ?, ?, 'dev@fernwerk-studio.example')", (sha, repo, z(h)))
c.commit(); c.close()
assert lauf(z(-27), ["x/a", "x/b", "x/neu", "x/nachher"], ids={"x/neu": 4242, "x/nachher": 777}, weiterleitung={"x/vorher": ("x/nachher", 777)})
with db.verbinden() as c2:
    assert c2.execute("SELECT COUNT(*) FROM commits WHERE repo = 'x/nachher'").fetchone()[0] == 3 and c2.execute("SELECT COUNT(*) FROM commits WHERE repo = 'x/vorher'").fetchone()[0] == 0
    assert c2.execute("SELECT COUNT(*) FROM repos WHERE repo IN ('x/vorher', 'x/nachher')").fetchone()[0] == 1 and c2.execute("SELECT id FROM repos WHERE repo = 'x/nachher'").fetchone()[0] == 777
s = web.seite()
assert "github.com/x/nachher" in s and "x/vorher" not in s
# Zahl der Eltern (25.09.2026): der Sammler schreibt sie aus der Listenantwort und trägt sie bei einem erneuten Lauf für bekannte
# Commits nach (Altbestand über --alles). Ein Merge hat zwei.
from app import github  # noqa: E402
liste_e = [{"sha": "e1", "commit": {"author": {"date": z(2), "email": "dev@fernwerk-studio.example"}, "message": "Arbeit"}, "author": None, "parents": [{"sha": "p"}]},
           {"sha": "e2", "commit": {"author": {"date": z(1), "email": "dev@fernwerk-studio.example"}, "message": "Merge branch x"}, "author": None, "parents": [{"sha": "p"}, {"sha": "q"}]}]
def alle_e(pfad, params=None, leer_bei_409=False):
    if pfad == "user/repos":
        return iter([{"full_name": "x/e", "id": 77, "archived": False, "pushed_at": z(0), "default_branch": "main", "language": "Python"}])
    return iter(liste_e if pfad == "repos/x/e/commits" else [])
c = db.verbinden(schreibend=True); c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES ('e1', 'x/e', ?, 'dev@fernwerk-studio.example', 'Arbeit')", (z(2),)); c.commit(); c.close()
def holen_e(pfad, params=None, leer_bei_409=False):   # kein Actions-Lauf, kein Commit-Detail: 404 betrifft nur das Repo, ein Fehler ohne Status beendete den Lauf
    e = github.GitHubFehler(f"nachgebaut: {pfad} antwortet 404")
    e.status, e.global_ = 404, False
    raise e
alt_e = github.alle_seiten, github.holen, sammler.jetzt
github.alle_seiten, github.holen, sammler.jetzt = alle_e, holen_e, lambda: z(0)
try:
    sammler.main(alles=True)
finally:
    github.alle_seiten, github.holen, sammler.jetzt = alt_e
with db.verbinden() as c2:
    assert dict(c2.execute("SELECT sha, eltern FROM commits WHERE repo = 'x/e'")) == {"e1": 1, "e2": 2}, "Eltern geschrieben, e1 nachgetragen"
    assert [r[1] for r in c2.execute("PRAGMA table_info(commits)")].count("eltern") == 1
# Ein Schreiber zur Zeit (25.09.2026, Timer-Lauf um 08:00 neben einem Lauf per Knopf): ein zweiter Prozess nimmt die Sperre und hält
# eine Schreibtransaktion 7 s offen, länger als der busy timeout von 5 s. Der Sammler wartet an der Sperre und läuft danach durch;
# ohne die Sperre scheiterte er wie live mit «database is locked».
import subprocess, sys, time  # noqa: E401,E402
halter = subprocess.Popen([sys.executable, "-c", "import sqlite3, time\nfrom app import db\nwith db.schreibsperre():\n"
                           "    c = sqlite3.connect(db.DB); c.execute('BEGIN IMMEDIATE'); print('hält', flush=True); time.sleep(7); c.commit()"],
                          stdout=subprocess.PIPE, text=True)
assert halter.stdout.readline().strip() == "hält"
t_w = time.monotonic()
assert lauf(z(0), ["x/ruhig", "x/y"]), "der Sammler wartet und läuft durch"
gewartet = time.monotonic() - t_w
assert halter.wait() == 0 and gewartet >= 5, ("an der Sperre gewartet", gewartet)
print("ok: Spaltenliste auf fünf Spalten, kaputte Laufzeile reisst nichts mit; Karenz über den Sammlerpfad: ohne Push sichtbar, nicht gelistet nach 24 h weg, abgebrochener Lauf verschiebt die Grenze nicht; Paginierung: zwei Seiten gestempelt, Abbruch nach Seite 1 ohne Stempel und ohne laeufe-Zeile; zweiter Schreiber wartet an der Sperre")
