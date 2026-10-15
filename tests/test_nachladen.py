"""Audit-Befunde vom 25.09.2026 über den echten Sammlerpfad mit nachgebautem GitHub.
(a) Ein Merge im Altbestand (eltern NULL, 120 Tage alt) bekommt seine Elternzahl beim Nachladen und zählt danach nicht mehr; ein
    nicht abrufbarer bleibt NULL, zählt weiter und steht einmal im title des Rückblicks.
(b) Ein 403 eines Repos überspringt nur dieses Repo, das andere lädt im selben Lauf vollständig; das Ratenlimit beendet den Lauf.
(c) 404 beim Commit-Detail: «nicht verfügbar» mit Versuchen und Zeitpunkt, nie geladen; Wiederholung nach 1 Stunde, 1 Tag, 1 Woche,
    dann dauerhaft; Erholung, sobald der Commit wieder da ist; die Wochenzeile nennt ihn im title und rechnet ihn nicht als offen.
(d) Die einmalige Nachprüfung alter Leer-Commits bei der Migration.
python3 -m tests.test_nachladen"""
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["GITPULS_DB"] = os.path.join(tempfile.mkdtemp(), "nachladen.db")
os.environ.setdefault("GITPULS_ADRESSEN", "dev@fernwerk-studio.example")
os.environ.setdefault("GITPULS_SPRACHE", "de")   # die bisherigen Prüfungen laufen auf Deutsch, die Vorgabe ist seit 25.09.2026 en
os.environ["GITPULS_FORMAT"] = "de-CH"   # Schreibweisen wie die eigene Instanz (26.09.2026); die übrigen Locales prüft tests/test_format.py
os.environ["TZ"] = "Europe/Zurich"   # die Zone setzt der Test selbst (10.10.2026, Audit S28): grün unter jeder TZ der Shell
from app import db, github, sammler, web  # noqa: E402

TZ, ICH = web.TZ, "dev@fernwerk-studio.example"
jetzt = datetime(2026, 9, 24, 18, 0, tzinfo=TZ)                   # Donnerstag
ju = jetzt.astimezone(timezone.utc)
iso = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fehler(status, meldung):
    e = github.GitHubFehler(f"GitHub {status} {meldung}")
    e.status, e.meldung, e.global_ = status, meldung, status == 401 or "rate limit" in meldung.lower()
    return e


def commit(sha, zeit, eltern=1):
    return {"sha": sha, "commit": {"author": {"date": iso(zeit), "email": ICH}, "message": "Arbeit"}, "author": None, "parents": [{"sha": "p"}] * eltern}


# Je Repo: Commit-Liste (oder Fehler) und Commit-Details sha -> (Dateien, Eltern) oder Fehler.
WELT, GEHOLT = {}, []


def alle_seiten(pfad, params=None, leer_bei_409=False):
    if pfad == "user/repos":
        return iter([{"full_name": n, "id": i + 1, "archived": False, "pushed_at": iso(ju), "default_branch": "main", "language": "Python"} for i, n in enumerate(WELT)])
    _, b, n, art, *_ = pfad.split("/")
    if art == "commits":
        liste = WELT[f"{b}/{n}"]["liste"]
        if isinstance(liste, BaseException):
            raise liste
        return iter(liste)
    return iter([])


def holen(pfad, params=None, leer_bei_409=False):
    _, b, n, art, *rest = pfad.split("/")
    if art == "actions":
        return {"workflow_runs": []}, {}
    if art == "dependabot":
        return [], {}
    GEHOLT.append(rest[0])
    w = WELT.get(f"{b}/{n}")
    d = (w or {}).get("details", {}).get(rest[0], fehler(404, "Not Found"))
    if isinstance(d, BaseException):
        raise d
    dateien, eltern = d
    return {"files": [{"filename": p, "additions": a, "deletions": m} for p, a, m in dateien], "parents": [{"sha": "p"}] * eltern}, {}


def lauf(zeit=ju):
    alt = github.alle_seiten, github.holen, sammler.jetzt
    github.alle_seiten, github.holen, sammler.jetzt = alle_seiten, holen, lambda: iso(zeit)
    try:
        sammler.main()
        return True
    except SystemExit:
        return False
    finally:
        github.alle_seiten, github.holen, sammler.jetzt = alt


def nachladen(zeit):
    alt = github.holen
    github.holen = holen
    try:
        c = db.verbinden(schreibend=True)
        try:
            return sammler.zeilen_nachladen(c, jetzt_=zeit)[:2]   # (geladen, offen); der Fehlertext steht in test_audit o1
        finally:
            c.close()
    finally:
        github.holen = alt


