"""Kleinste Prüfung für den Regelzähler der zweiten Reihe. python3 -m tests.test_serverstand

Vorlage ist der echte Ausschnitt aus `iptables-save` auf dem VPS, gemessen am 08.09.2026 um 17:22, nach dem
Neustart um 17:03: sieben INPUT- und vier DOCKER-USER-Regeln. Der Sprung in tailscaleds eigene Kette steht
mit in der Datei, zählt aber nicht mit; sonst stünden acht Regeln da, wo sieben gesetzt wurden.
"""
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

pfad = Path(__file__).resolve().parent.parent / "tools" / "server-stand"
spec = importlib.util.spec_from_loader("serverstand", SourceFileLoader("serverstand", str(pfad)))
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

ECHT = """*filter
:INPUT ACCEPT [0:0]
:DOCKER-USER - [0:0]
-A INPUT -j ts-input
-A INPUT -i lo -j ACCEPT
-A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
-A INPUT -i tailscale0 -j ACCEPT
-A INPUT -p tcp -m tcp --dport 22 -j ACCEPT
-A INPUT -p udp -m udp --dport 41641 -j ACCEPT
-A INPUT -p icmp -j ACCEPT
-A INPUT -j DROP
-A FORWARD -j DOCKER-USER
-A FORWARD -j ts-forward
-A DOCKER-USER -i lo -j RETURN
-A DOCKER-USER -i tailscale0 -j RETURN
-A DOCKER-USER -i eth0 -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
-A DOCKER-USER -i eth0 -j DROP
COMMIT
"""

r = ss.regeln(ECHT)
assert {k: len(v) for k, v in r.items()} == {"INPUT": 7, "DOCKER-USER": 4}, r
assert not any("ts-input" in z for z in r["INPUT"]), "tailscaleds eigener Sprung zählt nicht mit"
assert not any(z.startswith("-A FORWARD") for v in r.values() for z in v), "nur die zwei gezählten Ketten"

# Gleicher Text heisst gleicher Stand: so entscheidet firewall(), ob der laufende Stand dem gespeicherten
# entspricht. Eine gelöschte Regel muss diesen Vergleich brechen, sonst meldet die Seite Deckung, die es nicht gibt.
assert ss.regeln(ECHT) == r
assert ss.regeln(ECHT.replace("-A DOCKER-USER -i eth0 -j DROP\n", "")) != r

# Leere Ketten sind messbar, nicht abwesend: die Seite macht daraus einen Befund.
leer = ss.regeln("*filter\n:INPUT ACCEPT [0:0]\n:DOCKER-USER - [0:0]\nCOMMIT\n")
assert leer == {"INPUT": [], "DOCKER-USER": []}, leer

# Ein Container, den docker inspect nicht kennt: der Eintrag trägt jeden Schlüssel, nicht ermittelbare Werte als None,
# damit der Webprozess nie auf einen fehlenden Schlüssel läuft (KeyError 'bild', 11.09.2026).
import subprocess  # noqa: E402
def kein_docker(*cmd):
    raise subprocess.CalledProcessError(1, cmd, stderr="Error: No such object")
ss.lauf = kein_docker
c = ss.container("wochenlog-wochenlog-1")
assert c == {"name": "wochenlog-wochenlog-1", "status": "fehlt", "seit_utc": None, "bild": None, "neustarts": None, "gesund": None}, c
# Atomar geschrieben: über eine temporäre Datei IM ZIELVERZEICHNIS plus rename, nie über /tmp. Dort liegt auf dem
# VPS ein tmpfs neben dem ext4-Volume, und os.replace kopiert über die Grenze, statt zu tauschen; ein Leser erwischt
# dann ein halbes JSON. Der Ringpuffer auslastung.json ist am 20.09.2026 ersatzlos zurückgebaut, server.json bleibt.
import json, os, tempfile  # noqa: E402
BEISPIEL = json.loads((Path(__file__).with_name("server.beispiel.json")).read_text())
ordner = tempfile.mkdtemp()
ss.ZIEL = os.path.join(ordner, "server.json")
assert not hasattr(ss, "verlauf") and not hasattr(ss, "VERLAUF") and not hasattr(ss, "PUNKTE"), "Ringpuffer ersatzlos weg"

