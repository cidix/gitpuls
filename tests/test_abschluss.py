"""Die letzten Punkte vor der Kopie in das freie Repo (10.10.2026), je Punkt eine Prüfung, die auf 7fdcd81 rot ist und nach dem Bau grün.
Jede Prüfung läuft in einem eigenen Prozess:
    python -m tests.test_abschluss            alle, mit Zusammenfassung, Exit 1 bei einer roten
    python -m tests.test_abschluss b8         eine einzelne"""
import email.message
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

os.environ["TZ"] = "Europe/Zurich"   # die Zone setzt der Test selbst (Audit S28)
from tests.test_audit import WURZEL, beispiel, commit, repo_zeile, server_starten, umgebung

PRUEFUNGEN = {}


def pruefung(name, punkt):
    def setzen(fn):
        PRUEFUNGEN[name] = (punkt, fn)
        return fn
    return setzen


def anfrage(adr, pfad="/", methode="GET", **kopf):
    import urllib.request
    try:
        r = urllib.request.urlopen(urllib.request.Request(adr + pfad, method=methode, data=b"" if methode == "POST" else None, headers=kopf), timeout=20)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# Teil A

@pruefung("a4", "Punkt 4: /api/kopf ist entfernt samt kopf() und zaehle()")
def a4():
    _, web = umgebung()
    web.Handler.log_message = lambda *a: None
    srv, adr = server_starten(web)
    try:
        assert anfrage(adr, "/api/kopf")[0] == 404, "der Endpunkt antwortet nicht mehr"
    finally:
        srv.shutdown()
    from app import kennzahlen
    assert not hasattr(kennzahlen, "kopf") and not hasattr(web, "kopf_json") and not hasattr(web, "zaehle")


@pruefung("a5", "Punkt 5: die Teilerfolg-Zeile trennt nicht abrufbare Repos von gescheiterten Prüfabfragen, in de und en")
def a5():
    db, web = umgebung()
    start = "2026-10-10T04:00:00Z"
    c = db.verbinden(schreibend=True)
    c.execute("INSERT INTO laeufe (gestartet_utc, beendet_utc, neu, teilweise) VALUES (?, '2026-10-10T04:01:00Z', 0, 2)", (start,))
    c.execute("INSERT INTO repos (repo, default_branch, gesehen, fehler) VALUES ('o/a', 'main', ?, '403 Forbidden')", (start,))
    c.execute("INSERT INTO repos (repo, default_branch, gesehen) VALUES ('o/b', 'main', ?)", (start,))
    c.execute("INSERT INTO pruefungen (repo, ci, sicherheit, gesehen, ci_fehler, sicher_fehler) VALUES ('o/b', 'unbekannt', 'unbekannt', ?, '502', '502')", (start,))
    c.commit()
    from app.sprache import gilt
    for sprache, soll in (("de", ", Teilerfolg: 1 Repo nicht abrufbar, 2 Prüfabfragen gescheitert (CI, Sicherheit)"),
                          ("en", ", partial: 1 repo could not be fetched, 2 checks failed (CI, security)")):
        with gilt(sprache), db.verbinden() as c2:
            h = web.stand_block(c2)
        assert soll in h, (sprache, h)
    c.execute("UPDATE repos SET fehler = NULL WHERE repo = 'o/a'"); c.commit()
    with gilt("de"), db.verbinden() as c2:
        h = web.stand_block(c2)
    assert "nicht abrufbar" not in h and "2 Prüfabfragen gescheitert" in h, h


@pruefung("a6", "Punkt 6: die Wertregel der Detailzeilen überschreibt .glut nicht mehr")
def a6():
    css = (WURZEL / "app" / "web" / "style.css").read_text()
    regeln = re.findall(r"^([^{}\n]*\.dz > span:last-child[^{]*)\{", css, re.M)
    assert regeln and all(":not(.glut)" in r for r in regeln), regeln


def token_seite(web, db, ablauf, sprache="de"):
    web.TOKEN.write_text(json.dumps({"ablauf": ablauf, "gemessen": "2026-10-10T04:00:00Z"}))
    r = subprocess.run([sys.executable, "-c", f"""
from tests.test_audit import umgebung
import os, json
os.environ["GITPULS_DB"] = {str(db.DB)!r}
from app import web
print(web.seite(sprache={sprache!r}))
"""], cwd=WURZEL, capture_output=True, text=True, env={**os.environ, "GITPULS_SPRACHE": sprache})
    assert r.returncode == 0, r.stderr[-500:]
    return r.stdout


