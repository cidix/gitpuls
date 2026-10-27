"""Die Befunde des Voll-Audits vom 10.10.2026, je Befund eine Prüfung, die auf 721cde3 rot ist und nach dem Fix grün.
Jede Prüfung läuft in einem eigenen Prozess (eigene Umgebung, eigene Datenbank, eigene Zone):
    python -m tests.test_audit            alle, mit Zusammenfassung, Exit 1 bei einer roten
    python -m tests.test_audit s5         eine einzelne"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import types
from datetime import date, datetime, time, timedelta, timezone
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
PRUEFUNGEN = {}


def pruefung(name, befund):
    def setzen(fn):
        PRUEFUNGEN[name] = (befund, fn)
        return fn
    return setzen



# Git in den temporären Test-Repos ohne globale und System-Konfiguration des Rechners (27.10.2026): ein globaler Hook oder Signierzwang
# lehnte dort sonst jeden Commit ab, und die Prüfung hinge an der Maschine statt am Code.
GIT_ISOLIERT = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git_lauf(*a, **kw):
    return subprocess.run(["git", *a], env={**os.environ, **GIT_ISOLIERT}, check=True, capture_output=True, **kw)

def umgebung(sprache="de", **mehr):
    """Eigene Datenbank und feste Zone vor dem ersten Import von app.*; liefert (db, web)."""
    os.environ.update({"GITPULS_DB": os.path.join(tempfile.mkdtemp(), "audit.db"), "GITPULS_ADRESSEN": "dev@example.org",
                       "GITPULS_SPRACHE": sprache, "GITPULS_FORMAT": "de-CH" if sprache == "de" else "en-US", "TZ": "Europe/Zurich", **mehr})
    from app import db, web
    db.verbinden(schreibend=True).close()
    return db, web


def serverstand():
    spec = spec_from_loader("serverstand", SourceFileLoader("serverstand", str(WURZEL / "tools" / "server-stand")))
    ss = module_from_spec(spec)
    spec.loader.exec_module(ss)
    return ss


def programme(ordner, **skripte):
    """Nachgebaute Befehle als Shell-Skripte in `ordner`."""
    os.makedirs(ordner, exist_ok=True)
    for name, text in skripte.items():
        p = os.path.join(ordner, name)
        Path(p).write_text("#!/bin/sh\n" + text)
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return ordner


def beispiel():
    return json.loads((WURZEL / "tests" / "server.beispiel.json").read_text())


iso = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def commit(c, sha, repo, zeit, adresse="dev@example.org"):
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff, eltern, dateien_geladen) VALUES (?,?,?,?,?,1,1)",
              (sha, repo, iso(zeit) if isinstance(zeit, datetime) else zeit, adresse, f"Betreff {sha}"))


def repo_zeile(c, repo, branch="main", gesehen=None):
    gesehen = gesehen or iso(datetime.now(timezone.utc))
    c.execute("INSERT OR REPLACE INTO repos (repo, default_branch, sprache, pushed_at, gesehen, id, html_url) VALUES (?,?,?,?,?,?,?)",
              (repo, branch, "Python", gesehen, gesehen, abs(hash(repo)) % 10**8, f"https://github.com/{repo}"))
    c.execute("INSERT OR IGNORE INTO laeufe (gestartet_utc, beendet_utc, neu) VALUES (?,?,0)", (gesehen, gesehen))


# Teil A: Hostmessung und Einstellungen

@pruefung("b2", "B2: eine fehlende Hostmessung bricht server-stand nicht ab, die übrigen Werte werden geschrieben")
def b2():
    ss = serverstand()
    os.environ["PATH"] = programme(tempfile.mkdtemp())   # kein docker, systemctl, tailscale, apt-get, ufw, iptables
    daten = ss.messen()
    assert set(daten) >= {"erhoben_utc", "maschine", "zustand", "installiert", "firewall", "dienste"}, daten.keys()
    assert daten["installiert"]["docker"] is None and daten["installiert"]["tailscale"] is None, daten["installiert"]
    assert {"docker", "tailscale", "pakete"} <= set(daten["ungemessen"]), daten.get("ungemessen")
    assert daten["maschine"]["hostname"] and daten["maschine"]["kernel"], "die übrigen Werte stehen trotzdem da"


@pruefung("s11", "S11: verweigerter Zugriff ist nie «fehlt», Neustart nur mit belegtem Mechanismus, «nicht gemessen» in de und en")
def s11():
    ss = serverstand()
    os.environ["PATH"] = programme(tempfile.mkdtemp(), docker='echo "permission denied while trying to connect to the Docker daemon socket" >&2; exit 1\n')
    c = ss.container("app-1")
    assert c["status"] != "fehlt" and c.get("grund") == "Zugriff verweigert", c
    assert ss.messen()["zustand"]["neustart_noetig"] is None, "ohne Marker und ohne Mechanismus ist «kein Neustart nötig» nicht belegt"
    fd_, mech = tempfile.mkstemp()   # nicht mktemp: der Name wäre bis zum Schreiben frei (CodeQL py/insecure-temporary-file)
    os.close(fd_)
    Path(mech).write_text("")
    ss.NEUSTART_MECHANISMUS = (mech,)
    assert ss.messen()["zustand"]["neustart_noetig"] is False
    for sprache, wort in (("de", "nicht gemessen"), ("en", "not measured")):
        r = subprocess.run([sys.executable, "-c", f"""
