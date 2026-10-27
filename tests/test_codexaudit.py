"""Die 13 Befunde des Codex-Audits vom 11.10.2026 über ce8c752, je Befund eine Prüfung, die auf ce8c752 rot ist und nach dem Fix grün.
Jede Prüfung läuft in einem eigenen Prozess (eigene Umgebung, eigene Datenbank, eigene Zone):
    python -m tests.test_codexaudit            alle, mit Zusammenfassung, Exit 1 bei einer roten
    python -m tests.test_codexaudit c5         eine einzelne"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tests.test_audit import WURZEL, beispiel, commit, git_lauf, iso, programme, repo_zeile, serverstand, umgebung

PRUEFUNGEN = {}


def pruefung(name, befund):
    def setzen(fn):
        PRUEFUNGEN[name] = (befund, fn)
        return fn
    return setzen


# Klasse A: ungültige Eingaben gelten nie als leer oder frisch

@pruefung("c1", "1: ungültige API-Antworten (null, {}, \"\", false, 0) sind gescheiterte Abfragen und löschen keine Warnung")
def c1():
    db, _ = umgebung()
    from app import github, sammler
    c = db.verbinden(schreibend=True)
    c.execute("INSERT INTO warnungen (repo, nummer, paket, stufe, url) VALUES ('o/a', 7, 'lodash', 'critical', 'u')")
    c.execute("INSERT INTO pruefungen (repo, ci, sicherheit, gesehen, warnungen, warn_stufe, ci_gemessen, sicher_gemessen) "
              "VALUES ('o/a', 'fehlgeschlagen', 'ok', 'x', 1, 'critical', 'alt', 'alt')")
    c.commit()
    for wert in (None, {}, "", False, 0):
        sammler.github.holen = lambda pfad, params=None, leer_bei_409=False, w=wert: (w, {})
        n = sammler.pruefen(c, {"full_name": "o/a", "default_branch": "main"}, "neu")
        p = c.execute("SELECT ci, warnungen, ci_fehler, sicher_fehler, sicher_gemessen FROM pruefungen").fetchone()
        assert c.execute("SELECT COUNT(*) FROM warnungen").fetchone()[0] == 1, (wert, "die bekannte Warnung bleibt")
        assert n == 2 and p["ci"] == "fehlgeschlagen" and p["warnungen"] == 1 and p["ci_fehler"] and p["sicher_fehler"] \
            and p["sicher_gemessen"] == "alt", (wert, n, dict(p))
    # dieselbe Grenze für jede Liste und jedes Objekt der API: eine Seite, die keine Liste ist, ist eine gescheiterte Abfrage des Repos
    github.holen = lambda pfad, params=None, leer_bei_409=False: ({"message": "x"}, {})
    try:
        list(github.alle_seiten("repos/o/a/commits", {"per_page": 100}))
        raise AssertionError("eine Seite ohne Liste darf nicht als leer gelten")
    except github.GitHubFehler as e:
        assert not e.global_, "betrifft nur das Repo der Anfrage"
    github.holen = lambda pfad, params=None, leer_bei_409=False: (None, {})
    for aufruf in (lambda: sammler.dateien_eines("o/a", "abc"),):
        try:
            aufruf()
            raise AssertionError("ein Commit-Detail ohne Objekt darf nicht als «keine Dateien» gelten")
        except github.GitHubFehler as e:
            assert not e.global_


@pruefung("c6", "6: eine kaputte server.json ist «nicht lesbar» mit Grund, nicht «Noch kein Serverstand», und steht im Handlungsbedarf")
def c6():
    _, web = umgebung()
    for kaputt in ("{nicht json", "[]"):
        web.SERVER.write_text(kaputt)
        h = web.seite()
        assert "Noch kein Serverstand" not in h, kaputt
        zone = h[h.index('class="zone server"'):][:600]
        assert "nicht lesbar" in zone, (kaputt, zone)
        hb = h[h.index('id="handlung"'):]
        assert 'id="handlung-serverstand"' in hb and "nicht lesbar" in hb[:hb.index("</section>")], (kaputt, hb[:600])
    web.SERVER.unlink()
    assert "Noch kein Serverstand" in web.seite(), "eine fehlende Datei bleibt «noch nicht eingerichtet»"


@pruefung("c7", "7: ein ungültiger Deploy-Zeitpunkt (fehlt, «invalid», ohne Zone) ist nicht gemessen, nie frisch")
def c7():
    db, web = umgebung()
    jetzt = datetime.now(timezone.utc)
    s = beispiel()
    s["erhoben_utc"] = iso(jetzt)
    repo = s["dienste"][2]["repo"]
    c = db.verbinden(schreibend=True)
    repo_zeile(c, repo)
    c.commit(); c.close()
    for gemessen in (None, "invalid", jetzt.replace(tzinfo=None).isoformat(timespec="seconds")):
        dp = {"tag": "abc1234", "origin_main": "abc1234", "hinter": 0, "bildrelevant": 0, "commits": [], "repo": repo}
        if gemessen:
            dp["gemessen"] = gemessen
        s["dienste"][2]["deploy"] = dp
        with web.gilt("de"):
            h = web.server_dienste_block(s, jetzt)
        teil = h[h.index("gitpuls-gitpuls-1"):]
        teil = teil[:teil.index("</details>")]
        assert "nicht gemessen" in teil and "gleich" not in teil, (gemessen, teil[-300:])
        web.SERVER.write_text(json.dumps(s))
        web.DEPLOY.write_text(json.dumps({s["dienste"][2]["name"]: dp}))
        with db.verbinden() as c, web.gilt("de"):
            k = next(k for k in web.projekte(c, jetzt.astimezone(web.TZ).date(), jetzt.astimezone(web.TZ)) if k["repo"] == repo)
        assert k["messung"] and k["messung"][0][0] == "server", (gemessen, k["messung"])


@pruefung("c8", "8: eine ungültige Diensttabelle ist «nicht gemessen» mit Grund, ein kaputter Eintrag betrifft nur sich selbst")
def c8():
    ss = serverstand()
    os.environ["PATH"] = programme(tempfile.mkdtemp()) + os.pathsep + os.environ["PATH"]
    tab = os.path.join(tempfile.mkdtemp(), "dienste.json")
    ss.DIENSTE = tab
    os.environ["GITPULS_DIENSTE"] = tab
    for wurzel in ('{"dienste": null}', '{"dienste": {}}', "[]", '{"x": 1}'):
        Path(tab).write_text(wurzel)
        d = ss.messen()
        assert "dienste" in d["ungemessen"], (wurzel, d["ungemessen"])
    gut = {"name": "Gut", "repo": "o/gut", "container": ["gut-1"], "timer": []}
    Path(tab).write_text(json.dumps({"dienste": [{}, gut, {"name": "Kaputt", "container": None}, {"name": 5}]}))
    d = ss.messen()   # vorher KeyError oder TypeError: keine server.json
    namen = [x["name"] for x in d["dienste"]]
    assert "Gut" in namen and len(d["dienste"]) == 4, namen
    kaputt = [x for x in d["dienste"] if x["name"] != "Gut"]
    assert all(x.get("fehler") for x in kaputt) and not next(x for x in d["dienste"] if x["name"] == "Gut").get("fehler"), d["dienste"]
    # die Seite zeigt den kaputten Eintrag als nicht gemessen, die übrigen normal
    _, web = umgebung()
    s = beispiel()
    s["erhoben_utc"] = web.iso_utc()
    s["dienste"] = s["dienste"][:1] + kaputt + [{"name": "Roh", "container": "kein Array", "timer": None}]
    with web.gilt("de"):
        h = web.server_dienste_block(s, datetime.now(timezone.utc))
    assert 'class="blockfehler' not in h and h.count('<details class="dienst">') == 5 and h.count("nicht gemessen") >= 4, h[-800:]
    # deploy-stand: ein kaputter Eintrag reisst die übrigen nicht mit
    werk = tempfile.mkdtemp()
    r = subprocess.run(["bash", str(WURZEL / "tools" / "deploy-stand.sh")], capture_output=True, text=True,
                       env={**os.environ, "GITPULS_DEPLOY_ZIEL": os.path.join(werk, "deploy.json")})
    assert r.returncode == 0, (r.returncode, r.stderr[-500:])
    dj = json.loads(Path(werk, "deploy.json").read_text())
    assert dj.get("Kaputt", {}).get("fehler") and "Gut" in dj, dj
    # der Name ist Schlüssel des Deploy-Stands: zwei gleichnamige Dienste wären wieder Befund 9
    Path(tab).write_text(json.dumps({"dienste": [gut, {**gut, "repo": "o/zwei"}]}))
    r = subprocess.run(["bash", str(WURZEL / "tools" / "deploy-stand.sh")], capture_output=True, text=True,
                       env={**os.environ, "GITPULS_DEPLOY_ZIEL": os.path.join(werk, "deploy.json")})
    assert "mehrfach" in json.loads(Path(werk, "deploy.json").read_text())["Gut"].get("fehler", ""), r.stderr[-300:]
    assert all("mehrfach" in x.get("fehler", "") for x in ss.messen()["dienste"]), "server-stand meldet den doppelten Namen ebenso"


@pruefung("c12", "12: ungültige Taktwerte (nan, inf, -1, abc) sind ein sichtbarer Konfigurationsfehler beim Start")
def c12():
    for wert in ("nan", "inf", "-1", "abc"):
        r = subprocess.run([sys.executable, "-c", "from tests.test_audit import umgebung; umgebung()"], cwd=WURZEL, capture_output=True, text=True,
                           env={**os.environ, "GITPULS_TAKT_MIN": wert})
        assert r.returncode != 0 and "GITPULS_TAKT_MIN" in r.stderr, (wert, r.returncode, r.stderr[-300:])
    for name, wert in (("GITPULS_LAUFFRIST_S", "nan"), ("GITPULS_LAUFFRIST_S", "0"), ("GITPULS_PORT", "x")):
        r = subprocess.run([sys.executable, "-c", "from tests.test_audit import umgebung; _, web = umgebung(); web.port()"], cwd=WURZEL,
                           capture_output=True, text=True, env={**os.environ, name: wert})
        assert r.returncode != 0 and name in r.stderr, (name, wert, r.returncode, r.stderr[-300:])
    r = subprocess.run([sys.executable, "-c", "from tests.test_audit import umgebung; _, web = umgebung(); assert web.TAKT_MIN == 2.5"], cwd=WURZEL,
                       capture_output=True, text=True, env={**os.environ, "GITPULS_TAKT_MIN": "2.5"})
    assert r.returncode == 0, r.stderr[-300:]


# Klasse B: nicht eindeutige Schlüssel

def nicht_da(pfad):
    from app import github
    e = github.GitHubFehler(f"GitHub 422 auf {pfad}: No commit found for SHA")
    e.status, e.meldung, e.global_ = 422, "No commit found for SHA", False
    return e


def welt_fakes(welt, protokoll, uhr=None):
    """Nachgebautes GitHub mit eigenen IDs und Seiten ab params["page"]. welt: Name -> {"id", "branch", "commits": [(sha, datum)], "zweige"}."""
    def alle_seiten(pfad, params=None, leer_bei_409=False):
        params = params or {}
        if pfad == "user/repos":
            return iter([{"full_name": n, "id": w["id"], "archived": False, "pushed_at": "2026-10-10T00:00:00Z", "default_branch": w["branch"],
                          "language": "Python", "html_url": f"https://github.com/{n}"} for n, w in welt.items()])
        n = "/".join(pfad.split("/")[1:3])
        if pfad.endswith("/branches"):
            return iter([{"name": z, "commit": {"sha": "k" + n + z}} for z in welt[n].get("zweige", [])])
        def seiten():
            cs = [(s_, d) for s_, d in welt[n]["commits"] if not params.get("since") or d >= params["since"]]
            if params.get("sha"):   # wie GitHub: die Geschichte ab diesem Commit, ein unbekannter sha ist 422
                shas = [s_ for s_, _ in cs]
                if params["sha"] not in shas:
                    raise nicht_da(f"repos/{n}/commits")
                cs = cs[shas.index(params["sha"]):]
            for nr in range(params.get("page", 1) - 1, max((len(cs) + 99) // 100, 1)):
                protokoll.append((n, nr + 1))
                if uhr and n in uhr:
                    uhr[n](nr + 1)
                yield from [{"sha": s, "commit": {"author": {"date": d, "email": "dev@example.org"}, "message": "x"}, "author": None, "parents": [{}]}
                            for s, d in cs[nr * 100:nr * 100 + 100]]
        return seiten()

    def holen(pfad, params=None, leer_bei_409=False):
        protokoll.append(("holen", pfad))
        if "/actions/" in pfad:
            return {"total_count": 0, "workflow_runs": []}, {}
        if "/dependabot/" in pfad:
            return [], {}
        if "/compare/" in pfad:
            return {"ahead_by": 1, "behind_by": 0}, {}
        if "/commits/" in pfad:
            n, sha = "/".join(pfad.split("/")[1:3]), pfad.rsplit("/", 1)[1]
            if not sha.startswith("k") and sha not in {s_ for s_, _ in welt.get(n, {}).get("commits", [])}:
                raise nicht_da(pfad)   # nach Force-Push oder in einem neu angelegten Repo nicht mehr abrufbar
            return {"sha": sha, "commit": {"author": {"date": "2026-10-01T00:00:00Z"}}, "files": [], "parents": [{}]}, {}
        if pfad == "rate_limit":
            return {}, {}
        raise AssertionError(pfad)
    return alle_seiten, holen


def sammeln(sammler, welt, protokoll, **k):
    from app import github
    alt = github.alle_seiten, github.holen
    github.alle_seiten, github.holen = welt_fakes(welt, protokoll, k.pop("uhr", None))
    try:
        return sammler.main(**k)
    finally:
        github.alle_seiten, github.holen = alt


@pruefung("c2", "2: getauschte Repo-Namen werden über die GitHub-ID aufgelöst, auch mit gleichnamigen Zweigen")
def c2():
    db, _ = umgebung()
    from app import sammler
    jetzt = datetime.now(timezone.utc)
    welt = {"o/a": {"id": 1, "branch": "main", "commits": [("a1", iso(jetzt - timedelta(days=2)))], "zweige": ["wip"]},
            "o/b": {"id": 2, "branch": "main", "commits": [("b1", iso(jetzt - timedelta(days=2)))], "zweige": ["wip"]}}
    sammeln(sammler, welt, [])
    # dazwischen über einen temporären Namen getauscht: ID 1 heisst jetzt o/b, ID 2 o/a
    welt = {"o/b": {**welt["o/a"]}, "o/a": {**welt["o/b"]}}
    sammeln(sammler, welt, [])   # vorher IntegrityError auf zweige.repo, zweige.name
    with db.verbinden() as c:
        je = dict(c.execute("SELECT sha, repo FROM commits"))
        ids = dict(c.execute("SELECT repo, id FROM repos"))
    assert je == {"a1": "o/b", "b1": "o/a"} and ids == {"o/b": 1, "o/a": 2}, (je, ids)
    # und zurück, ohne gleichnamige Zweige: vorher landeten beide Historien bei o/a
    welt = {"o/a": {**welt["o/b"], "zweige": []}, "o/b": {**welt["o/a"], "zweige": []}}
    sammeln(sammler, welt, [])
    with db.verbinden() as c:
        assert dict(c.execute("SELECT sha, repo FROM commits")) == {"a1": "o/a", "b1": "o/b"}


@pruefung("c9", "9: zwei Dienste desselben Repos haben je ihren eigenen Deploy-Stand")
def c9():
    werk = Path(tempfile.mkdtemp())
    ursprung, klon = werk / "ursprung", werk / "klon"
    git = lambda *a, cwd=ursprung: git_lauf(*a, cwd=cwd, text=True).stdout.strip()
    ursprung.mkdir()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.org"); git("config", "user.name", "t")
    (ursprung / "Dockerfile").write_text("FROM x\nCOPY app.py ./\n")
    shas = []
    for i in range(4):
        (ursprung / "app.py").write_text(f"print({i})\n")
        git("add", "-A"); git("commit", "-qm", f"c{i}")
        shas.append(git("rev-parse", "--short", "HEAD"))
    git("clone", "-q", str(ursprung), str(klon), cwd=werk)
    bilder = {"prod-1": f"x:{shas[0]}", "staging-1": f"x:{shas[3]}"}
    docker = "case \"$1\" in inspect) case \"$2\" in " + " ".join(f"{k}) echo {v};;" for k, v in bilder.items()) + " esac;; esac\n"
    os.environ["PATH"] = programme(str(werk / "bin"), docker=docker) + os.pathsep + os.environ["PATH"]
    tab = werk / "dienste.json"
    tab.write_text(json.dumps({"dienste": [{"name": "Produktion", "repo": "o/x", "checkout": str(klon), "container": ["prod-1"], "timer": []},
                                           {"name": "Staging", "repo": "o/x", "checkout": str(klon), "container": ["staging-1"], "timer": []}]}))
    ziel = werk / "deploy.json"
    r = subprocess.run(["bash", str(WURZEL / "tools" / "deploy-stand.sh")], capture_output=True, text=True,
                       env={**os.environ, "GITPULS_DIENSTE": str(tab), "GITPULS_DEPLOY_ZIEL": str(ziel)})
    assert r.returncode == 0, r.stderr[-400:]
    dj = json.loads(ziel.read_text())
    assert dj["Produktion"]["hinter"] == 3 and dj["Staging"]["hinter"] == 0 and dj["Produktion"]["repo"] == "o/x", dj
    ss = serverstand()
    ss.ZIEL = str(werk / "server.json")
    ss.DIENSTE = str(tab)
    os.environ["GITPULS_DIENSTE"] = str(tab)
    d = ss.messen()
    assert [x["deploy"]["hinter"] for x in d["dienste"]] == [3, 0], d["dienste"]
    # die Projektzeile des Repos zeigt den Rückstand der Produktion, nicht den aktuellen Stand von Staging
    db, web = umgebung()
    jetzt = datetime.now(timezone.utc)
    web.SERVER.write_text(json.dumps({**beispiel(), "erhoben_utc": iso(jetzt), "dienste": d["dienste"]}))
    web.DEPLOY.write_text(json.dumps({k: {**v, "gemessen": iso(jetzt)} for k, v in dj.items()}))
    c = db.verbinden(schreibend=True)
    repo_zeile(c, "o/x")
    c.commit(); c.close()
    with db.verbinden() as c, web.gilt("de"):
        k = next(k for k in web.projekte(c, jetzt.astimezone(web.TZ).date(), jetzt.astimezone(web.TZ)) if k["repo"] == "o/x")
    assert k["server_befund"] and k["server"]["hinter"] == 3, k["server"]


@pruefung("c11", "11: verschiedene Zweignamen (fix/a, fix-a, fix.a) und Repos ergeben verschiedene Sprungziele")
def c11():
    _, web = umgebung()
    ids = {web.hb_id("zweige", "o/a", z) for z in ("fix/a", "fix-a", "fix.a", "fix_a")}
    assert len(ids) == 4, ids
    assert web.hb_id("zweige", "o/a-b", "c") != web.hb_id("zweige", "o/a", "b-c")
    assert web.anker("o/a-b") != web.anker("o-a/b")
    assert all(re.fullmatch(r"[A-Za-z0-9_~-]+", i) for i in ids), ids


# Klasse C: falsche Zustandsaussagen

FILTER_LEER = "*filter\n:INPUT ACCEPT [0:0]\n:FORWARD DROP [0:0]\n:OUTPUT ACCEPT [0:0]\nCOMMIT\n"


@pruefung("c5", "5: iptables nach Tabelle: filter zählt allein, Betreiberregeln auch auf docker0 fallen auf, nat und raw sind «nicht verglichen»")
def c5():
    ss = serverstand()
    text = "*nat\n:PREROUTING ACCEPT [0:0]\n:INPUT ACCEPT [0:0]\n-A INPUT -p tcp --dport 22 -j ACCEPT\nCOMMIT\n" + FILTER_LEER
    assert ss.regeln(text)["INPUT"] == [], "die leere Filter-INPUT-Kette ist leer"
    werk = Path(tempfile.mkdtemp())
    def lauf(laufend, gespeichert):
        (werk / "rules.v4").write_text(gespeichert)
        (werk / "laufend").write_text(laufend)
        bin_ = programme(str(werk / "bin"), **{"iptables-save": f"cat {werk / 'laufend'}\n", "systemctl": "echo enabled\n"})
        os.environ["PATH"] = bin_ + os.pathsep + os.environ["PATH"]
        ss.FAMILIEN = (("IPv4", "iptables-save", str(werk / "rules.v4")),)
        return ss.firewall(None)
    def filt(*regeln):
        return ("*filter\n:INPUT DROP [0:0]\n:FORWARD DROP [0:0]\n:OUTPUT ACCEPT [0:0]\n:DOCKER - [0:0]\n:DOCKER-USER - [0:0]\n"
                + "".join(z + "\n" for z in regeln) + "COMMIT\n")
    # Eine geänderte Betreiberregel auf docker0 fällt auf, in DOCKER-USER und in FORWARD (Bestätigungslauf 11.10.2026)
    for kette in ("DOCKER-USER", "FORWARD"):
        fw = lauf(filt(f"-A {kette} -i docker0 -p tcp --dport 8080 -j DROP"), filt(f"-A {kette} -i docker0 -p tcp --dport 9090 -j DROP"))
        assert fw["wie_gespeichert"] is False, (kette, "eine geänderte Betreiberregel auf docker0 weicht ab", fw)
        fw = lauf(filt(), filt(f"-A {kette} -i docker0 -p tcp --dport 9090 -j DROP"))
        assert fw["wie_gespeichert"] is False, (kette, "eine fehlende Betreiberregel auf docker0 weicht ab", fw)
    # Ein Wechsel der Container-IP ist keine Abweichung: Docker schreibt seine Ketten in filter, nat und raw neu (MASQUERADE mit Container-IP
    # wie Docker 24, DROP vor veröffentlichten Ports wie Docker 29)
    def docker(ip):
        return (f"*nat\n:POSTROUTING ACCEPT [0:0]\n-A POSTROUTING -s {ip}/32 -d {ip}/32 -p tcp -m tcp --dport 80 -j MASQUERADE\nCOMMIT\n"
                f"*raw\n:PREROUTING ACCEPT [0:0]\n-A PREROUTING -d {ip}/32 ! -i br-68424ba94bb2 -j DROP\nCOMMIT\n"
                + filt(f"-A DOCKER -d {ip}/32 ! -i br-68424ba94bb2 -o br-68424ba94bb2 -p tcp -m tcp --dport 80 -j ACCEPT", "-A FORWARD -j DOCKER-USER"))
    fw = lauf(docker("172.18.0.3"), docker("172.18.0.2"))
    assert fw["wie_gespeichert"] is True and fw["nicht_verglichen"] == ["nat", "raw"], fw
    # nat und raw stehen als «nicht verglichen» auf der Seite, de und en
    _, web = umgebung()
    for sprache, wort in (("de", "nicht verglichen"), ("en", "not compared")):
        s_ = {**beispiel(), "firewall": fw}
        with web.gilt(sprache):
            h = web.fuss_firewall(s_)
        assert "nat, raw" in h and wort in h, (sprache, h[-300:])


@pruefung("c13", "13: «gleich main» nur bei identischem Commit, ein Stand ohne Rückstand auf anderem Commit ist abweichend")
def c13():
    _, web = umgebung()
    jetzt = datetime.now(timezone.utc)
    s = beispiel()
    s["erhoben_utc"] = iso(jetzt)
    for dp, gleich in (({"tag": "abc1234", "origin_main": "def5678", "hinter": 0, "bildrelevant": 0, "gleich": False}, False),
                       ({"tag": "abc1234", "origin_main": "def5678", "hinter": 0, "bildrelevant": 0}, False),
                       ({"tag": "abc1234", "origin_main": "abc1234", "hinter": 0, "bildrelevant": 0, "gleich": True}, True),
                       ({"tag": "abc1234def", "origin_main": "abc1234", "hinter": 0, "bildrelevant": 0}, True)):
        s["dienste"][2]["deploy"] = {**dp, "commits": [], "gemessen": iso(jetzt), "branch": "main"}
        with web.gilt("de"):
            h = web.server_dienste_block(s, jetzt)
        teil = h[h.index("gitpuls-gitpuls-1"):]
        teil = teil[:teil.index("</details>")]
        assert ("gleich main" in teil) == gleich and ("abweichend" in teil) != gleich, (dp, teil[-300:])
    # deploy-stand schreibt «gleich» aus dem Vergleich der Commits
    text = (WURZEL / "tools" / "deploy-stand.sh").read_text()
    assert '"gleich"' in text, "deploy.json trägt, ob der laufende Commit der Kopf des Default-Branch ist"


@pruefung("c10", "10: 7 und 30 Tage vergleichen die Vorperiode bis zum selben Zeitpunkt (vorwoche_fenster)")
def c10():
    db, web = umgebung()
    jetzt = datetime(2026, 10, 11, 12, 0, tzinfo=web.TZ)
    heute = jetzt.date()
    c = db.verbinden(schreibend=True)
    repo_zeile(c, "o/a")
    commit(c, "v7", "o/a", datetime(2026, 10, 4, 23, 0, tzinfo=web.TZ))    # Vorperiode der 7 Tage, nach dem Zeitpunkt
    commit(c, "v7f", "o/a", datetime(2026, 10, 4, 9, 0, tzinfo=web.TZ))    # Vorperiode der 7 Tage, vor dem Zeitpunkt
    commit(c, "v30", "o/a", datetime(2026, 9, 11, 23, 0, tzinfo=web.TZ))   # Vorperiode der 30 Tage, nach dem Zeitpunkt
    commit(c, "v30f", "o/a", datetime(2026, 9, 11, 9, 0, tzinfo=web.TZ))   # Vorperiode der 30 Tage, vor dem Zeitpunkt
    commit(c, "h", "o/a", datetime(2026, 10, 11, 9, 0, tzinfo=web.TZ))
    c.commit()
    with web.gilt("de"):
        k = web.kacheldaten(c, heute, jetzt)
    assert k[1]["vor"] == 1, ("7 Tage: nur der Commit vor 12 Uhr am 4.10. zählt", k[1]["vor"])
    assert k[2]["vor"] == 1, ("30 Tage: der 11.09. 23 Uhr liegt nach dem Zeitpunkt, der 4.10. 9 Uhr zählt", k[2]["vor"])
    montag = heute - timedelta(days=heute.weekday())
    assert web.vorfenster(montag, heute, jetzt, 7) == web.vorwoche_fenster(heute, jetzt), "dieselbe Fensterlogik"


# Befund 3 und 4

@pruefung("c3", "3: der Erstimport setzt bei seiner Seite fort, jeder Lauf erreicht die übrigen Repos, CI und Sicherheit")
def c3():
    db, _ = umgebung()
    import time as zeit_
    from app import sammler
    uhr = [0.0]
    zeit_.monotonic = lambda: uhr[0]
    def seite(_):
        uhr[0] += 400   # jede Seite von o/gross kostet 400 s, die Frist ist 1000 s
    jetzt = datetime.now(timezone.utc)
    welt = {"o/gross": {"id": 1, "branch": "main", "commits": [(f"g{i:03d}", iso(jetzt - timedelta(hours=i))) for i in range(450)]},
            "o/klein": {"id": 2, "branch": "main", "commits": [("k1", iso(jetzt))]}}
    for lauf in range(4):
        uhr[0], protokoll = 0.0, []
        try:
            sammeln(sammler, welt, protokoll, ende=1000.0, uhr={"o/gross": seite})
        except db.FristAbgelaufen as e:
            raise AssertionError(f"Lauf {lauf + 1}: der Erstimport darf den Lauf nicht beenden, {e}")
        with db.verbinden() as c:
            assert c.execute("SELECT COUNT(*) FROM pruefungen WHERE repo = 'o/klein'").fetchone()[0], f"Lauf {lauf + 1}: CI und Sicherheit erreicht"
            assert c.execute("SELECT COUNT(*) FROM commits WHERE repo = 'o/klein'").fetchone()[0] == 1
        if lauf == 1:
            assert ("o/gross", 1) not in protokoll, ("der zweite Lauf setzt fort statt bei Seite 1", protokoll)
    with db.verbinden() as c:
        assert c.execute("SELECT COUNT(*) FROM commits WHERE repo = 'o/gross'").fetchone()[0] == 450
        assert c.execute("SELECT abgerufen_utc FROM repos WHERE repo = 'o/gross'").fetchone()[0], "nach dem letzten Teil gilt der Import als fertig"


def importlauf(sammler, welt, uhr_n, protokoll):
    """Ein Lauf mit Frist 1000 s, jede Seite des Repos uhr_n kostet 400 s: nach der zweiten Seite endet der Teil des Erstimports."""
    import time as zeit_
    uhr = [0.0]
    zeit_.monotonic = lambda: uhr[0]
    def seite(_):
        uhr[0] += 400
    sammeln(sammler, welt, protokoll, ende=1000.0, uhr={uhr_n: seite})


@pruefung("c14", "Bestätigungslauf 1a: ein Force-Push während des Erstimports lässt keine Commits aus, der Import beginnt neu bei Seite 1")
def c14():
    db, _ = umgebung()
    from app import sammler
    alt = datetime.now(timezone.utc) - timedelta(days=60)   # ausserhalb jeder Überlappung des späteren Abrufs
    welt = {"o/a": {"id": 1, "branch": "main", "commits": [(f"f{i:03d}", iso(alt - timedelta(hours=i))) for i in range(250)]}}
    importlauf(sammler, welt, "o/a", [])
    with db.verbinden() as c:
        assert c.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 200 and c.execute("SELECT import_seite FROM repos").fetchone()[0] == 3
    welt["o/a"]["commits"] = welt["o/a"]["commits"][100:]   # Force-Push: die ersten 100 sind weg, der Kopf ist jetzt f100
    protokoll = []
    importlauf(sammler, welt, "o/a", protokoll)
    with db.verbinden() as c:
        fehlt = {f"f{i:03d}" for i in range(100, 250)} - {r[0] for r in c.execute("SELECT sha FROM commits")}
        assert not fehlt, (len(fehlt), "erreichbare Commits übersprungen", sorted(fehlt)[:3])
        assert c.execute("SELECT abgerufen_utc FROM repos").fetchone()[0]
    assert ("o/a", 1) in protokoll, protokoll


@pruefung("c15", "Bestätigungslauf 1b: ein gelöschtes und unter demselben Namen neu angelegtes Repo wird ganz importiert")
def c15():
    db, _ = umgebung()
    from app import sammler
    alt = datetime.now(timezone.utc) - timedelta(days=60)
    welt = {"o/a": {"id": 1, "branch": "main", "commits": [(f"a{i:03d}", iso(alt - timedelta(hours=i))) for i in range(250)]}}
    importlauf(sammler, welt, "o/a", [])
    welt = {"o/a": {"id": 9, "branch": "main", "commits": [(f"n{i:03d}", iso(alt - timedelta(hours=i))) for i in range(250)]}}
    protokoll = []
    importlauf(sammler, welt, "o/a", protokoll)
    assert ("o/a", 1) in protokoll, ("ein anderes Repo beginnt bei Seite 1", protokoll)
    importlauf(sammler, welt, "o/a", [])
    with db.verbinden() as c:
        fehlt = {f"n{i:03d}" for i in range(250)} - {r[0] for r in c.execute("SELECT sha FROM commits")}
        assert not fehlt, (len(fehlt), "Commits des neuen Repos übersprungen", sorted(fehlt)[:3])
        assert c.execute("SELECT abgerufen_utc FROM repos").fetchone()[0]


@pruefung("c4", "4: ausrollprobe.sh führt keine COPY-Quellnamen als Shellcode aus")
def c4():
    werk = Path(tempfile.mkdtemp())
    repo, app = werk / "repo", werk / "app"
    for d in (repo, app):
        (d / "deploy").mkdir(parents=True)
        (d / "tools").mkdir()
    (repo / "Dockerfile").write_text('FROM x\nCOPY ["$(>AUSGEFUEHRT)app.py", "./"]\n')   # ohne Leerzeichen: legt im Arbeitsordner eine Datei an
    for d in (repo, app):
        (d / "app.py").write_text("print(1)\n")
    subprocess.run(["cp", str(WURZEL / "deploy" / "ausrollprobe.sh"), str(repo / "deploy")], check=True)
    subprocess.run(["cp", str(WURZEL / "tools" / "copy-quellen.awk"), str(repo / "tools")], check=True)
    git = lambda *a: git_lauf(*a, cwd=repo)
    git("init", "-q"); git("config", "user.email", "t@example.org"); git("config", "user.name", "t"); git("add", "-A"); git("commit", "-qm", "x")
    # docker exec führt das Skript lokal aus, /app ist der Ordner app
    docker = ('case "$1" in inspect) echo gitpuls:x;; exec) shift 2; [ "$1" = bash ] && [ "$2" = -c ] || exit 9; '
              f's=$(printf %s "$3" | sed "s#/app#{app}#g"); shift 3; a=$1; [ "$a" = /app ] && a={app}; [ $# -gt 0 ] && shift; exec bash -c "$s" "$a" "$@";; esac\n')
    r = subprocess.run(["bash", str(repo / "deploy" / "ausrollprobe.sh"), str(repo), "b"], capture_output=True, text=True,
                       env={**os.environ, "PATH": programme(str(werk / "bin"), docker=docker) + os.pathsep + os.environ["PATH"]})
    assert not (repo / "AUSGEFUEHRT").exists() and not (app / "AUSGEFUEHRT").exists(), ("ein Dateiname wurde als Befehl ausgeführt", r.stdout, r.stderr[-300:])


if __name__ == "__main__":
    if len(sys.argv) > 1:
        PRUEFUNGEN[sys.argv[1]][1]()
        sys.exit(0)
    rot = []
    for name, (befund, _) in PRUEFUNGEN.items():
        r = subprocess.run([sys.executable, "-m", "tests.test_codexaudit", name], cwd=WURZEL, capture_output=True, text=True, timeout=300)
        if r.returncode:
            rot.append(name)
            grund = (r.stderr.strip().splitlines() or ["?"])[-1][:220]
            print(f"rot   {befund}\n      {grund}")
        else:
            print(f"grün  {befund}")
    print(f"{len(PRUEFUNGEN) - len(rot)} von {len(PRUEFUNGEN)} grün" + (f", rot: {', '.join(rot)}" if rot else ""))
    sys.exit(1 if rot else 0)