@pruefung("a7", "Punkt 7: Warnung vor dem Ablauf des Tokens ab 14 Tagen und nach Ablauf, ohne Ablaufdatum keine")
def a7():
    db, web = umgebung()
    from app import github, sammler
    alt = github.holen
    github.holen = lambda pfad, params=None, leer_bei_409=False: ({}, {"github-authentication-token-expiration": "2026-12-01 00:00:00 +0100"}) if pfad == "rate_limit" else alt(pfad, params)
    sammler.token_messen()
    github.holen = alt
    assert json.loads(sammler.TOKEN.read_text())["ablauf"] == "2026-12-01 00:00:00 +0100", "der Sammler misst den Kopf von /rate_limit"
    jetzt = datetime.now(timezone.utc)
    fmt_ = lambda d: d.strftime("%Y-%m-%d %H:%M:%S UTC")
    h = token_seite(web, db, fmt_(jetzt + timedelta(days=10)))
    hb = h[h.index('id="handlung"'):]
    assert 'id="handlung-token"' in hb and "läuft am" in hb and "in 10 Tagen" in hb, hb[:600]
    assert '<a href="#handlung-token">1 Token läuft ab</a>' in h, "die Kopfspalte zählt ihn mit"
    h = token_seite(web, db, fmt_(jetzt - timedelta(days=2)))
    assert "abgelaufen am" in h[h.index('id="handlung"'):], "nach Ablauf"
    assert "expired on" in token_seite(web, db, fmt_(jetzt - timedelta(days=2)), "en")
    assert "expires on" in token_seite(web, db, fmt_(jetzt + timedelta(days=3)), "en")
    for ruhig in (fmt_(jetzt + timedelta(days=30)), None):
        assert 'id="handlung-token"' not in token_seite(web, db, ruhig), ("keine Warnung", ruhig)


# Teil B

class Konto:
    """Nachgebautes GitHub hinter urlopen mit n Repos und einem Ratenlimit von `limit` Anfragen je `fenster` Sekunden auf einer festen Uhr."""

    def __init__(self, n, limit, fenster):
        self.n, self.limit, self.fenster, self.t, self.anfragen, self.fenster_start, self.gesperrt = n, limit, fenster, 1_000_000.0, 0, 1_000_000.0, 0

    def kopf(self, **k):
        m = email.message.Message()
        for a, b in k.items():
            m[a.replace("_", "-")] = str(b)
        return m

    def antwort(self, daten, **kopf):
        r = io.BytesIO(json.dumps(daten).encode())
        r.headers = self.kopf(**kopf)
        return r

    def urlopen(self, req, timeout=None):
        self.t += 0.05
        if self.t >= self.fenster_start + self.fenster:
            self.fenster_start, self.anfragen = self.t, 0
        if self.anfragen >= self.limit:
            self.gesperrt += 1
            raise urllib.error.HTTPError(req.full_url, 403, "rate limit exceeded", self.kopf(X_RateLimit_Remaining=0, X_RateLimit_Reset=int(self.fenster_start + self.fenster)),
                                         io.BytesIO(b'{"message": "API rate limit exceeded"}'))
        self.anfragen += 1
        u = urlsplit(req.full_url)
        pfad, q = u.path.lstrip("/"), parse_qs(u.query)
        alt = "2026-09-01T00:00:00Z"
        if pfad == "rate_limit":
            return self.antwort({}, github_authentication_token_expiration="2026-12-01 00:00:00 UTC")
        if pfad == "user/repos":
            s = int(q["page"][0])
            daten = [{"full_name": f"o/r{i}", "id": i + 1, "archived": False, "pushed_at": alt, "default_branch": "main", "language": "Python",
                      "html_url": f"https://github.com/o/r{i}"} for i in range((s - 1) * 100, min(s * 100, self.n))]
            return self.antwort(daten, **({"Link": f'<https://api.github.com/user/repos?page={s + 1}>; rel="next"'} if s * 100 < self.n else {}))
        teile = pfad.split("/")
        if teile[-1] == "commits":
            return self.antwort([{"sha": f"c-{teile[2]}", "commit": {"author": {"date": alt, "email": "dev@example.org"}, "message": "x"},
                                  "author": None, "parents": [{}]}])
        if teile[-1] == "branches":
            return self.antwort([])
        if "actions" in teile:
            return self.antwort({"total_count": 0, "workflow_runs": []})
        if "dependabot" in teile:
            return self.antwort([])
        if teile[-2] == "commits":
            return self.antwort({"files": [], "parents": [{}]})
        raise AssertionError(pfad)