def zeile(sha):
    with db.verbinden() as c:
        r = c.execute("SELECT eltern, dateien_geladen, dateien_versuche, dateien_versucht, (SELECT COUNT(*) FROM commit_dateien d WHERE d.sha = commits.sha) n "
                      "FROM commits WHERE sha = ?", (sha,)).fetchone()
        return tuple(r) if r else None


def gezaehlt():
    with db.verbinden() as c:
        return {r[0] for r in c.execute(f"SELECT sha FROM commits WHERE {web.IN_ADRESSEN}", web.ADRESSEN)}


# (a) Altbestand: ein Merge und ein Einzelcommit von vor 120 Tagen ohne Elternzahl, dazu einer aus einem gelöschten Repo.
alt = iso(ju - timedelta(days=120))
with db.verbinden(schreibend=True) as c:
    c.executemany("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES (?,?,?,?)",
                  [("merge_alt", "fernwerk-studio/a", alt, ICH), ("einzel_alt", "fernwerk-studio/a", alt, ICH), ("weg_alt", "fernwerk-studio/weg", alt, ICH)])
    c.commit()
WELT.update({"fernwerk-studio/a": {"liste": [], "details": {"merge_alt": ([("x.py", 5, 1)], 2), "einzel_alt": ([("y.py", 3, 0)], 1)}},
             "fernwerk-studio/b": {"liste": [], "details": {}}})
assert gezaehlt() >= {"merge_alt", "einzel_alt", "weg_alt"}, "vorher zählt der Merge mit NULL wie ein Einzelcommit"
assert lauf(), "ein normaler Lauf ohne --alles"
assert zeile("merge_alt")[:2] == (2, 1) and zeile("einzel_alt")[:2] == (1, 1), (zeile("merge_alt"), zeile("einzel_alt"))
assert "merge_alt" not in gezaehlt() and "einzel_alt" in gezaehlt(), "nach dem Nachladen zählt der alte Merge nicht mehr"
assert zeile("weg_alt")[:3] == (None, 0, 1) and "weg_alt" in gezaehlt(), ("nicht abrufbar: eltern bleibt NULL, zählt weiter", zeile("weg_alt"))
with db.verbinden() as c:
    rb = web.rueckblick_block(c, jetzt.date(), jetzt, "30")
assert rb.count("ohne Elternzahl") == 1 and 'title="1 Commit ohne Elternzahl, als Einzelcommit gezählt"' in rb, rb[:400]

# (b) 403 je Repo: a lehnt die Commit-Liste mit 403 ab, b wird im selben Lauf vollständig geladen, samt Commit-Details.
WELT["fernwerk-studio/a"]["liste"] = fehler(403, "Resource not accessible by personal access token")
WELT["fernwerk-studio/b"] = {"liste": [commit("b1", ju - timedelta(hours=5)), commit("b2", ju - timedelta(hours=4))],
                   "details": {"b1": ([("b.py", 4, 1)], 1), "b2": ([("c.py", 2, 2)], 1)}}
laeufe = lambda: sqlite3.connect(db.DB).execute("SELECT COUNT(*) FROM laeufe").fetchone()[0]
vorher = laeufe()
assert lauf(ju + timedelta(minutes=1)), "403 eines Repos beendet den Lauf nicht"
with db.verbinden() as c:
    fehler_je = dict(c.execute("SELECT repo, fehler FROM repos"))
assert zeile("b1")[1:] == (1, None, None, 1) and zeile("b2")[1:] == (1, None, None, 1), (zeile("b1"), zeile("b2"))
assert fehler_je == {"fernwerk-studio/a": "403 Resource not accessible by personal access token", "fernwerk-studio/b": None}, fehler_je
assert laeufe() == vorher + 1, "der Lauf gilt als erfolgreich"
# Im Nachladen: ein offener Commit von a scheitert mit 403, die übrigen Commits von a werden übersprungen, b lädt weiter.
with db.verbinden(schreibend=True) as c:
    c.executemany("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, eltern) VALUES (?,?,?,?,1)",
                  [("a5", "fernwerk-studio/a", iso(ju - timedelta(days=1)), ICH), ("a6", "fernwerk-studio/a", iso(ju - timedelta(days=2)), ICH),
                   ("b3", "fernwerk-studio/b", iso(ju - timedelta(days=3)), ICH)])
    c.commit()