class MkstempSpion:   # merkt sich, in welchem Verzeichnis die temporäre Datei angelegt wird
    def __init__(self, echt): self.echt, self.orte = echt, []
    def mkstemp(self, **kw): self.orte.append(kw.get("dir")); return self.echt.mkstemp(**kw)
spion = MkstempSpion(ss.tempfile)
ss.tempfile = spion
try:
    ss.schreiben(ss.ZIEL, BEISPIEL)
finally:
    ss.tempfile = spion.echt
assert spion.orte == [os.path.dirname(ss.ZIEL)], ("temporäre Datei nicht im Zielverzeichnis", spion.orte)
assert json.load(open(ss.ZIEL)) == BEISPIEL
assert os.listdir(ordner) == ["server.json"], ("keine temporäre Datei liegen geblieben", os.listdir(ordner))
# Getauscht, nicht in-place geschrieben: die Inode wechselt mit jedem Lauf.
vorher = os.stat(ss.ZIEL).st_ino
ss.schreiben(ss.ZIEL, BEISPIEL)
assert os.stat(ss.ZIEL).st_ino != vorher, "in-place geschrieben statt getauscht"

# Firewall (03.10.2026, Codex-Sammellauf): eine Familie, deren Messung scheitert, steht als nicht gemessen in der Datei und fällt nicht
# still weg; scheitern beide, heisst das nicht «keine zweite Reihe».
import types  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
def lauf_fw(ausfall):
    def lauf_(*cmd):
        if cmd[0] == "ufw":
            raise FileNotFoundError("ufw")
        if cmd[0] in ausfall:
            raise subprocess.CalledProcessError(1, cmd)
        return ECHT
    return lauf_
echt_sp = ss.subprocess
ss.subprocess = types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(stdout="enabled\n"), CalledProcessError=subprocess.CalledProcessError,
                                      SubprocessError=subprocess.SubprocessError, TimeoutExpired=subprocess.TimeoutExpired)
try:
    ss.lauf = lauf_fw({"ip6tables-save"})
    fw = ss.firewall(datetime(2026, 10, 3, tzinfo=timezone.utc))
    assert fw["nicht_gemessen"] == ["IPv6"] and list(fw["familien"]) == ["IPv4"] and fw["art"] == "iptables", fw
    ss.lauf = lauf_fw({"iptables-save", "ip6tables-save"})
    fw = ss.firewall(datetime(2026, 10, 3, tzinfo=timezone.utc))
    assert fw["nicht_gemessen"] == ["IPv4", "IPv6"] and fw["familien"] == {} and fw["art"] == "iptables", fw
finally:
    ss.subprocess = echt_sp

# Fremde Hosts (11.10.2026, Probe auf dem VPS): nur iptables-persistent ist messbar. Ein ufw-Host (Ubuntu 24.04, systemctl is-enabled
# netfilter-persistent gibt not-found) hat weder Unit noch /etc/iptables: Persistenz und Vergleich sind nicht gemessen, nicht «nicht aktiv»
# und «weicht ab». Ein Host mit nft und ohne iptables-save heisst nftables. Rootless-Docker und Podman legen im Netz des Hosts keine
# DOCKER-USER an: die Kette fehlt, sie ist nicht leer.
UFW = """*filter
:INPUT DROP [0:0]
:DOCKER-USER - [0:0]
-A INPUT -j ufw-before-logging-input
-A INPUT -j ufw-before-input
COMMIT
"""
def fremd(befehle, is_enabled, ausgabe=UFW):
    ss.UNGEMESSEN.clear()
    ss.lauf = lambda *cmd: "Status: active\n" if cmd[0] == "ufw" else ausgabe
    ss.shutil = types.SimpleNamespace(which=lambda b: f"/usr/sbin/{b}" if b in befehle else None)
    ss.subprocess = types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(stdout=is_enabled), CalledProcessError=subprocess.CalledProcessError,
                                          SubprocessError=subprocess.SubprocessError, TimeoutExpired=subprocess.TimeoutExpired)
    return ss.firewall(datetime(2026, 10, 11, tzinfo=timezone.utc))