import json, os
from tests.test_audit import umgebung, beispiel
db, web = umgebung({sprache!r})
s = beispiel()
s["erhoben_utc"] = web.iso_utc()
s["installiert"].update(docker=None, containerd=None)
s["zustand"]["neustart_noetig"] = None
s["ungemessen"] = {{"docker": "docker fehlt", "neustart": "kein Mechanismus"}}
web.SERVER.write_text(json.dumps(s))
h = web.seite()
assert 'class="blockfehler' not in h, "Blockfehler"
i = h.index(">Docker</span>")
assert {wort!r} in h[i:i + 200] and 'title="docker fehlt"' in h[i:i + 200], h[i:i + 200]
"""], cwd=WURZEL, capture_output=True, text=True)
        assert r.returncode == 0, (sprache, r.stderr[-500:])


@pruefung("s8", "S8/S15/S16: Zielpfad, Produkt (Vorgabe leer) und Diensttabelle kommen aus Einstellungen; ein gesetztes GITPULS_STACK aus 1.0 bricht den Start nicht")
def s8():
    ziel = os.path.join(tempfile.mkdtemp(), "server.json")
    os.environ.update(GITPULS_SERVER_ZIEL=ziel, GITPULS_DIENSTE=str(WURZEL / "tools" / "dienste.beispiel.json"))
    os.environ.pop("GITPULS_PRODUKT", None)
    ss = serverstand()
    assert ss.ZIEL == ziel, ss.ZIEL
    versioniert = subprocess.run(["git", "ls-files", "tools/dienste.json"], cwd=WURZEL, capture_output=True, text=True).stdout.strip()
    os.environ["PATH"] = programme(tempfile.mkdtemp())
    d = ss.messen()
    assert d["maschine"]["produkt"] == "", d["maschine"]["produkt"]
    assert [x["name"] for x in d["dienste"]] == [x["name"] for x in json.loads((WURZEL / "tools" / "dienste.beispiel.json").read_text())["dienste"]]
    assert not versioniert and "tools/dienste.json" in (WURZEL / ".gitignore").read_text(), "die eigene Tabelle ist nicht versioniert"
    _, web = umgebung(GITPULS_STACK="Rust, Go")   # entfallen mit 1.1.0, wird übergangen
    assert not hasattr(web, "STACK") and "<html" in web.seite()


@pruefung("s16", "S16: deploy-stand nimmt den Default-Branch je Dienst, überspringt Dienste ohne Container, Tag ohne Commit ist nicht gemessen")
def s16():
    werk = tempfile.mkdtemp()
    os.makedirs(os.path.join(werk, "tools"))
    shutil.copy(WURZEL / "tools" / "deploy-stand.sh", os.path.join(werk, "tools"))
    programme(os.path.join(werk, "tools"), **{"bildrelevant.sh": 'echo "3 2"\n'})
    bin_ = programme(os.path.join(werk, "bin"), docker='case "$2" in alt-1) echo "bild:latest";; *) echo "bild:abc1234";; esac\n',
                     git='case "$3" in\n fetch) exit 0;;\n ls-remote) printf "ref: refs/heads/trunk\\tHEAD\\nabc\\tHEAD\\n";;\n'
                         ' rev-parse) case "$*" in *verify*latest*) exit 1;; *verify*) exit 0;; *origin/trunk*) echo def5678;; *) exit 1;; esac;;\n'
                         ' log) printf "def5678\\tNeu\\n";;\nesac\n')
    tabelle = os.path.join(werk, "dienste.json")
    json.dump({"dienste": [{"name": "A", "repo": "o/a", "container": ["a-1"], "checkout": "/x"}, {"name": "B", "repo": "o/b", "container": ["alt-1"], "checkout": "/y"},
                           {"name": "C", "repo": "o/c", "container": [], "checkout": "/z"}]}, open(tabelle, "w"))
    ziel = os.path.join(werk, "deploy.json")
    r = subprocess.run(["sh", os.path.join(werk, "tools", "deploy-stand.sh")], capture_output=True, text=True,
                       env={**os.environ, "PATH": bin_ + os.pathsep + os.environ["PATH"], "GITPULS_DEPLOY_ZIEL": ziel, "GITPULS_DIENSTE": tabelle})
    assert r.returncode == 0, r.stderr
    d = json.load(open(ziel))
    # je Dienst seit 11.10.2026 (Codex-Audit, Befund 9), das Repo steht im Eintrag
    assert d["A"]["origin_main"] == "def5678" and d["A"].get("branch") == "trunk" and d["A"]["repo"] == "o/a", d["A"]
    assert "latest" in d["B"].get("fehler", ""), d["B"]
    assert "C" not in d and "übersprungen" in r.stderr, (d, r.stderr)


@pruefung("bildrelevant", "Punkt 6: bildrelevant.sh mit pipefail und einem COPY-Parser, der die JSON-Form versteht; ausrollprobe ebenso")
def bildrelevant():
    g = tempfile.mkdtemp()
    git = lambda *a: git_lauf("-C", g, *a, text=True).stdout.strip()
    git("init", "-q"); git("config", "user.email", "a@b"); git("config", "user.name", "a")
    os.makedirs(os.path.join(g, "app"))
    Path(g, "Dockerfile").write_text('FROM x\nCOPY ["app/", "./app/"]\n')
    git("add", "."); git("commit", "-qm", "1")
    von = git("rev-parse", "HEAD")
    Path(g, "app", "x.py").write_text("1"); git("add", "."); git("commit", "-qm", "2")
    Path(g, "liesmich.md").write_text("1"); git("add", "."); git("commit", "-qm", "3")
    aus = subprocess.run([str(WURZEL / "tools" / "bildrelevant.sh"), g, von, "HEAD"], capture_output=True, text=True).stdout.split()
    assert aus == ["2", "1"], ("JSON-Form: der Commit an app/ zählt", aus)
    git("rm", "-q", "Dockerfile"); git("commit", "-qm", "4")
    r = subprocess.run([str(WURZEL / "tools" / "bildrelevant.sh"), g, von, "HEAD"], capture_output=True, text=True)
    assert r.returncode != 0, ("ohne Dockerfile scheitert git show in der Pipe, das darf nicht still durchgehen", r.stdout)
    probe = (WURZEL / "deploy" / "ausrollprobe.sh").read_text()
    assert "pipefail" in probe and "copy-quellen.awk" in probe, "ausrollprobe mit pipefail und demselben Parser"


@pruefung("s9", "S9: kein fest gesetztes TZ=Europe/Zurich in deploy/*.service, die Zone kommt aus der .env wie bei compose.yaml")
def s9():
    for unit in sorted((WURZEL / "deploy").glob("*.service")):
        text = unit.read_text()
        assert "Europe/Zurich" not in text, unit.name
        if "docker compose" not in text:
            assert re.search(r"^EnvironmentFile=-/\S+/gitpuls/\.env$", text, re.M), f"{unit.name}: Host-Skript ohne die .env"


@pruefung("s24", "S24: die Uhrzeit im Laufstatus wird in der Zone des Servers formatiert, nicht in der des Browsers")
def s24():
    _, web = umgebung()
    with web.gilt("de"):
        js = web.skript()
    assert '"zone": "Europe/Zurich"' in js, js[:300]
    assert re.search(r"timeZone\s*=\s*T\.zone|timeZone:\s*T\.zone", js), "die Uhrzeit nimmt die Zone des Servers"


@pruefung("s25", "S25: die Timer-Umrechnung behält die Zonenbezeichnung (02:30 CEST = 00:30Z, 02:30 CET = 01:30Z)")
def s25():
    os.environ["TZ"] = "Europe/Zurich"
    import time as t_
    t_.tzset()
    ss = serverstand()
    assert ss.zeit("Sun 2026-10-25 02:30:00 CEST") == "2026-10-25T00:30:00Z", ss.zeit("Sun 2026-10-25 02:30:00 CEST")
    assert ss.zeit("Sun 2026-10-25 02:30:00 CET") == "2026-10-25T01:30:00Z", ss.zeit("Sun 2026-10-25 02:30:00 CET")
    assert ss.zeit("Sun 2026-10-25 02:30:00 UTC") == "2026-10-25T02:30:00Z"


@pruefung("s28", "S28: jeder Test, der app.* lädt, setzt seine Zone selbst")
def s28():
    for p in sorted((WURZEL / "tests").glob("test_*.py")) + [WURZEL / "tests" / "gegenprobe.py"]:
        text = p.read_text()
        if re.search(r"^from app import|^from app\.|^import app", text, re.M) and p.name != "test_audit.py":
            i = text.index(next(m.group(0) for m in re.finditer(r"^from app import|^from app\.|^import app", text, re.M)))
            assert 'os.environ["TZ"]' in text[:i], f"{p.name}: setzt TZ nicht vor dem Import"


@pruefung("s17", "S17: Zweigbefunde, Lage- und Servertexte nennen den tatsächlichen Default-Branch statt fest main")
def s17():
    db, web = umgebung()
    c = db.verbinden(schreibend=True)
    repo_zeile(c, "o/a", branch="trunk")
    commit(c, "a1", "o/a", datetime.now(timezone.utc) - timedelta(days=2))
    c.execute("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc) VALUES ('o/a', 'wip', 2, 0, ?)", (iso(datetime.now(timezone.utc) - timedelta(days=9)),))
    c.commit(); c.close()
    h = web.seite()
    lage = h[h.index('<div class="lage">'):h.index('<div class="tiles3">')]
    assert "wartet auf trunk" in lage and "main" not in lage, re.sub(r"<[^>]+>", " ", lage)[:400]


# Firewall, Updates, Container, Deploy

VPS_FILTER = """*filter
:INPUT ACCEPT [0:0]
:FORWARD DROP [0:0]
:OUTPUT ACCEPT [5:6]
:DOCKER - [0:0]
:DOCKER-USER - [0:0]
:ts-input - [0:0]
-A INPUT -j ts-input
-A INPUT -i lo -j ACCEPT
-A INPUT -p tcp -m tcp --dport 22 -j ACCEPT
-A INPUT -j DROP
-A FORWARD -j DOCKER-USER
-A FORWARD -j ts-forward
-A DOCKER -d 172.30.0.2/32 ! -i br-0a1b2c3d4e5f -o br-0a1b2c3d4e5f -p tcp -m tcp --dport 8789 -j ACCEPT
-A DOCKER-USER -i eth0 -j DROP
-A ts-input -i tailscale0 -j ACCEPT
COMMIT
"""


@pruefung("s10", "S10: der Firewall-Vergleich umfasst den ganzen wirksamen Regelsatz samt Policies und Sprüngen")
def s10():
    ss = serverstand()
    fd_, gespeichert = tempfile.mkstemp()   # nicht mktemp: der Name wäre bis zum Schreiben frei (CodeQL py/insecure-temporary-file)
    os.close(fd_)
    ss.FAMILIEN = (("IPv4", "iptables-save", gespeichert),)
    ss.subprocess = types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(stdout="enabled\n"), CalledProcessError=subprocess.CalledProcessError,
                                          SubprocessError=subprocess.SubprocessError, TimeoutExpired=subprocess.TimeoutExpired)
    boot = datetime(2026, 10, 1, tzinfo=timezone.utc)
    def fw(laufend):
        ss.lauf = lambda *cmd: laufend
        return ss.firewall(boot)["wie_gespeichert"]
    Path(gespeichert).write_text(VPS_FILTER)
    assert fw(VPS_FILTER), "gleicher Stand"
    assert fw(VPS_FILTER.replace("172.30.0.2", "172.31.0.9").replace(":OUTPUT ACCEPT [5:6]", ":OUTPUT ACCEPT [9:9]")), "Docker-Ketten und Zähler zählen nicht"
    assert not fw(VPS_FILTER.replace(":INPUT ACCEPT [0:0]", ":INPUT DROP [0:0]")), "eine geänderte Policy ist eine Abweichung"
    assert not fw(VPS_FILTER.replace("-A FORWARD -j DOCKER-USER\n", "")), "ein fehlender Sprung ist eine Abweichung"
    assert not fw(VPS_FILTER.replace("-A INPUT -j ts-input\n", "")), "auch der Sprung in tailscaleds Kette"


APT_SIM = """Inst libfoo1 [1.0] (1.1 Debian:13.7/stable [amd64])
Inst tzdata [2025a] (2025b Debian:13-updates/stable-updates [all])
Inst docker-ce [1] (2 Docker CE:trixie [amd64])
"""
APT_POLICY = """Package files:
 500 https://download.docker.com/linux/debian trixie/stable amd64 Packages
     release o=Docker,a=trixie,l=Docker CE,c=stable,b=amd64
 500 http://deb.debian.org/debian trixie-updates/main amd64 Packages
     release v=13-updates,o=Debian,a=stable-updates,n=trixie-updates,l=Debian,c=main,b=amd64
 500 http://deb.debian.org/debian trixie/main amd64 Packages
     release v=13.7,o=Debian,a=stable,n=trixie,l=Debian,c=main,b=amd64
