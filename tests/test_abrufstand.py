"""Abrufstand je Repo und Zweigköpfe (03.10.2026, Codex-Audit und Sammellauf) über den echten Sammlerpfad mit nachgebautem GitHub.
Jeder Fall war vorher ein Fehler: ein Zukunftscommit legte den Abruf aller Repos still, ein erstmals sichtbares älteres Repo und ein
neuer Default-Branch wurden nie ganz gelesen, ein Zweigkopf mit Zukunftsdatum hielt die Zweigfrist auf.
python3 -m tests.test_abrufstand"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["GITPULS_DB"] = os.path.join(tempfile.mkdtemp(), "abruf.db")
os.environ.setdefault("GITPULS_ADRESSEN", "dev@fernwerk-studio.example")
os.environ["TZ"] = "Europe/Zurich"   # die Zone setzt der Test selbst (10.10.2026, Audit S28): grün unter jeder TZ der Shell
from app import db, github, sammler  # noqa: E402

iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
UHR = [datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)]
WELT, ABRUFE, KOEPFE = {}, [], []


def repo(name, pushed, commits=(), zweige=(), branch="main"):
    WELT[name] = {"default_branch": branch, "pushed_at": iso(pushed), "archived": False, "commits": list(commits), "zweige": list(zweige), "fehler": None}


def fehler(status):
    e = github.GitHubFehler(f"nachgebaut {status}")
    e.status, e.meldung, e.global_ = status, "Bad Gateway", False
    return e


def alle_seiten(pfad, params=None, leer_bei_409=False):
    if pfad == "user/repos":
        return iter([{"full_name": n, "id": i + 1, "archived": w["archived"], "pushed_at": w["pushed_at"], "default_branch": w["default_branch"],
                      "language": "Python", "html_url": f"https://github.com/{n}"} for i, (n, w) in enumerate(WELT.items())])
    n = "/".join(pfad.split("/")[1:3])
    if pfad.endswith("/commits"):
        assert leer_bei_409, "die Commitliste ist die einzige Liste mit 409 als leer"
        ABRUFE.append((n, params.get("since")))
        if WELT[n]["fehler"]:
            raise WELT[n]["fehler"]
        return iter([{"sha": s, "commit": {"author": {"date": d, "email": "dev@fernwerk-studio.example"}, "message": "x"}, "author": None, "parents": [{}]}
                     for s, d in WELT[n]["commits"] if not params.get("since") or d >= params["since"]])
    if pfad.endswith("/branches"):
        return iter([{"name": z, "commit": {"sha": s}} for z, s, _ in WELT[n]["zweige"]])
    raise AssertionError(pfad)


def holen(pfad, params=None, leer_bei_409=False):
    n = "/".join(pfad.split("/")[1:3])
    if "/actions/" in pfad:
        return {"total_count": 0, "workflow_runs": []}, {}
    if "/dependabot/" in pfad:
        return [], {}
    if "/compare/" in pfad:
        return {"ahead_by": 1, "behind_by": 0}, {}
    if "/commits/" in pfad:
        sha = pfad.rsplit("/", 1)[1]
        if params is None:   # Zweigkopf
            KOEPFE.append(sha)
        datum = next((d for _, s, d in WELT[n]["zweige"] if s == sha), "2026-09-01T00:00:00Z")
        return {"commit": {"author": {"date": datum}}, "files": [], "parents": [{}]}, {}
    raise AssertionError(pfad)


def lauf(stunden=1):
    """Ein Lauf eine Stunde nach dem letzten; liefert (Abrufe, lauf.json)."""
    UHR[0] += timedelta(hours=stunden)
    ABRUFE.clear(), KOEPFE.clear()
    alt = github.alle_seiten, github.holen, sammler.jetzt
    github.alle_seiten, github.holen, sammler.jetzt = alle_seiten, holen, lambda: iso(UHR[0])
    try:
        sammler.main()
    finally:
        github.alle_seiten, github.holen, sammler.jetzt = alt
    return dict(ABRUFE), json.loads(sammler.LAUF.read_text())


def stand(n):
    with db.verbinden() as c:
        return tuple(c.execute("SELECT abgerufen_utc, abruf_branch FROM repos WHERE repo = ?", (n,)).fetchone())


def shas(n):
    with db.verbinden() as c:
        return {r[0] for r in c.execute("SELECT sha FROM commits WHERE repo = ?", (n,))}


minus7 = lambda s: iso(datetime.fromisoformat(s.replace("Z", "+00:00")) - timedelta(days=7))

# 1. Erstlauf: jedes Repo ohne Abrufstand wird ganz geholt, der Stand ist die Startzeit des Laufs.
repo("fernwerk-studio/zukunft", UHR[0] - timedelta(days=30), [("z1", "2027-01-01T00:00:00Z")])   # Autordatum in der Zukunft
repo("fernwerk-studio/normal", UHR[0], [("n1", "2026-10-01T10:00:00Z")])
abrufe, lj = lauf()
start1 = iso(UHR[0])
assert abrufe == {"fernwerk-studio/zukunft": None, "fernwerk-studio/normal": None} and lj["teilweise"] == 0, (abrufe, lj)
assert stand("fernwerk-studio/normal") == (start1, "main"), stand("fernwerk-studio/normal")

# 2. Zukunftscommit plus normal gepushtes Repo: das Fenster von normal kommt aus seinem eigenen Stand, nicht aus MAX(zeitstempel_utc)
# (vorher 2026-12-25 für alle Repos und kein Abruf). Der neue Commit kommt an.
WELT["fernwerk-studio/normal"]["commits"].append(("n2", iso(UHR[0] + timedelta(minutes=30))))
WELT["fernwerk-studio/normal"]["pushed_at"] = iso(UHR[0] + timedelta(minutes=30))
abrufe, lj = lauf()
assert abrufe.get("fernwerk-studio/normal") == minus7(start1) and abrufe["fernwerk-studio/normal"] < iso(UHR[0]), abrufe
assert "n2" in shas("fernwerk-studio/normal") and lj["teilweise"] == 0, (shas("fernwerk-studio/normal"), lj)
assert "fernwerk-studio/zukunft" not in abrufe, "ohne Push seit Stand minus Überlappung kein Abruf"

# 3. Ein erstmals sichtbares älteres Repo (letzter Push vor Monaten) wird ganz geholt, mit seiner ganzen Historie.
repo("fernwerk-studio/alt", datetime(2026, 8, 1, tzinfo=timezone.utc), [("a1", "2026-07-01T00:00:00Z"), ("a2", "2026-08-01T00:00:00Z")])
abrufe, lj = lauf()
assert abrufe.get("fernwerk-studio/alt", "fehlt") is None and shas("fernwerk-studio/alt") == {"a1", "a2"} and stand("fernwerk-studio/alt")[0] == iso(UHR[0]), (abrufe, shas("fernwerk-studio/alt"))

# 4. Wechsel des Default-Branch: der alte Stand gilt nicht für den neuen Zweig, das Repo wird ganz geholt, auch ohne neuen Push.
WELT["fernwerk-studio/alt"]["default_branch"] = "trunk"
WELT["fernwerk-studio/alt"]["commits"].append(("a0", "2026-06-01T00:00:00Z"))   # ältere Historie, die nur auf trunk liegt
abrufe, _ = lauf()
assert abrufe.get("fernwerk-studio/alt", "fehlt") is None and "a0" in shas("fernwerk-studio/alt") and stand("fernwerk-studio/alt") == (iso(UHR[0]), "trunk"), (abrufe, stand("fernwerk-studio/alt"))
abrufe, _ = lauf()
assert "fernwerk-studio/alt" not in abrufe, ("danach wieder inkrementell, ohne Push kein Abruf", abrufe)

# 5. Fehler mitten im Abruf eines Repos: Teilerfolg, der Stand bleibt, der nächste Lauf holt dasselbe Fenster noch einmal.
stand_vorher = stand("fernwerk-studio/normal")
WELT["fernwerk-studio/normal"]["fehler"] = fehler(502)
WELT["fernwerk-studio/normal"]["pushed_at"] = iso(UHR[0] + timedelta(minutes=10))
WELT["fernwerk-studio/normal"]["commits"].append(("n3", iso(UHR[0] + timedelta(minutes=10))))
abrufe, lj = lauf()
assert lj["teilweise"] == 1 and stand("fernwerk-studio/normal") == stand_vorher and "n3" not in shas("fernwerk-studio/normal"), (lj, stand("fernwerk-studio/normal"))
WELT["fernwerk-studio/normal"]["fehler"] = None
abrufe, lj = lauf()
assert abrufe["fernwerk-studio/normal"] == minus7(stand_vorher[0]) and "n3" in shas("fernwerk-studio/normal") and lj["teilweise"] == 0, (abrufe, lj)

# 6. Archiviert und wieder sichtbar: das Repo setzt bei seinem eigenen Stand fort, nicht bei einem fremden.
stand_n = stand("fernwerk-studio/normal")[0]
WELT["fernwerk-studio/normal"]["archived"] = True
lauf(stunden=48)
WELT["fernwerk-studio/normal"]["archived"] = False
WELT["fernwerk-studio/normal"]["pushed_at"] = iso(UHR[0])
abrufe, _ = lauf()
assert abrufe["fernwerk-studio/normal"] == minus7(stand_n), (abrufe, stand_n)

# 7. Zweigkopf mit Zukunftsdatum: beim ersten Sehen min(Autordatum, jetzt), bei unverändertem Kopf ohne neuen Abruf derselbe Wert,
# bei neuem Kopf neu gelesen.
WELT["fernwerk-studio/normal"]["zweige"] = [("wip", "k1", "2027-03-01T00:00:00Z")]
_, _ = lauf()
zweig = lambda: tuple(db.verbinden().execute("SELECT kopf_sha, letzter_utc FROM zweige WHERE name = 'wip'").fetchone())
gesehen = iso(UHR[0])
assert zweig() == ("k1", gesehen) and KOEPFE == ["k1"], (zweig(), KOEPFE)
lauf()
assert zweig() == ("k1", gesehen) and KOEPFE == [], ("derselbe Kopf: kein Abruf, die Frist läuft weiter", zweig(), KOEPFE)
WELT["fernwerk-studio/normal"]["zweige"] = [("wip", "k2", "2026-10-01T00:00:00Z")]
lauf()
assert zweig() == ("k2", "2026-10-01T00:00:00Z") and KOEPFE == ["k2"], (zweig(), KOEPFE)

# 8. MAX(zeitstempel_utc) steuert den Abruf nirgends mehr.
assert "SELECT MAX(zeitstempel_utc)" not in open(sammler.__file__).read()
print("ok: Abrufstand je Repo (Zukunftscommit, neues älteres Repo, Branch-Wechsel, Fehler mitten im Abruf, wieder sichtbar), Zweigkopf mit Zukunftsdatum")