echt_shutil, echt_fam = ss.shutil, ss.FAMILIEN
ss.FAMILIEN = tuple((n, b, os.path.join(tempfile.mkdtemp(), "fehlt")) for n, b, _ in echt_fam)
try:
    fw = fremd({"ufw", "iptables-save", "ip6tables-save"}, "not-found\n")
    assert (fw["art"], fw["persistent"], fw["wie_gespeichert"]) == ("ufw", None, None), fw
    assert {"firewall.persistent", "firewall.gespeichert"} <= set(ss.UNGEMESSEN), ss.UNGEMESSEN
    fw = fremd({"nft"}, "not-found\n")
    assert (fw["art"], fw["familien"], fw["nicht_gemessen"], fw["wie_gespeichert"]) == ("nftables", {}, [], None) and "firewall.nftables" in ss.UNGEMESSEN, fw
    fw = fremd({"iptables-save", "ip6tables-save"}, "", "*filter\n:INPUT ACCEPT [0:0]\n-A INPUT -i lo -j ACCEPT\nCOMMIT\n")
    assert fw["familien"] == {"IPv4": {"INPUT": 1}, "IPv6": {"INPUT": 1}}, ("ohne Kette DOCKER-USER kein Zähler dafür", fw)
    fw = fremd({"iptables-save", "ip6tables-save"}, "disabled\n")
    assert fw["persistent"] is False and fw["wie_gespeichert"] is False, ("installiert, aber aus: das bleibt ein Befund", fw)
finally:
    ss.shutil, ss.subprocess, ss.FAMILIEN = echt_shutil, echt_sp, echt_fam

# Timer (03.10.2026, Codex-Audit): der ActiveState der Timer-Unit wird getrennt vom Service gemessen. Ein gestoppter Timer mit sauberem
# letztem Lauf steht als timer_aktiv «inactive» in der Datei; die Selbstmessung von gitpuls-server ersetzt nur das Service-Ergebnis.
def show_(unit, *props):
    if unit.endswith(".timer"):
        return {"LoadState": "loaded", "ActiveState": "inactive", "NextElapseUSecRealtime": "", "TimersCalendar": ""}
    return {"Result": "success", "ExecMainStatus": "0", "ExecMainStartTimestamp": "Sat 2026-10-03 01:00:00 UTC",
            "ExecMainExitTimestamp": "Sat 2026-10-03 01:00:30 UTC", "ActiveState": "inactive"}
echt_show, ss.show = ss.show, show_
try:
    t_ = ss.timer("gitpuls-sammler")
    assert (t_["timer_aktiv"], t_["status"], t_["ergebnis"], t_["exit"], t_["naechster_utc"]) == ("inactive", "inactive", "success", 0, None), t_
    t_ = ss.timer(ss.EIGENE)
    assert t_["timer_aktiv"] == "inactive" and t_["ergebnis"] == "success", ("die Selbstmessung ersetzt den Timerzustand nicht", t_)
finally:
    ss.show = echt_show

# Deploy-Abstand (03.10.2026, Codex-Sammellauf): tools/deploy-stand.sh mit nachgebautem docker, git und bildrelevant.sh. Ein Messfehler
# behält den letzten Eintrag samt altem Zeitpunkt und vermerkt den Fehler; ohne früheren Eintrag steht nur der Fehler; ein Erfolg
# ersetzt den Eintrag ganz, auch einen alten Fehler.
import shutil, stat  # noqa: E402
werk = tempfile.mkdtemp()
os.makedirs(os.path.join(werk, "tools")); os.makedirs(os.path.join(werk, "bin"))
shutil.copy(pfad.with_name("deploy-stand.sh"), os.path.join(werk, "tools", "deploy-stand.sh"))
def skript(name, text):
    p_ = os.path.join(werk, name)
    open(p_, "w").write("#!/bin/sh\n" + text)
    os.chmod(p_, os.stat(p_).st_mode | stat.S_IEXEC)