WELT["fernwerk-studio/a"]["details"]["a5"] = fehler(403, "Resource not accessible by personal access token")
WELT["fernwerk-studio/b"]["details"]["b3"] = ([("d.py", 1, 0)], 1)
GEHOLT.clear()
nachladen(ju + timedelta(minutes=2))
assert GEHOLT == ["a5", "b3"] and zeile("b3")[1] == 1 and zeile("a5")[1] is None and zeile("a6")[1] is None, (GEHOLT, zeile("a5"), zeile("b3"))
# Erholung: a liefert wieder, der Vermerk verschwindet.
WELT["fernwerk-studio/a"]["liste"], WELT["fernwerk-studio/a"]["details"]["a5"], WELT["fernwerk-studio/a"]["details"]["a6"] = [], ([("e.py", 1, 1)], 1), ([("f.py", 1, 1)], 1)
assert lauf(ju + timedelta(minutes=3))
with db.verbinden() as c:
    assert tuple(c.execute("SELECT fehler, fehler_utc FROM repos WHERE repo = 'fernwerk-studio/a'").fetchone()) == (None, None)
assert zeile("a5")[1] == 1 and zeile("a6")[1] == 1
# Ratenlimit: beendet den Lauf, ohne Laufzeile, auch wenn es wie ein 403 aussieht.
WELT["fernwerk-studio/b"]["liste"] = fehler(403, "API rate limit exceeded for user ID 1.")
vorher = laeufe()
assert not lauf(ju + timedelta(minutes=4)), "Ratenlimit beendet den Lauf"
assert laeufe() == vorher
WELT["fernwerk-studio/b"]["liste"] = []
# Ratenlimit im Nachladen: beendet das Nachladen, auch für die Repos danach.
with db.verbinden(schreibend=True) as c:
    c.executemany("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, eltern) VALUES (?,?,?,?,1)",
                  [("r1", "fernwerk-studio/a", iso(ju - timedelta(hours=1)), ICH), ("r2", "fernwerk-studio/b", iso(ju - timedelta(hours=2)), ICH)])
    c.commit()
WELT["fernwerk-studio/a"]["details"]["r1"] = fehler(429, "You have exceeded a secondary rate limit.")
WELT["fernwerk-studio/b"]["details"]["r2"] = ([("g.py", 1, 0)], 1)
GEHOLT.clear()
assert nachladen(ju + timedelta(minutes=5)) == (0, 2) and GEHOLT == ["r1"], GEHOLT
WELT["fernwerk-studio/a"]["details"]["r1"] = ([("h.py", 1, 0)], 1)
nachladen(ju + timedelta(minutes=6))

# (c) 404 beim Commit-Detail: nicht verfügbar, nie geladen, Wiederholung nach 1 Stunde, 1 Tag, 1 Woche, danach dauerhaft.
mi = datetime(2026, 9, 23, 10, 0, tzinfo=TZ)                     # Mittwoch dieser Woche
with db.verbinden(schreibend=True) as c:
    c.executemany("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, eltern) VALUES (?,?,?,?,1)",
                  [("n1", "fernwerk-studio/b", iso(mi), ICH), ("n2", "fernwerk-studio/b", iso(mi + timedelta(hours=1)), ICH)])
    c.commit()
WELT["fernwerk-studio/b"]["details"].update({"n1": fehler(404, "Not Found"), "n2": fehler(422, "No commit found for SHA: n2")})
t = ju
nachladen(t)
assert zeile("n1") == (1, 0, 1, iso(t), 0) and zeile("n2") == (1, 0, 1, iso(t), 0), (zeile("n1"), zeile("n2"))
s = web.seite(jetzt=jetzt)
tz = s[s.index('<span class="delta bezug zeilen"'):].split("</span> Zeilen</span>")[0]
assert "2 Commits ohne Zeilenangabe" in tz and "nachgeladen" not in tz, ("nicht verfügbar ist nicht offen und nicht in der Summe", tz)
assert tz.endswith('><span class="zplus">+11</span> / <span class="zminus">\u22125'), tz   # diese Woche b1 4/1, b2 2/2, a5 1/1, a6 1/1, b3 1/0, r1 1/0, r2 1/0; n1 und n2 ohne
def schritt(zeit, geholt, versuche):
    GEHOLT.clear()
    nachladen(zeit)
    assert ("n1" in GEHOLT) == geholt and zeile("n1")[1:3] == (0, versuche) and zeile("n1")[4] == 0, (zeit, GEHOLT, zeile("n1"))
    assert zeile("n1")[3] == iso(zeit if geholt else zeile_zeit[0]), zeile("n1")
    if geholt:
        zeile_zeit[0] = zeit