def konto_lauf(konto):
    """Ein Lauf des echten Sammlers gegen das Konto: (Exit, Repos mit Abrufstand, Repos mit Prüfung, Laufzeilen)."""
    import time as zeit
    from app import db, github, sammler
    zeit.time = zeit.monotonic = lambda: konto.t
    zeit.sleep = lambda s: setattr(konto, "t", konto.t + s)
    github.urllib.request.urlopen = konto.urlopen
    try:
        sammler.main()
        ok = True
    except SystemExit:
        ok = False
    with db.verbinden() as c:
        return (ok, c.execute("SELECT COUNT(*) FROM repos WHERE abgerufen_utc IS NOT NULL").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM pruefungen").fetchone()[0], c.execute("SELECT COUNT(*) FROM laeufe").fetchone()[0])


@pruefung("b8", "Punkt 8: am Ratenlimit wartet der Sammler bis zum Reset, wenn es in die Frist passt, sonst endet er mit Fortschritt; 400 Repos")
def b8():
    os.environ["GITHUB_TOKEN"] = "x"
    umgebung()
    k = Konto(400, limit=1000, fenster=200)   # das Limit ist nach 200 s zurück, das passt in die Frist von 600 s
    ok, stand, geprueft, laeufe = konto_lauf(k)
    assert ok and stand == 400 and geprueft == 400 and laeufe == 1 and k.gesperrt >= 1, ("ein Lauf mit Warten bis zum Reset", ok, stand, geprueft, laeufe, k.gesperrt)


@pruefung("b8_fortschritt", "Punkt 8: liegt der Reset hinter der Frist, endet der Lauf mit gesichertem Stand und der nächste setzt fort")
def b8_fortschritt():
    os.environ["GITHUB_TOKEN"] = "x"
    umgebung()
    k = Konto(400, limit=1500, fenster=3600)   # Reset erst nach einer Stunde: kein Warten möglich
    verlauf = []
    for _ in range(4):
        verlauf.append(konto_lauf(k))
        if verlauf[-1][0]:
            break
        k.t += 3600   # der nächste Lauf des Timers
    assert all(b[1:3] > a[1:3] for a, b in zip([(None, 0, 0, 0)] + verlauf, verlauf) if not a[0]), ("jeder Lauf mit Fortschritt", verlauf)
    assert verlauf[0][2] > 0, ("Prüfungen des abgebrochenen Laufs sind festgeschrieben", verlauf)
    assert verlauf[-1][0] and verlauf[-1][1:] == (400, 400, 1), verlauf


@pruefung("b9", "Punkt 9: jede Anfrage mit fremdem Host bekommt 421, Loopback und die erlaubten Hosts gehen durch")
def b9():
    _, web = umgebung(GITPULS_HOSTS="dash.example.ts.net")
    web.Handler.log_message = lambda *a: None
    web.lauf_starten = lambda: (202, {"gestartet": "x"})
    srv, adr = server_starten(web)
    port = adr.rsplit(":", 1)[1]
    try:
        assert anfrage(adr, "/gesund")[0] == 200, "127.0.0.1 mit Port"
        assert anfrage(adr, "/gesund", Host=f"localhost:{port}")[0] == 200
        assert anfrage(adr, "/gesund", Host="dash.example.ts.net:8444")[0] == 200, "der weitergereichte Host hinter tailscale serve"
        for host in ("rebind.example", f"rebind.example:{port}", "127.0.0.1.rebind.example"):
            assert anfrage(adr, "/", Host=host)[0] in (421, 403), host
            assert anfrage(adr, "/gesund", Host=host)[0] in (421, 403), host
        assert anfrage(adr, "/lauf", "POST", Host="rebind.example", **{"Sec-Fetch-Site": "same-origin"})[0] in (421, 403)
    finally:
        srv.shutdown()


@pruefung("b10", "Punkt 10: der Rückblick «seit Beginn» rechnet bei drei Jahren nach Monaten, in de und en")
def b10():
    db, web = umgebung()
    heute = date(2026, 10, 10)
    c = db.verbinden(schreibend=True)
    repo_zeile(c, "o/a")
    tag, i = date(2023, 10, 16), 0
    while tag <= heute:
        commit(c, f"j{i}", "o/a", datetime(tag.year, tag.month, tag.day, 12, tzinfo=web.TZ))
        tag, i = tag + timedelta(days=3), i + 1
    c.commit()
    from app import format as fmt
    from app.sprache import gilt
    for sprache, wort in (("de", "Commits je Monat"), ("en", "Commits per month")):
        with gilt(sprache), fmt.gilt(fmt.waehlen(sprache, "")), db.verbinden() as c2:
            kopf, grafik = web.rueckblick_fenster(web.tageszaehlung(c2, datetime(2026, 10, 10, 20, tzinfo=web.TZ)), heute, date(2023, 10, 16), "beginn", "x")
        balken = len(re.findall(r'<rect class="b', grafik))
        assert balken == 37, (sprache, balken)   # Oktober 2023 bis Oktober 2026
        assert wort in grafik, (sprache, wort)
        assert re.search(r'class="pmarke[^"]*"[^>]*>\d+ (im|in) \w+\s\d{4}</span>', grafik), ("Spitze mit Monat und Jahr", sprache)
        assert "Wochen ab Montag" not in grafik and "Weeks start" not in grafik, sprache


# Teil C

# Treffer, die bleiben dürfen: die Units und die Beispieltabelle tragen /opt/gitpuls als markierten Beispielpfad.
ERLAUBT = {"deploy/gitpuls-deploy.service": "/opt/gitpuls", "deploy/gitpuls-sammler.service": "/opt/gitpuls",
           "deploy/gitpuls-server.service": "/opt/gitpuls", "deploy/gitpuls-sterne.service": "/opt/gitpuls", "tools/dienste.beispiel.json": "/opt/"}


@pruefung("c11", "Punkt 11 und 12: kein Name, keine Adresse, kein Host und kein Pfad des Autors im getrackten Code")
def c11():
    liste = Path(os.environ.get("GITPULS_SPERRLISTE") or Path.home() / ".config" / "veroeffentlichung" / "sperrliste.txt")
    muster = [z.strip().lower() for z in liste.read_text().splitlines() if z.strip()] if liste.is_file() else []
    muster += ["/users/", "/opt/"]   # die Namen stehen nur in der Sperrliste ausserhalb des Repos (A5)
    dateien = subprocess.run(["git", "ls-files"], cwd=WURZEL, capture_output=True, text=True).stdout.split()
    funde = []
    for f in dateien:
        if f.endswith((".png", ".ico")) or f == "tests/test_abschluss.py":
            continue
        for nr, zeile in enumerate((WURZEL / f).read_text(errors="replace").splitlines(), 1):
            # Die Identität des Repos ist gewollt öffentlich: der Repo- und Image-Name und der Rechteinhaber der Lizenz.
            z = zeile.lower().replace("cidix/gitpuls", "").replace("copyright (c) 2026 outperform gmbh", "")
            for m in muster:
                if m in z and not (f in ERLAUBT and (ERLAUBT[f] is None or (m == "/opt/" and ERLAUBT[f].lower() in z))):
                    funde.append(f"{f}:{nr}: {m}")
    assert not funde, funde[:20]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        PRUEFUNGEN[sys.argv[1]][1]()
        sys.exit(0)
    rot = []
    for name, (punkt, _) in PRUEFUNGEN.items():
        r = subprocess.run([sys.executable, "-m", "tests.test_abschluss", name], cwd=WURZEL, capture_output=True, text=True, timeout=600)
        if r.returncode:
            rot.append(name)
            print(f"rot   {punkt}\n      {(r.stderr.strip().splitlines() or ['?'])[-1][:220]}")
        else:
            print(f"grün  {punkt}")
    print(f"{len(PRUEFUNGEN) - len(rot)} von {len(PRUEFUNGEN)} grün" + (f", rot: {', '.join(rot)}" if rot else ""))
    sys.exit(1 if rot else 0)