skript("tools/bildrelevant.sh", 'case "$4" in --log) printf "def5678\\tNeu\\n";; *) echo "3 2";; esac\n')   # --log seit 11.10.2026: nur bildrelevante Commits
skript("bin/docker", 'case "$2" in kaputt-1) exit 1;; esac\necho "bild:abc1234"\n')
skript("bin/git", 'case "$2:$3" in *ohnenetz*:fetch) exit 1;; esac\ncase "$3" in rev-parse) case "$4" in --verify) ;; *) echo def5678;; esac;; '
                 'ls-remote) printf "ref: refs/heads/main\\tHEAD\\n";; log) printf "def5678\\tNeu\\n";; esac\n')
def dienste(*eintraege):
    json.dump({"dienste": [{"name": r_, "repo": r_, "container": [c_], "checkout": k_} for r_, c_, k_ in eintraege]}, open(os.path.join(werk, "tools", "dienste.json"), "w"))
ziel = os.path.join(werk, "deploy.json")
def deploy_lauf():
    subprocess.run(["sh", os.path.join(werk, "tools", "deploy-stand.sh")], check=True, capture_output=True,
                   env={**os.environ, "PATH": os.path.join(werk, "bin") + os.pathsep + os.environ["PATH"], "GITPULS_DEPLOY_ZIEL": ziel})
    return json.load(open(ziel))
alt_eintrag = {"tag": "aaa1111", "origin_main": "bbb2222", "hinter": 1, "bildrelevant": 1, "commits": [], "gemessen": "2026-10-01T10:00:00+02:00"}
json.dump({"fernwerk-studio/a": alt_eintrag}, open(ziel, "w"))
dienste(("fernwerk-studio/a", "a-1", "/srv/ohnenetz"), ("fernwerk-studio/b", "kaputt-1", "/srv/b"), ("fernwerk-studio/c", "c-1", "/srv/c"))
d = deploy_lauf()
assert d["fernwerk-studio/a"] == {**alt_eintrag, "repo": "fernwerk-studio/a", "fehler": "git fetch in /srv/ohnenetz gescheitert"}, ("alter Stand, alter Zeitpunkt, Fehler dazu", d["fernwerk-studio/a"])
assert d["fernwerk-studio/b"] == {"repo": "fernwerk-studio/b", "fehler": "Container kaputt-1 nicht gefunden"}, d["fernwerk-studio/b"]
assert {k: v for k, v in d["fernwerk-studio/c"].items() if k != "gemessen"} == {"repo": "fernwerk-studio/c", "tag": "abc1234", "origin_main": "def5678", "branch": "main", "hinter": 2, "bildrelevant": 2,
                                                                     "gleich": True, "commits": [{"sha": "def5678", "betreff": "Neu"}]}, d["fernwerk-studio/c"]
dienste(("fernwerk-studio/a", "a-1", "/srv/a"))
d = deploy_lauf()
assert "fehler" not in d["fernwerk-studio/a"] and d["fernwerk-studio/a"]["tag"] == "abc1234" and list(d) == ["fernwerk-studio/a"], ("Erfolg ersetzt den Fehler", d)
assert sorted(os.listdir(werk)) == ["bin", "deploy.json", "tools"], ("keine temporäre Datei liegen geblieben", os.listdir(werk))

print("ok: sieben INPUT- und vier DOCKER-USER-Regeln, tailscaleds Sprung zählt nicht, leere Kette bleibt messbar;"
      " server.json atomar über das Zielverzeichnis getauscht, Ringpuffer ersatzlos zurückgebaut; Firewall-Familie und Timer-Unit"
      " getrennt gemessen; ufw-, nftables- und Rootless-Host ohne falschen Firewall-Befund; Deploy-Messfehler behält den letzten Stand")
