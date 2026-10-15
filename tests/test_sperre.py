"""Frist der Schreibsperre (26.09.2026, Codex-Review): echte Prozesse, keine Nachbildung. Ein Halter hält die Sperre im Datenordner; der
Sammler wartet höchstens bis zur Frist (hier GITPULS_LAUFFRIST_S=2) und endet mit einer lesbaren Meldung, Exit 1 und dem Fehler in lauf.json,
ohne Laufzeile. Stirbt der Halter (SIGKILL), ist die Sperre sofort frei. Der Knopflauf wird über seiner harten Grenze beendet, der Knopf
steht danach wieder auf «bereit», und die Sperre ist frei, sobald der Halter geht.
python3 -m tests.test_sperre  (auf dem Mac mit .venv/bin/python)"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ORDNER = Path(tempfile.mkdtemp())
os.environ["GITPULS_DB"] = str(ORDNER / "sperre.db")
os.environ["GITPULS_LAUFFRIST_S"] = "2"
os.environ.setdefault("GITPULS_ADRESSEN", "dev@fernwerk-studio.example")
os.environ.setdefault("GITPULS_SPRACHE", "de")
os.environ["TZ"] = "Europe/Zurich"   # die Zone setzt der Test selbst (10.10.2026, Audit S28): grün unter jeder TZ der Shell
from app import db, web  # noqa: E402

WURZEL = Path(__file__).resolve().parent.parent
db.verbinden(schreibend=True).close()
HALTER = ("import sys, time; from app import db\n"
          "with db.schreibsperre(db.frist() + 3600):\n"
          "    print('gehalten', flush=True); time.sleep(float(sys.argv[1]))\n")


def halter(dauer=60):
    """Ein zweiter Prozess, der die Sperre nimmt und hält; zurück erst, wenn er sie hat."""
    p = subprocess.Popen([sys.executable, "-c", HALTER, str(dauer)], cwd=WURZEL, stdout=subprocess.PIPE, text=True, env=os.environ | {"GITPULS_LAUFFRIST_S": "3600"})
    assert p.stdout.readline().strip() == "gehalten"
    return p


def frei(grenze_s=0.5):
    """Nimmt die Sperre mit kurzer Frist; (frei, Sekunden bis dahin)."""
    t0 = time.monotonic()
    try:
        with db.schreibsperre(time.monotonic() + grenze_s):
            return True, time.monotonic() - t0
    except db.FristAbgelaufen:
        return False, time.monotonic() - t0


# (1) Halter hält, der Sammler bricht nach der Frist ab: Exit 1, lesbare Meldung mit Halter und Dauer, Fehler in lauf.json, keine Laufzeile.
h = halter()
time.sleep(1)
t0 = time.monotonic()
r = subprocess.run([sys.executable, "-m", "app.sammler"], cwd=WURZEL, capture_output=True, text=True, timeout=60)
dauer = time.monotonic() - t0
lj = json.loads(web.LAUF.read_text())
assert r.returncode == 1 and 2 <= dauer < 6, (r.returncode, dauer, r.stdout[-400:], r.stderr[-400:])
assert f"Ein anderer Schreiber (PID {h.pid}) hält die Sperre seit " in r.stdout and "s Warten ist die Frist des Laufs abgelaufen" in r.stdout, r.stdout
# Die gewartete Zeit in der Meldung ist die echte (26.09.2026, auf dem VPS gesehen: vorher stand dort die Voreinstellung 600 s).
gewartet_s = int(r.stdout.split("nach ")[1].split(" s Warten")[0])
assert 1 <= gewartet_s <= 3, gewartet_s
assert lj["exit"] == 1 and lj["fertig"] and f"(PID {h.pid}) hält die Sperre seit" in lj["fehler"] and "Traceback" not in r.stdout + r.stderr, lj
seit = int(lj["fehler"].split("seit ")[1].split(" s")[0])
assert 2 <= seit <= 6, seit   # der Halter hielt rund eine Sekunde vor dem Start plus die zwei Sekunden Frist
with db.verbinden() as c:
    assert c.execute("SELECT COUNT(*) FROM laeufe").fetchone()[0] == 0, "ohne Sperre keine Laufzeile"
assert not frei()[0], "der Halter hält noch"

# (2) Der Halter stirbt (SIGKILL, kein Aufräumen): die Sperre ist sofort frei.
h.send_signal(signal.SIGKILL)
h.wait()
ok, bis = frei()
assert ok and bis < 0.6, (ok, bis)   # frei statt nach der Frist; gibt der Kernel die Sperre einen Moment später frei, folgt ein Warteschritt (bis 0.5 s, gemessen auf dem CI-Runner 0.21 s)

# (3) Knopflauf über der harten Grenze: der Unterprozess wartet (seine Frist 30 s), web.lauf beendet ihn nach KNOPF_GRENZE_S, setzt den Zustand
# zurück und schreibt das Ende; der Knopf steht danach auf «bereit», die Sperre ist frei, sobald der Halter geht.
h = halter()
alt_grenze, web.KNOPF_GRENZE_S = web.KNOPF_GRENZE_S, 2
os.environ["GITPULS_LAUFFRIST_S"] = "30"
web.lauf_seit[0] = web.iso_utc()
try:
    t0 = time.monotonic()
    web.lauf()
    dauer = time.monotonic() - t0
finally:
    web.KNOPF_GRENZE_S = alt_grenze
    os.environ["GITPULS_LAUFFRIST_S"] = "2"
st = web.lauf_status()
lj = json.loads(web.LAUF.read_text())
assert 2 <= dauer < 5 and web.lauf_seit[0] is None and st["zustand"] == "bereit", (dauer, web.lauf_seit, st)
assert lj["exit"] == -9 and "Lauf per Knopf nach 2 s beendet" in lj["fehler"], lj
h.terminate()
h.wait()
ok, bis = frei()
assert ok and bis < 0.6, (ok, bis)   # frei statt nach der Frist; gibt der Kernel die Sperre einen Moment später frei, folgt ein Warteschritt (bis 0.5 s, gemessen auf dem CI-Runner 0.21 s)

# (4) Ein Knopflauf, der nicht einmal startet, lässt den Knopf ebenfalls nicht hängen (finally).
alt_exe, web.sys.executable = web.sys.executable, "/nicht/vorhanden"
web.lauf_seit[0] = web.iso_utc()
try:
    web.lauf()
finally:
    web.sys.executable = alt_exe
assert web.lauf_seit[0] is None and web.lauf_status()["zustand"] == "bereit" and "nicht startbar" in json.loads(web.LAUF.read_text())["fehler"]

# (5) Frist im Werk: eine abgelaufene Frist endet mit FristAbgelaufen, nicht mit einem Traceback aus dem Werk.
try:
    db.frist_pruefen(time.monotonic() - 1, "beim Test")
    raise AssertionError("keine FristAbgelaufen")
except db.FristAbgelaufen as e:
    assert "Frist von 2 s abgelaufen, beim Test" == str(e), e
assert db.LAUFFRIST_S == 2 and float(subprocess.run([sys.executable, "-c", "from app import db; print(db.LAUFFRIST_S)"], cwd=WURZEL, capture_output=True, text=True,
                                                    env={k: v for k, v in os.environ.items() if k != "GITPULS_LAUFFRIST_S"}).stdout) == 600, "live 600 s"
print("ok: Sammler bricht nach der Frist mit Meldung ab, keine Laufzeile; Halter-Tod gibt sofort frei; Knopflauf hart beendet, Knopf bereit, Sperre frei")