zeile_zeit = [t]
schritt(t + timedelta(minutes=59), False, 1)
assert "n2" not in GEHOLT, "vor Ablauf der Stunde nicht wiederholt"
WELT["fernwerk-studio/b"]["details"]["n2"] = ([("n.py", 6, 0)], 1)          # n2 ist wieder erreichbar
t2 = t + timedelta(hours=1)
schritt(t2, True, 2)
schritt(t2 + timedelta(hours=23), False, 2)
t3 = t2 + timedelta(days=1)
schritt(t3, True, 3)
schritt(t3 + timedelta(days=6), False, 3)
t4 = t3 + timedelta(days=7)
schritt(t4, True, 4)
t = t4
assert zeile("n2") == (1, 1, None, None, 1), ("Erholung: geladen, Versuche gelöscht", zeile("n2"))
GEHOLT.clear()
nachladen(t + timedelta(days=30))   # im 90-Tage-Fenster, sonst prüfte das nichts
assert "n1" not in GEHOLT and zeile("n1")[1:3] == (0, 4), "nach der Woche dauerhaft ohne Zeilen"

# (d) Migration: eine Datenbank ohne dateien_versuche, mit einem Leer-Commit (geladen, keine Dateizeile) und einem mit Dateien. Der leere
# wird einmal neu geprüft (NULL), der andere bleibt; ein zweiter Start setzt einen inzwischen leer geladenen nicht erneut zurück.
alt_db = Path(tempfile.mkdtemp()) / "alt.db"
k = sqlite3.connect(alt_db)
k.execute("CREATE TABLE commits (sha TEXT PRIMARY KEY, repo TEXT NOT NULL, zeitstempel_utc TEXT NOT NULL, autor_adresse TEXT NOT NULL, autor_login TEXT, betreff TEXT, "
          "eltern INTEGER, dateien_geladen INTEGER) WITHOUT ROWID")
k.execute("CREATE TABLE commit_dateien (sha TEXT NOT NULL, pfad TEXT NOT NULL, plus INTEGER NOT NULL, minus INTEGER NOT NULL, PRIMARY KEY (sha, pfad)) WITHOUT ROWID")
k.executemany("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, eltern, dateien_geladen) VALUES (?, 'x/y', '2026-09-20T10:00:00Z', 'a@b', 1, 1)", [("leer",), ("voll",)])
k.execute("INSERT INTO commit_dateien (sha, pfad, plus, minus) VALUES ('voll', 'a.py', 1, 1)")
k.commit(); k.close()
haupt_db, db.DB = db.DB, alt_db
db.verbinden(schreibend=True).close()
with db.verbinden() as c:
    nach_1 = dict(c.execute("SELECT sha, dateien_geladen FROM commits"))
with db.verbinden(schreibend=True) as c:
    c.execute("UPDATE commits SET dateien_geladen = 1 WHERE sha = 'leer'"); c.commit()
db.verbinden(schreibend=True).close()
with db.verbinden() as c:
    nach_2 = dict(c.execute("SELECT sha, dateien_geladen FROM commits"))
db.DB = haupt_db
assert nach_1 == {"leer": None, "voll": 1} and nach_2 == {"leer": 1, "voll": 1}, (nach_1, nach_2)
# Einstufung in github.holen selbst, mit nachgebautem urlopen: nur Netz, 401 und Ratenlimit sind global.
import io  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from email.message import Message  # noqa: E402
def http(code, meldung, rest=None):
    kopf = Message()
    if rest is not None:
        kopf["X-RateLimit-Remaining"] = rest
    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "x", kopf, io.BytesIO(('{"message": "%s"}' % meldung).encode()))
    return urlopen
def einstufung(urlopen):
    alt_u, alt_t = urllib.request.urlopen, os.environ.get("GITHUB_TOKEN")
    urllib.request.urlopen, os.environ["GITHUB_TOKEN"] = urlopen, "t"
    try:
        github.holen("repos/x/y/commits")
    except github.GitHubFehler as e:
        return e.status, e.global_
    finally:
        urllib.request.urlopen = alt_u
        if alt_t is None:
            del os.environ["GITHUB_TOKEN"]
def netz(req, timeout=None):
    raise urllib.error.URLError("keine Verbindung")
assert [einstufung(u) for u in (http(403, "Resource not accessible by personal access token"), http(403, "API rate limit exceeded for user"),
                                http(403, "Forbidden", "0"), http(429, "Too many"), http(401, "Bad credentials"), http(404, "Not Found"),
                                http(451, "Repository access blocked"), http(422, "No commit found"), netz)] == \
    [(403, False), (403, True), (403, True), (429, True), (401, True), (404, False), (451, False), (422, False), (None, True)]
print("ok: alter Merge nach dem Nachladen ausgeschlossen, nicht abrufbarer bleibt NULL und steht im title; 403 überspringt nur das Repo,"
      " Ratenlimit beendet Lauf und Nachladen; 404/422 nicht verfügbar mit Abständen 1 h, 1 Tag, 1 Woche, Erholung; Leer-Commits einmal neu geprüft")