"""
APT_CONFIG = """APT::Periodic::Unattended-Upgrade "1";
Unattended-Upgrade::Origins-Pattern "";
Unattended-Upgrade::Origins-Pattern:: "origin=Debian,codename=${distro_codename},label=Debian";
Unattended-Upgrade::Origins-Pattern:: "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
"""


@pruefung("s12", "S12: automatische Updates nur behauptet, wenn unattended-upgrades aktiv ist und die erlaubten Quellen das Paket decken")
def s12():
    _, web = umgebung()
    z = {"pakete_quellen": {"Debian:stable": 1, "Debian:stable-updates": 2}, "quellen_auto": {"Debian:stable": True, "Debian:stable-updates": False},
         "unattended_upgrades": True}
    assert web.von_hand(z) == {"Debian:stable-updates": 2}, web.von_hand(z)
    assert web.von_hand({**z, "unattended_upgrades": False}) == z["pakete_quellen"], "nicht aktiv: nichts ist automatisch"
    s = beispiel()
    s["zustand"].update(pakete_upgradebar=3, pakete_quellen=z["pakete_quellen"], quellen_auto={}, unattended_upgrades=True)
    with web.gilt("de"):
        h = web.fuss_updates(s)
    assert "deckt der Automatismus" not in h, ("ohne gemessene Deckung keine Behauptung", h)
    ss = serverstand()
    ss.lauf = lambda *cmd: {"apt-get": APT_SIM, "apt-cache": APT_POLICY, "apt-config": APT_CONFIG}[cmd[0]]
    quellen, auto = ss.pakete({"ID": "debian", "VERSION_CODENAME": "trixie"})
    assert quellen == {"Debian:stable": 1, "Debian:stable-updates": 1, "Docker CE:trixie": 1}, quellen
    assert auto == {"Debian:stable": True, "Debian:stable-updates": False, "Docker CE:trixie": False}, auto


@pruefung("s13", "S13: ein Container mit unhealthy zählt in Lagesatz und Zustandspunkt als Befund")
def s13():
    _, web = umgebung()
    s = beispiel()
    s["zustand"]["neustart_noetig"] = False
    s["dienste"][2]["timer"] = [t for t in s["dienste"][2]["timer"] if t["status"] != "fehlt"]
    s["dienste"][0]["container"][0]["gesund"] = "unhealthy"
    with web.gilt("de"):
        satz, ab = web.server_lage(s)
        h = web.server_dienste_block(s, datetime.now(timezone.utc))
    assert any("unhealthy" in a for a in ab), satz
    erste = h[:h.index("</summary>")]
    assert 'class="punkt glut"' in erste, erste


@pruefung("s14", "S14: eine deploy.json, älter als zwei Takte des Deploy-Timers, erscheint als veraltet")
def s14():
    db, web = umgebung()
    jetzt = datetime.now(timezone.utc)
    s = beispiel()
    s["erhoben_utc"] = iso(jetzt)
    dp = {"tag": "abc1234", "origin_main": "abc1234", "hinter": 0, "bildrelevant": 0, "commits": [], "gemessen": iso(jetzt - timedelta(hours=3))}
    s["dienste"][2]["deploy"] = dp
    with web.gilt("de"):
        h = web.server_dienste_block(s, jetzt)
    teil = h[h.index("gitpuls-gitpuls-1"):]
    assert "veraltet" in teil[:teil.index("</details>")], teil[:600]
    web.SERVER.write_text(json.dumps(s))
    web.DEPLOY.write_text(json.dumps({s["dienste"][2]["name"]: {**dp, "repo": s["dienste"][2]["repo"]}}))   # je Dienst seit 11.10.2026
    c = db.verbinden(schreibend=True)
    repo_zeile(c, s["dienste"][2]["repo"])
    c.commit(); c.close()
    with db.verbinden() as c, web.gilt("de"):
        k = next(k for k in web.projekte(c, jetzt.astimezone(web.TZ).date(), jetzt.astimezone(web.TZ)) if k["repo"] == s["dienste"][2]["repo"])
    assert k["messung"] and "veraltet" in k["messung"][0][1], k["messung"]


@pruefung("s7", "S7: compose.yaml läuft lokal (Volume, Tokendatei, Port aus Einstellungen), Takt im Container startet den Sammellauf")
def s7():
    text = (WURZEL / "compose.example.yaml").read_text()
    for teil in ("${GITPULS_TOKENDATEI:-./github.env}", "name: ${GITPULS_VOLUME:-gitpuls-daten}",
                 "127.0.0.1:${GITPULS_PORT:-8789}:8789", "GITPULS_TAKT_MIN"):
        assert teil in text, teil
    assert "/etc/gitpuls/github.env:/etc" not in text, "die Tokendatei des Hosts ist eine Einstellung"
    # Kein interpolierter external-Wert taugt für Docker Compose und podman-compose zugleich (Probe Podman 11.10.2026)
    assert not any(z.strip().startswith("external:") for z in text.splitlines()), "external gehört in compose.override.yaml"
    assert "GITPULS_VOLUME_EXTERN" not in (WURZEL / ".env.example").read_text(), ".env.example: GITPULS_VOLUME_EXTERN entfernen"
    _, web = umgebung()
    gestartet = []
    web.lauf_starten = lambda: gestartet.append(1)
    def halt(_):
        raise KeyboardInterrupt
    web.time.sleep = halt
    try:
        web.takt()
    except KeyboardInterrupt:
        pass
    assert gestartet == [1], gestartet


@pruefung("s6", "S6: deploy/github.env.beispiel nennt alle nötigen Rechte")
def s6():
    text = (WURZEL / "deploy" / "github.env.beispiel").read_text()
    for recht in ("Contents", "Metadata", "Actions", "Dependabot alerts"):
        assert recht in text, recht


@pruefung("dienstetabelle", "Bestätigungslauf: eine fehlende Diensttabelle ist «nicht gemessen» mit Grund, deploy-stand endet mit Exit ungleich 0")
def dienstetabelle():
    werk = tempfile.mkdtemp()
    os.makedirs(os.path.join(werk, "tools"))
    for name in ("server-stand", "deploy-stand.sh"):
        shutil.copy(WURZEL / "tools" / name, os.path.join(werk, "tools", name))
    os.environ.pop("GITPULS_DIENSTE", None)
    os.environ["PATH"] = programme(tempfile.mkdtemp()) + os.pathsep + os.environ["PATH"]
    spec = spec_from_loader("ss", SourceFileLoader("ss", os.path.join(werk, "tools", "server-stand")))
    ss = module_from_spec(spec)
    spec.loader.exec_module(ss)
    d = ss.messen()
    assert "Diensttabelle" in d["ungemessen"].get("dienste", ""), d["ungemessen"]
    r = subprocess.run(["bash", os.path.join(werk, "tools", "deploy-stand.sh")], capture_output=True, text=True,
                       env={**os.environ, "GITPULS_DEPLOY_ZIEL": os.path.join(werk, "deploy.json")})
    assert r.returncode != 0 and "Diensttabelle" in r.stderr + r.stdout, (r.returncode, r.stdout, r.stderr)
    for sprache, wort in (("de", "Dienste nicht gemessen"), ("en", "Services not measured")):
        r = subprocess.run([sys.executable, "-c", f"""
import json
from tests.test_audit import umgebung, beispiel
db, web = umgebung({sprache!r})
s = beispiel(); s["erhoben_utc"] = web.iso_utc(); s["dienste"] = []
s["ungemessen"] = {{"dienste": {d["ungemessen"]["dienste"]!r}}}
web.SERVER.write_text(json.dumps(s))
h = web.seite()
assert {wort!r} in h and "Diensttabelle" in h, h[h.index('class="tile dienste"'):][:300]
"""], cwd=WURZEL, capture_output=True, text=True)
        assert r.returncode == 0, (sprache, r.stderr[-400:])


# Teil B: Sammler

def welt_fakes(github, welt, protokoll, uhr=None):
    """Nachgebautes GitHub über alle_seiten und holen. welt: Repo -> {"branch", "commits": [(sha, datum)], "seiten": n, "zweige": [...]}."""
    def alle_seiten(pfad, params=None, leer_bei_409=False):
        if pfad == "user/repos":
            return iter([{"full_name": n, "id": i + 1, "archived": False, "pushed_at": "2026-10-10T00:00:00Z", "default_branch": w["branch"],
                          "language": "Python", "html_url": f"https://github.com/{n}"} for i, (n, w) in enumerate(welt.items())])
        n = "/".join(pfad.split("/")[1:3])
        if pfad.endswith("/branches"):
            return iter([{"name": z, "commit": {"sha": "k" + z}} for z in welt[n].get("zweige", [])])
        def seiten():
            cs = [(s_, d) for s_, d in welt[n]["commits"] if not (params or {}).get("since") or d >= params["since"]]   # wie GitHub: ab since
            for nr in range(0, max(len(cs), 1), 100):
                protokoll.append((n, nr // 100 + 1))
                if uhr and n in uhr:
                    uhr[n](nr // 100 + 1)
                yield from [{"sha": s, "commit": {"author": {"date": d, "email": "dev@example.org"}, "message": "x"}, "author": None, "parents": [{}]}
                            for s, d in cs[nr:nr + 100]]
        return seiten()

    def holen(pfad, params=None, leer_bei_409=False):
        protokoll.append(("holen", pfad, dict(params or {})))
        if "/actions/" in pfad:
            return {"total_count": 0, "workflow_runs": []}, {}
        if "/dependabot/" in pfad:
            return [], {}
        if "/compare/" in pfad:
            return {"ahead_by": 1, "behind_by": 0}, {}
        if "/commits/" in pfad:
            return {"commit": {"author": {"date": "2026-10-01T00:00:00Z"}}, "files": [], "parents": [{}]}, {}
        raise AssertionError(pfad)
    return alle_seiten, holen


def sammeln(sammler, github, welt, protokoll, **k):
    alt = github.alle_seiten, github.holen
    github.alle_seiten, github.holen = welt_fakes(github, welt, protokoll, k.pop("uhr", None))
    try:
        return sammler.main(**k)
    finally:
        github.alle_seiten, github.holen = alt


@pruefung("b4", "B4: Commits je Seite festgeschrieben, Abrufstand je Repo sofort, Frist vor jeder weiteren Seite")
def b4():
    db, _ = umgebung()
    import time as zeit_
    from app import github, sammler
    uhr = [1000.0]
    zeit_.monotonic = lambda: uhr[0]
    ende = 1500.0
    def ueber_frist(seite):
        if seite == 1:
            uhr[0] = 2000.0   # nach der ersten Seite von b ist die Frist um
    jetzt = datetime.now(timezone.utc)
    welt = {"o/a": {"branch": "main", "commits": [(f"a{i}", iso(jetzt - timedelta(hours=i))) for i in range(3)]},
            "o/b": {"branch": "main", "commits": [(f"b{i:03d}", iso(jetzt - timedelta(hours=i))) for i in range(300)]}}
    protokoll = []
    try:
        sammeln(sammler, github, welt, protokoll, ende=ende, uhr={"o/b": ueber_frist})
        raise AssertionError("die Frist hätte den Lauf beenden müssen")
    except db.FristAbgelaufen:
        pass
    assert ("o/b", 2) not in protokoll, ("nach Ablauf der Frist keine weitere Seite", protokoll)
    with db.verbinden() as c:
        stand_a = c.execute("SELECT abgerufen_utc FROM repos WHERE repo = 'o/a'").fetchone()[0]
        n_b = c.execute("SELECT COUNT(*) FROM commits WHERE repo = 'o/b'").fetchone()[0]
    assert stand_a, "o/a ist vollständig geholt und hat seinen Stand, obwohl der Lauf danach abbrach"
    assert n_b == 100, ("die erste Seite von o/b ist festgeschrieben", n_b)
    uhr[0], protokoll[:] = 1000.0, []
    sammeln(sammler, github, welt, protokoll, ende=ende)
    with db.verbinden() as c:
        assert c.execute("SELECT COUNT(*) FROM commits WHERE repo = 'o/b'").fetchone()[0] == 300, "der nächste Lauf holt o/b fertig"


@pruefung("s5", "S5: Parameter und Zweignamen in API-URLs korrekt kodiert (release&x=1, fix#42)")
def s5():
    umgebung()
    from urllib.parse import parse_qs, urlsplit
    from app import github, sammler
    urls = []
    class Antwort:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"
    def urlopen(req, timeout=None):
        urls.append(req.full_url)
        return Antwort()
    github.urllib.request.urlopen = urlopen
    os.environ["GITHUB_TOKEN"] = "x"
    github.json.load = lambda r: {}
    github.holen("repos/o/a/actions/runs", {"branch": "release&x=1", "per_page": 1})
    assert parse_qs(urlsplit(urls[-1]).query)["branch"] == ["release&x=1"], urls[-1]
    protokoll = []
    welt = {"o/a": {"branch": "main", "commits": [], "zweige": ["fix#42"]}}
    sammeln(sammler, github, welt, protokoll)
    vergleich = [p[1] for p in protokoll if p[0] == "holen" and "/compare/" in p[1]]
    assert vergleich and "fix%2342" in vergleich[0] and "#" not in vergleich[0], vergleich
    seiten = iter([([], {"Link": '<https://api.github.com/x?after=Y3Vy%3D&per_page=100>; rel="next"'}), ([], {})])
    nach = []
    sammler.github.holen = lambda pfad, params=None, leer_bei_409=False: (nach.append(dict(params)), next(seiten))[1]
    sammler.warnungen_von("o/a")
    assert nach[1]["after"] == "Y3Vy=", ("der Cursor kommt dekodiert an holen, das ihn einmal kodiert", nach[1])


@pruefung("s3", "S3: beim Umbenennen ziehen pruefungen und warnungen auf den neuen Namen um")
def s3():
    db, _ = umgebung()
    from app import sammler
    c = db.verbinden(schreibend=True)
    c.execute("INSERT INTO pruefungen (repo, ci, sicherheit, gesehen, warnungen) VALUES ('o/alt', 'ok', 'ok', 'x', 1)")
    c.execute("INSERT INTO warnungen (repo, nummer, paket, url) VALUES ('o/alt', 1, 'p', 'u')")
    c.commit()
    sammler.umbenennen(c, "o/alt", "o/neu")
    assert [tuple(r) for r in c.execute("SELECT repo FROM pruefungen")] == [("o/neu",)]
    assert [tuple(r) for r in c.execute("SELECT repo FROM warnungen")] == [("o/neu",)]


@pruefung("s4", "S4 zurückgenommen: ein Wechsel des Default-Branch holt den neuen ganz, die Commits des alten bleiben (bekannte Grenze, S1)")
def s4():
    db, _ = umgebung()
    from app import github, sammler
    jetzt = datetime.now(timezone.utc)
    welt = {"o/a": {"branch": "main", "commits": [("m1", iso(jetzt - timedelta(days=3))), ("s1", iso(jetzt - timedelta(days=4)))]}}
    sammeln(sammler, github, welt, [])
    welt["o/a"] = {"branch": "trunk", "commits": [("t1", iso(jetzt - timedelta(days=1))), ("s1", iso(jetzt - timedelta(days=4)))]}
    sammeln(sammler, github, welt, [])
    with db.verbinden() as c:
        shas = {r[0] for r in c.execute("SELECT sha FROM commits WHERE repo = 'o/a'")}
    assert shas == {"m1", "s1", "t1"}, shas


@pruefung("s4_gemeinsam", "S4 zurückgenommen: ein Commit, den zwei Repos tragen, übersteht den Branch-Wechsel eines der beiden")
def s4_gemeinsam():
    db, _ = umgebung()
    from app import github, sammler
    jetzt = datetime.now(timezone.utc)
    # x1 liegt vor der Überlappung von sieben Tagen: o/b holt ihn im zweiten Lauf nicht noch einmal, er muss bleiben.
    welt = {"o/a": {"branch": "main", "commits": [("x1", iso(jetzt - timedelta(days=30)))]},
            "o/b": {"branch": "main", "commits": [("x1", iso(jetzt - timedelta(days=30))), ("b1", iso(jetzt - timedelta(days=2)))]}}
    sammeln(sammler, github, welt, [])
    welt["o/a"] = {"branch": "trunk", "commits": [("t1", iso(jetzt - timedelta(days=1)))]}
    sammeln(sammler, github, welt, [])
    with db.verbinden() as c:
        assert c.execute("SELECT COUNT(*) FROM commits WHERE sha = 'x1'").fetchone()[0] == 1, "der gemeinsame Commit bleibt"


@pruefung("o1", "O1: ein globaler Fehler beim Nachladen der Codezeilen steht im Laufstatus")
def o1():
    db, _ = umgebung()
    from app import github, sammler
    jetzt = datetime.now(timezone.utc)
    welt = {"o/a": {"branch": "main", "commits": [("c1", iso(jetzt - timedelta(days=1)))]}}
    alt = sammler.dateien_eines
    def global_(repo, sha):
        e = github.GitHubFehler("GitHub nicht erreichbar: nachgebaut")
        raise e
    sammler.dateien_eines = global_
    try:
        sammeln(sammler, github, welt, [])
    finally:
        sammler.dateien_eines = alt
    lj = json.loads(sammler.LAUF.read_text())
    assert lj["exit"] == 0 and "Codezeilen" in (lj.get("fehler") or ""), lj


# Teil C: Anzeige und Zugriff

@pruefung("s18", "S18: Zukunftscommits enden überall bei jetzt, auch im 30-Tage-Fenster; nur Zukunftscommits ergeben keinen Blockfehler")
def s18():
    db, web = umgebung()
    jetzt = datetime.now(timezone.utc).replace(microsecond=0)
    zh = jetzt.astimezone(web.TZ)
    c = db.verbinden(schreibend=True)
    repo_zeile(c, "o/a")
    commit(c, "z1", "o/a", jetzt + timedelta(days=3))
    commit(c, "z2", "o/a", jetzt + timedelta(days=40))
    c.commit()
    h = web.seite(jetzt=zh)
    assert 'class="blockfehler' not in h, re.findall(r'data-block="([^"]+)"', h)
    spaeter = min(jetzt + timedelta(seconds=30), datetime.combine(zh.date(), time(23, 59, 59), web.TZ).astimezone(timezone.utc))
    if spaeter > jetzt:   # ein Zukunftscommit am heutigen Kalendertag
        commit(c, "z0", "o/a", spaeter)
        c.commit()
        with db.verbinden() as c2:
            k30 = web.kacheldaten(c2, zh.date(), zh)[2]
        assert k30["n"] == 0, ("der Zukunftscommit von heute zählt im 30-Tage-Fenster nicht", k30["n"])
    c.close()


@pruefung("s19", "S19: Wochenrang über alle Kalenderwochen seit Beginn, verglichen wirklich zum selben Zeitpunkt")
def s19():
    db, web = umgebung()
    zh = datetime(2026, 10, 7, 12, 0, tzinfo=web.TZ)   # Mittwoch
    c = db.verbinden(schreibend=True)
    repo_zeile(c, "o/a")
    commit(c, "w3", "o/a", datetime(2026, 9, 14, 10, 0, tzinfo=web.TZ))   # Montag vor drei Wochen
    commit(c, "w1", "o/a", datetime(2026, 9, 30, 13, 0, tzinfo=web.TZ))   # Mittwoch der Vorwoche, eine Stunde nach «jetzt»
    c.commit(); c.close()
    h = web.seite(jetzt=zh)
    assert "zum selben Zeitpunkt Rang 2 von 4 Wochen" in h, re.findall(r"Rang \d+ von \d+ Wochen?", h)


@pruefung("s20", "S20: «heute wieder aktiv» erscheint nur bei einem Commit von heute")
def s20():
    db, web = umgebung()
    zh = datetime(2026, 10, 7, 12, 0, tzinfo=web.TZ)
    c = db.verbinden(schreibend=True)
    repo_zeile(c, "o/a")
    commit(c, "g1", "o/a", datetime(2026, 10, 6, 15, 0, tzinfo=web.TZ))   # nur gestern
    c.commit(); c.close()
    h = web.seite(jetzt=zh)
    assert "Heute wieder aktiv" not in h and "heute wieder aktiv" not in h, re.sub(r"<[^>]+>", " ", h[h.index('<div class="lage">'):][:600])


@pruefung("s26", "S26: die Load heisst Last und wird nicht als Belegung der Kerne erklärt")
def s26():
    for sprache, wort, falsch in (("de", ">Last</span>", "Kerne sind im Mittel belegt"), ("en", ">Load</span>", "cores are busy")):
        r = subprocess.run([sys.executable, "-c", f"""
import json
from tests.test_audit import umgebung, beispiel
db, web = umgebung({sprache!r})
s = beispiel(); s["erhoben_utc"] = web.iso_utc(); web.SERVER.write_text(json.dumps(s))
h = web.seite()
assert {wort!r} in h, "Bezeichnung"
assert {falsch!r} not in h, "Erklärung als Belegung"
assert "Auslastung" not in h
"""], cwd=WURZEL, capture_output=True, text=True)
        assert r.returncode == 0, (sprache, r.stderr[-400:])


def server_starten(web):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pruefung("s27", "S27: Blockfehler je Anfrage, ein gleichzeitiger zweiter Aufbau löscht den Fehler des ersten nicht")
def s27():
    import urllib.error
    import urllib.request
    _, web = umgebung()
    web.Handler.log_message = lambda *a: None
    srv, adr = server_starten(web)
    erste_wartet, weiter = threading.Event(), threading.Event()
    zaehler = {"rb": 0, "fr": 0}
    echt_rb, echt_fr = web.rueckblick_block, web.fusszeile
    def rb(*a):
        zaehler["rb"] += 1
        if zaehler["rb"] == 1:
            raise RuntimeError("nachgebaut")
        return echt_rb(*a)
    def fr(*a):
        zaehler["fr"] += 1
        if zaehler["fr"] == 1:
            erste_wartet.set()
            weiter.wait(10)
        return echt_fr(*a)
    web.rueckblick_block, web.fusszeile = rb, fr
    ergebnis = {}
    def holen(name):
        try:
            ergebnis[name] = urllib.request.urlopen(adr + "/gesund", timeout=20).status
        except urllib.error.HTTPError as e:
            ergebnis[name] = e.code
    a = threading.Thread(target=holen, args=("a",))
    a.start()
    assert erste_wartet.wait(10)
    holen("b")
    weiter.set()
    a.join(20)
    srv.shutdown()
    assert ergebnis == {"a": 503, "b": 200}, ergebnis


@pruefung("o2", "O2: server.json und deploy.json mit falschem Wurzeltyp gelten wie ungültiges JSON, seit 11.10.2026 als «nicht lesbar» mit Grund")
def o2():
    # Bis 11.10.2026 hiess das «Noch kein Serverstand»; der Codex-Audit (Befund 6) verlangt eine sichtbare ausgefallene Messung.
    _, web = umgebung()
    for wurzel in ("[]", "3", '"x"'):
        web.SERVER.write_text(wurzel)
        web.DEPLOY.write_text(wurzel)
        h = web.seite()
        assert 'class="blockfehler' not in h and "Serverstand nicht lesbar" in h, (wurzel, re.findall(r'data-block="([^"]+)"', h))
    ss = serverstand()
    ss.ZIEL = str(web.SERVER)
    eintraege, fehler = ss.deploy_lesen()
    assert eintraege == {} and "kein Objekt" in fehler, fehler


@pruefung("n5", "N5: POST /lauf nimmt nur Anfragen vom eigenen Ursprung an, sonst 403")
def n5():
    import urllib.error
    import urllib.request
    _, web = umgebung()
    web.Handler.log_message = lambda *a: None
    web.lauf_starten = lambda: (202, {"gestartet": "x"})
    srv, adr = server_starten(web)
    def post(**kopf):
        try:
            return urllib.request.urlopen(urllib.request.Request(adr + "/lauf", method="POST", data=b"", headers=kopf), timeout=10).status
        except urllib.error.HTTPError as e:
            return e.code
    try:
        assert post(**{"Sec-Fetch-Site": "cross-site", "Origin": "https://boese.example"}) == 403
        assert post(Origin="https://boese.example") == 403
        assert post() == 403, "ohne Ursprung kein Lauf"
        assert post(**{"Sec-Fetch-Site": "same-origin"}) == 202
        assert post(Origin=adr) == 202
    finally:
        srv.shutdown()


# Teil D: Demo

@pruefung("b1", "B1: demodaten.py schreibt nur in einen leeren oder eigenen Ordner und löscht nie eine fremde Datei")
def b1():
    sys.path.insert(0, str(WURZEL / "demo"))
    fremd = Path(tempfile.mkdtemp())
    (fremd / "gitpuls.db").write_text("echte Daten")
    r = subprocess.run([sys.executable, str(WURZEL / "demo" / "demodaten.py"), str(fremd)], capture_output=True, text=True)
    assert r.returncode != 0 and (fremd / "gitpuls.db").read_text() == "echte Daten", ("fremder Ordner bleibt unberührt", r.stdout, r.stderr[-300:])
    eigen = Path(tempfile.mkdtemp()) / "demo"
    assert subprocess.run([sys.executable, str(WURZEL / "demo" / "demodaten.py"), str(eigen)], capture_output=True).returncode == 0
    (eigen / "notiz.txt").write_text("von Hand")
    assert subprocess.run([sys.executable, str(WURZEL / "demo" / "demodaten.py"), str(eigen), "--szene", "ruhig"], capture_output=True).returncode == 0
    assert (eigen / "notiz.txt").read_text() == "von Hand", "eine Datei, die das Skript nicht erzeugt hat, bleibt"


@pruefung("b3", "B3: die Aufnahmeinstanz bindet an 127.0.0.1 und startet ohne GITHUB_TOKEN und GITPULS_GITHUB_ENV")
def b3():
    js = (WURZEL / "demo" / "aufnahmen.mjs").read_text()
    starten = js[js.index("async function starten"):]
    starten = starten[:starten.index("spawn(")]
    for teil in ("delete env.GITHUB_TOKEN", "delete env.GITPULS_GITHUB_ENV", "env.GITPULS_BIND = '127.0.0.1'"):
        assert teil in starten, teil
    _, web = umgebung(GITPULS_BIND="127.0.0.1")
    assert web.BIND == "127.0.0.1"


def release_welt():
    """Ein Repo mit Releases für tools/deploy-stand.sh (11.10.2026): c0 = v1.0.2, c1 = v1.0.3, c2 berührt das Bild ohne Release, c3 nur die
    README. Container A läuft aus dem Release-Bild 1.0.3, B aus 1.0.2 (beide mit den OCI-Labels von release.yml), C aus dem Commit c2."""
    werk = Path(tempfile.mkdtemp())
    ursprung, klon = werk / "ursprung", werk / "klon"
    git = lambda *a, cwd=ursprung: git_lauf(*a, cwd=cwd, text=True).stdout.strip()
    ursprung.mkdir()
    git("init", "-q", "-b", "main"); git("config", "user.email", "t@example.org"); git("config", "user.name", "t")
    (ursprung / "Dockerfile").write_text("FROM x\nCOPY app.py ./\n")
    shas = []
    for i, (datei, tag) in enumerate((("app.py", "v1.0.2"), ("app.py", "v1.0.3"), ("app.py", None), ("README.md", None))):
        (ursprung / datei).write_text(f"{i}\n")
        git("add", "-A"); git("commit", "-qm", f"c{i}")
        shas.append(git("rev-parse", "HEAD"))
        if tag:
            git("tag", "-a", tag, "-m", tag)
    git("clone", "-q", str(ursprung), str(klon), cwd=werk)
    (werk / "tools").mkdir()
    for f in ("deploy-stand.sh", "bildrelevant.sh", "copy-quellen.awk"):
        shutil.copy(WURZEL / "tools" / f, werk / "tools")
    docker = ('case "$*" in\n'
              f' *a-1*image.version*) echo 1.0.3;; *a-1*image.revision*) echo {shas[1]};; *a-1*) echo ghcr.io/cidix/gitpuls:1.0.3;;\n'
              f' *b-1*image.version*) echo 1.0.2;; *b-1*image.revision*) echo {shas[0]};; *b-1*) echo ghcr.io/cidix/gitpuls:1.0.2;;\n'
              f' *c-1*Labels*) echo;; *c-1*) echo gitpuls:{shas[2][:7]};;\nesac\n')
    tab = werk / "dienste.json"
    tab.write_text(json.dumps({"dienste": [{"name": n, "repo": "o/x", "checkout": str(klon), "container": [c]} for n, c in (("A", "a-1"), ("B", "b-1"), ("C", "c-1"))]}))
    ziel = werk / "deploy.json"
    r = subprocess.run(["bash", str(werk / "tools" / "deploy-stand.sh")], capture_output=True, text=True,
                       env={**os.environ, "PATH": programme(str(werk / "bin"), docker=docker) + os.pathsep + os.environ["PATH"],
                            "GITPULS_DIENSTE": str(tab), "GITPULS_DEPLOY_ZIEL": str(ziel)})
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(ziel.read_text()), shas


@pruefung("release-neuestes", "Release-Bild auf dem neuesten Release: verglichen mit v*, nicht mit main, kein Befund")
def release_neuestes():
    d, shas = release_welt()
    a = d["A"]
    assert "fehler" not in a and a["tag"] == "1.0.3" and a["hinter"] == 0 and a["bildrelevant"] == 0 and a["gleich"] is True and a["commits"] == [], a
    assert a["branch"] == "v1.0.3" and a["origin_main"] == shas[1][:7], a


@pruefung("release-neuer", "Release-Bild mit neuerem Release: Befund «neuere Version verfügbar» mit deren Nummer")
def release_neuer():
    d, shas = release_welt()
    b = d["B"]
    assert "fehler" not in b and b["tag"] == "1.0.2" and b["hinter"] == 1 and b["bildrelevant"] == 1 and b["gleich"] is False, b
    assert b["neuer"] == "1.0.3" and b["commits"] == [{"sha": "v1.0.3", "betreff": "neuere Version verfügbar"}] and b["branch"] == "v1.0.3", b


@pruefung("commit-nicht-bildrelevant", "Commit-Bild, dahinter nur Commits ausserhalb des Bildes: verglichen mit main, gezählt nur bildrelevante, kein Befund")
def commit_nicht_bildrelevant():
    d, shas = release_welt()
    c = d["C"]
    assert "fehler" not in c and c["branch"] == "main" and c["origin_main"] == shas[3][:7], c
    assert c["hinter"] == 0 and c["bildrelevant"] == 0 and c["commits"] == [], c


@pruefung("version-probe", "Versions-Tag (11.10.2026): ausrollprobe vergleicht einen Container aus 1.0.1 mit dem Stand von v1.0.1, nicht mit HEAD")
def version_probe():
    werk = Path(tempfile.mkdtemp())
    repo, app = werk / "repo", werk / "app"
    for d in (repo / "deploy", repo / "tools", app):
        d.mkdir(parents=True)
    (repo / "Dockerfile").write_text("FROM x\nCOPY app.py ./\n")
    subprocess.run(["cp", str(WURZEL / "deploy" / "ausrollprobe.sh"), str(repo / "deploy")], check=True)
    subprocess.run(["cp", str(WURZEL / "tools" / "copy-quellen.awk"), str(repo / "tools")], check=True)
    git = lambda *a: git_lauf(*a, cwd=repo)
    git("init", "-q"); git("config", "user.email", "t@example.org"); git("config", "user.name", "t")
    (repo / "app.py").write_text("print(1)\n"); git("add", "-A"); git("commit", "-qm", "1.0.1"); git("tag", "v1.0.1")
    (repo / "app.py").write_text("print(2)\n"); git("commit", "-qam", "danach")   # main ist dem Release voraus
    (app / "app.py").write_text("print(1)\n")   # der Container führt v1.0.1 aus
    docker = ('case "$1" in inspect) case "$*" in *Labels*) echo;; *) echo ghcr.io/cidix/gitpuls:1.0.1;; esac;; exec) shift 2; '
              f's=$(printf %s "$3" | sed "s#/app#{app}#g"); shift 3; a=$1; [ "$a" = /app ] && a={app}; [ $# -gt 0 ] && shift; exec bash -c "$s" "$a" "$@";; esac\n')
    r = subprocess.run(["bash", str(repo / "deploy" / "ausrollprobe.sh"), str(repo), "b"], capture_output=True, text=True,
                       env={**os.environ, "PATH": programme(str(werk / "bin"), docker=docker) + os.pathsep + os.environ["PATH"]})
    assert r.returncode == 0 and "GLEICH" in r.stdout and "v1.0.1" in r.stdout, (r.stdout, r.stderr[-300:])
    (app / "app.py").write_text("print(3)\n")
    r = subprocess.run(["bash", str(repo / "deploy" / "ausrollprobe.sh"), str(repo), "b"], capture_output=True, text=True,
                       env={**os.environ, "PATH": str(werk / "bin") + os.pathsep + os.environ["PATH"]})
    assert r.returncode == 1 and "ABWEICHUNG" in r.stdout, (r.stdout, r.stderr[-300:])


if __name__ == "__main__":
    if len(sys.argv) > 1:
        PRUEFUNGEN[sys.argv[1]][1]()
        sys.exit(0)
    rot = []
    for name, (befund, _) in PRUEFUNGEN.items():
        r = subprocess.run([sys.executable, "-m", "tests.test_audit", name], cwd=WURZEL, capture_output=True, text=True, timeout=300)
        if r.returncode:
            rot.append(name)
            grund = (r.stderr.strip().splitlines() or ["?"])[-1][:220]
            print(f"rot   {befund}\n      {grund}")
        else:
            print(f"grün  {befund}")
    print(f"{len(PRUEFUNGEN) - len(rot)} von {len(PRUEFUNGEN)} grün" + (f", rot: {', '.join(rot)}" if rot else ""))
    sys.exit(1 if rot else 0)
