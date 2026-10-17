"""gitpuls-Oberfläche: Server zuoberst (server.json vom Host-Timer), darunter GitHub: woran habe ich
zuletzt gearbeitet, was ruht, und wo läuft etwas anderes als das, was im Repo steht.
Serverseitig gerendert, kein Framework.

GET /          Serverstand (Lagesatz, Maschine, Dienste, Installiert, Updates), dann GitHub:
               Kennzahlen mit Zweigbefunden, drei rollende Zahlen, Projekte nach letztem Commit, Rückblick
GET /gesund    baut die Seite auf: 200 «ok», 503 «blockfehler: …», sobald ein Block seine Fehlerzeile trägt (seit d7f58c0);
               eine unlesbare Datenbank bricht vor den Blöcken ab und ergibt 500
POST /lauf     startet den Sammler als Unterprozess (202); 409 läuft schon, 429 Sperrfrist 60 s
GET /lauf/status  Zustand, Phase, Start, Ende, Exit aus /data/lauf.json, das der Sammler schreibt
"""
import calendar
import hashlib
import html
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import TZPATH, ZoneInfo

from app import db
from app import format as fmt
from app.kennzahlen import rekorde, serien
from app.sprache import STANDARD, TABELLEN, aktiv, gilt, t
from app.texte import de as texte_de, en as texte_en

WEB = Path(__file__).with_name("web")
# Hash im Dateinamen: Safari hielt /tokens.css nach dem Umbau einen Tag im Cache (max-age=86400
# unter gleicher URL, gemessen 2026-09-02) und rendert die neue Seite mit den alten Variablen.
STATIK = {n: f"/s/{hashlib.sha256((WEB / n).read_bytes()).hexdigest()[:10]}-{n}" for n in ("tokens.css", "style.css", "icon.svg", "apple-touch-icon.png")}
PFAD_ZU_DATEI = {v: k for k, v in STATIK.items()}
# Favicon (27.09.2026): «g» fett in der Akzentfarbe auf der Kachelfläche, einmal erzeugt und eingecheckt. icon.svg folgt hell und dunkel
# über prefers-color-scheme; favicon.ico (16 und 32 px) und apple-touch-icon.png (180 px) sind die dunkle Fassung, gerendert aus dem SVG.
# /favicon.ico trägt keine Prüfsumme in der Adresse und wird deshalb nicht als immutable ausgeliefert.
MEDIENTYP = {".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png"}
def zonenpfad(name):
    """Der Pfad zur Zonendatei eines IANA-Namens, gesucht in denselben Ordnern, die ZoneInfo benutzt."""
    if not name:
        return None
    if name.startswith("/"):
        return name if os.path.isfile(name) else None
    for wurzel in TZPATH:
        p = os.path.join(wurzel, name)
        if os.path.isfile(p):
            return p
    return None


def zonenquelle(tz=None, localtime="/etc/localtime", namensdatei="/etc/timezone"):
    """Die Zonendatei des Systems und ihr Name, als (Pfad, Name). Drei Quellen in dieser Reihenfolge: TZ aus der
    Umgebung (Name oder Pfad; der führende Doppelpunkt wird abgestreift, POSIX erlaubt `TZ=:Europe/Zurich` und
    libc versteht ihn, ZoneInfo nicht), der Zonenname hinter /etc/localtime, der Inhalt von /etc/timezone.
    Der Zonenordner heisst nicht überall gleich (Debian /usr/share/zoneinfo/…, macOS /usr/share/zoneinfo.default/…),
    deshalb ein Muster statt eines festen Splits. Die drei Quellen stehen als Parameter, damit jeder Zweig ohne
    Eingriff ins laufende System prüfbar ist."""
    wert = (os.environ.get("TZ", "") if tz is None else tz).lstrip(":").strip()
    pfad = zonenpfad(wert)
    if pfad:
        return pfad, (wert if not wert.startswith("/") else re.sub(r"^.*/zoneinfo[^/]*/", "", pfad))
    if wert:
        # Ein TZ-Wert, zu dem es keine Zonendatei gibt: eine POSIX-Regel wie `CET-1CEST,M3.5.0,M10.5.0/3`, ein
        # Tippfehler, eine Zone, die dieses System nicht kennt. Wir weichen auf die Systemzone aus, damit beide
        # Seiten weiter aus einer Datei lesen — aber still darf das nicht geschehen, denn es übergeht die
        # ausdrückliche Angabe des Betreibers.
        print(f"gitpuls: zu TZ={wert!r} gibt es keine Zonendatei; es gilt die Zone des Systems. "
              f"Setze TZ auf einen IANA-Zonennamen, wenn eine andere gelten soll.", flush=True)
    if os.path.isfile(localtime):
        echt = os.path.realpath(localtime)
        m = re.search(r"/zoneinfo[^/]*/(.+)$", echt)
        return echt, (m.group(1) if m else None)
    # /etc/localtime kann fehlen; Debian nennt die Zone dann hier. UnicodeDecodeError ist kein OSError: zeigt der
    # Pfad auf eine Zonendatei statt auf Text, stirbt der Start ohne den Fang (Byte 0x80 an Stelle 44).
    try:
        name = open(namensdatei).read().strip() or None
    except (OSError, UnicodeDecodeError):
        name = None
    # Nur ein Zonenname gilt: eine Zonendatei kann rein ASCII sein (Etc/UTC unter Debian) und liest sich dann ohne Fehler als Text.
    name = name if name and re.fullmatch(r"[A-Za-z0-9_+./-]+", name) else None
    return zonenpfad(name), name


def systemzone(**quellen):
    """Die Zeitzone für Python — aus DERSELBEN Datei, auf die auch libc und damit SQLites 'localtime' gezwungen
    wird (`TZ=:<Pfad>`). Vorher nahm Python einen Zonennamen und SQLite ihren eigenen Weg, und eine Prüfung
    verglich beide an zwei Stichproben. **Zwei Stichproben prüfen Offsets, keine Zonen:** Europe/Helsinki und
    Asia/Jerusalem haben im Januar und im Juli denselben Offset und trotzdem verschiedene Umstellungstermine, das
    Paar kam durch (Codex-Befund 20.09.2026, reproduziert an 2026-03-27T21:30:00Z: SQLite 27.03. 23:30, Python
    28.03. 00:30). Seither gibt es keine zwei Wege mehr, die man vergleichen müsste, sondern eine Quelle.
    Die Deckung bleibt trotzdem nachgewiesen, aber beim Start über alle Übergänge des ausgewerteten Zeitraums
    (`zonendeckung`) statt über Stichproben."""
    pfad, name = zonenquelle(**quellen)
    if pfad:
        try:
            with open(pfad, "rb") as f:
                zone = ZoneInfo.from_file(f, key=name or pfad)
            # Der führende Doppelpunkt mit absolutem Pfad weist libc auf genau diese Datei; damit liest SQLite,
            # was Python gelesen hat, und nicht das, was ein gleich lautender Name anderswo bedeutet.
            os.environ["TZ"] = ":" + pfad
            time.tzset()
            return zone
        except (OSError, ValueError):
            pass
    print(f"gitpuls: keine lesbare Zonendatei gefunden (TZ={os.environ.get('TZ')!r}); Python und SQL laufen "
          f"deshalb beide auf UTC. Setze TZ auf einen IANA-Zonennamen.", flush=True)
    os.environ["TZ"] = "UTC"
    time.tzset()
    return ZoneInfo("UTC")


def offset_sql(c, zeitpunkt):
    """Der Offset, den SQLites 'localtime' zu diesem UTC-Zeitpunkt anwendet, in Sekunden."""
    iso = zeitpunkt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return c.execute("SELECT CAST(strftime('%s', ?, 'localtime') AS INTEGER) - CAST(strftime('%s', ?) AS INTEGER)",
                     (iso, iso)).fetchone()[0]


def _wechsel(offset, a, b):
    """Die erste ganze Sekunde, ab der zwischen a und b der neue Offset gilt, per Bisektion. Auf Sekunden
    gerundet, weil die Zeitstempel sekundengenau sind und die Fehlermeldung sonst Bruchteile nennt."""
    while b - a > timedelta(seconds=1):
        m = a + (b - a) / 2
        if offset(a) == offset(m):
            a = m
        else:
            b = m
    b = b.replace(microsecond=0)
    return b if offset(b) != offset(a) else b + timedelta(seconds=1)


def zonendeckung(c, zone, von, bis):
    """Liefern SQLites 'localtime' und `zone` im ausgewerteten Zeitraum überall dasselbe? Geprüft wird nicht an
    Stichproben, sondern an JEDEM Umstellungstermin BEIDER Seiten zwischen `von` und `bis`, je eine Sekunde davor
    und danach — dort und nur dort trennen sich zwei Zonen mit gleichen Offsets (Codex-Befund 20.09.2026).
    Liefert None, wenn alles deckt, sonst den Text der Abweichung mit Zone, Zeitpunkt und beiden Werten."""
    py = lambda d: int(d.astimezone(zone).utcoffset().total_seconds())
    sql = lambda d: offset_sql(c, d)
    lokal = lambda d, f: (d + timedelta(seconds=f(d))).strftime("%Y-%m-%d %H:%M:%S")
    tag = datetime.combine(von, datetime.min.time(), timezone.utc)
    ende = datetime.combine(bis, datetime.min.time(), timezone.utc) + timedelta(days=1)
    vorher = None
    grenzen = []
    while tag <= ende:
        jetzt_off = (py(tag), sql(tag))
        if jetzt_off[0] != jetzt_off[1]:
            return (f"Zeitzone {getattr(zone, 'key', zone)}: Python und SQLite rechnen am {tag:%d.%m.%Y} verschieden, "
                    f"Python {lokal(tag, py)}, SQLite {lokal(tag, sql)}.")
        if vorher and vorher[1] != jetzt_off:
            for f in (py, sql):
                grenzen.append(_wechsel(f, vorher[0], tag))
        vorher = (tag, jetzt_off)
        tag += timedelta(days=1)
    for g in sorted(set(grenzen)):
        for d in (g - timedelta(seconds=1), g):
            if lokal(d, py) != lokal(d, sql):
                return (f"Zeitzone {getattr(zone, 'key', zone)}: am Umstellungstermin {d:%Y-%m-%dT%H:%M:%SZ} liefert "
                        f"Python {lokal(d, py)} und SQLite {lokal(d, sql)}. Die zwei Seiten lesen nicht dieselbe "
                        f"Zonendatei; setze TZ auf einen IANA-Zonennamen, den beide kennen.")
    return None


TZ = systemzone()


def zonenname():
    """Der Name der geltenden Zone für die Legende, «Europe/Zurich» oder was das System sagt. Vorher stand dort
    «Zürcher Datum» als fester Text; in einer fremden Fassung behauptete das einen Tag, der nicht gemeint ist."""
    return getattr(TZ, "key", None) or f"{db.jetzt(TZ):%Z}"
# Die Adressen, deren Commits als eigene zählen. KEIN Default mit den Adressen des Autors: eine fremde
# Installation ohne gesetzte Variable sähe sonst eine leere Aktivitätsseite mit null Commits, null Serie und
# null Wochenwerten — und das ist von einer echten jungen Datenlage nicht zu unterscheiden (Codex-Befund
# 20.09.2026). Fehlt die Variable, bricht der Start laut ab, wie bei der Token-Datei.
ADRESSEN = [a.strip() for a in os.environ.get("GITPULS_ADRESSEN", "").split(",") if a.strip()]
# Die gezählte Menge, EINE Bedingung für jede Abfrage der Seite: eigene Adressen, ohne Merge-Commits (25.09.2026). Ein Merge hat
# mehr als einen Elternteil und bringt keine eigene Arbeit; ein Squash hat einen und zählt. Unbekannt (NULL, Altbestand vor dem
# --alles-Lauf) zählt wie bisher. Kacheln, Aufteilung, «Zuletzt», Ring, Serie, Rückblick, Rekorde und Projektzeilen lesen alle hier.
IN_ADRESSEN = f"autor_adresse IN ({','.join('?' * len(ADRESSEN))}) AND COALESCE(eltern, 1) <= 1"
# Zustandswörter je Repo, Tage seit dem letzten eigenen Commit. Eine Stelle.
SCHWELLEN = {"aktiv": 7, "ruht": 60}
# Ein Zweig vor main wird erst zum Befund, wenn sein letzter Commit älter als diese Frist ist (Entscheid 26.09.2026): ein frisch
# gepushter Arbeitszweig ist Arbeit, kein Handlungsbedarf. Sekundengenau gegen jetzt, nicht nach Kalendertagen. Alle Zähler (Block
# Handlungsbedarf, Kopfzahl samt Sprunglinks, Lagesatz) lesen k["befunde"] aus projekte(), die Frist wirkt nur dort; die Zweigangabe
# der Projektzeile (kurz.zweig) bleibt davon unberührt, ebenso Dependabot-Zweige, Sicherheitswarnungen und Actions.
ZWEIG_FRIST = timedelta(days=3)
# Ein Repo fällt von der Seite, wenn GitHub es nicht mehr listet (gelöscht oder archiviert). Gemessen
# wird nicht der letzte Lauf, sondern eine Karenz: sonst würde eine Antwort, die nur die erste Seite
# liefert, jedes fehlende Repo wie ein gelöschtes aussehen lassen. Bei stündlichem Timer muss ein Repo
# rund 24 Läufe hintereinander fehlen. Die Zeilen bleiben in der Datenbank, sie werden nur nicht gezeigt.
KARENZ_H = 24
DEPLOY = db.DB.parent / "deploy.json"   # schreibt tools/deploy-stand.sh auf dem Host
LAUF = db.DB.parent / "lauf.json"       # schreibt app/sammler.py, auch bei den Läufen des Timers
TOKEN = db.DB.parent / "token.json"     # schreibt app/sammler.py::token_messen, Ablaufdatum des Tokens
TOKEN_WARNUNG = timedelta(days=14)      # ab dieser Restlaufzeit und nach Ablauf ein Eintrag im Handlungsbedarf (Harte Grenzen, PAT)
SERVER = db.DB.parent / "server.json"   # schreibt tools/server-stand auf dem Host, alle 5 Minuten; kein zweiter Weg
SERVER_ALT_S = 15 * 60                  # Warnzeile ab diesem Alter: drei verpasste Raster des Timers
KETTEN = ("INPUT", "DOCKER-USER")       # die zwei Ketten, die tools/server-stand zählt
SPERRE_S = 60                           # der Sammler zieht GitHub-Anfragen, deshalb länger als in der Vorlage (15 s)
lauf_lock = threading.Lock()
lauf_seit = [None]                      # Startzeit des Laufs per Knopf, None wenn keiner läuft


def iso_utc(d=None):
    return (d or db.jetzt(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def lauf_status(eigener=True):
    """Zustand für den Knopf: eigener Unterprozess oder ein Timer-Lauf laut lauf.json. Ein Lauf ohne Ende, älter als die Frist
    (db.LAUFFRIST_S, live 600 s wie TimeoutStartSec der Unit), gilt als abgebrochen, sonst bliebe der Knopf tot. `eigener=False`
    sieht vom eigenen Unterprozess ab (Abschluss in lauf())."""
    unlesbar = None
    try:
        l = json.loads(LAUF.read_text())
    except FileNotFoundError:
        l = {}
    except (OSError, ValueError) as e:
        l, unlesbar = {}, f"lauf.json unlesbar, {type(e).__name__}"
    if not isinstance(l, dict):   # Typprüfung (Codex-Audit 11.10.2026): eine Liste als Wurzel brach vorher /lauf/status mit AttributeError
        l, unlesbar = {}, f"lauf.json ist {type(l).__name__}, kein Objekt"
    jetzt = db.jetzt(timezone.utc)
    # Ein ungültiger Zeitpunkt ist unbekannt, nie «gerade eben»: kein laufender Lauf, keine Sperrfrist, der Grund steht im Fehler.
    alter = lambda iso: sekunden_seit(iso, jetzt)
    a_start, a_fertig = alter(l.get("start")), alter(l.get("fertig"))
    if (l.get("start") and a_start is None) or (l.get("fertig") and a_fertig is None):
        unlesbar = unlesbar or "lauf.json: Zeitpunkt ungültig"
    laeuft = (eigener and bool(lauf_seit[0])) or bool(a_start is not None and not l.get("fertig") and a_start < db.LAUFFRIST_S)
    sperre = max(0, int(SPERRE_S - a_fertig)) if a_fertig is not None else 0
    return {"zustand": "laeuft" if laeuft else "bereit", "phase": l.get("phase") if laeuft else None, "start": l.get("start"),
            "fertig": l.get("fertig"), "exit": l.get("exit"), "fehler": unlesbar or l.get("fehler"), "neu": l.get("neu"), "teilweise": l.get("teilweise"),
            "sperre_s": sperre}


def sekunden_seit(iso_s, jetzt):
    """Sekunden seit einem ISO-Zeitpunkt mit Zone; None für fehlend, ungültig oder ohne Zone (Codex-Audit 11.10.2026, Befund 7: vorher wurde
    aus None «0 Sekunden», also frisch). Eine Stelle für deploy.json, lauf.json und server.json."""
    try:
        d = datetime.fromisoformat(iso_s.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    return (jetzt - d).total_seconds() if d.tzinfo else None


def lauf_starten():
    with lauf_lock:
        s = lauf_status()
        if s["zustand"] == "laeuft":
            return 409, {"fehler": "läuft schon", "start": lauf_seit[0] or s["start"]}
        if s["sperre_s"]:
            return 429, {"fehler": f"Sperrfrist, noch {s['sperre_s']} s", "sperre_s": s["sperre_s"]}
        lauf_seit[0] = iso_utc()
    threading.Thread(target=lauf, daemon=True).start()
    return 202, {"gestartet": lauf_seit[0]}


# Takt im Container (10.10.2026, Audit S7), die Alternative zu den Host-Timern für Rechner ohne systemd, etwa einen Laptop: mit
# GITPULS_TAKT_MIN über 0 startet der Webprozess den Sammler alle so viele Minuten wie der Knopf (lauf_starten, also mit Sperre und
# Sperrfrist). 0 schaltet ihn ab, die eigene Instanz läuft mit den Host-Timern.
TAKT_MIN = db.zahl("GITPULS_TAKT_MIN", 0.0)
# Bindadresse im Container 0.0.0.0, veröffentlicht wird nur 127.0.0.1 (compose.yaml); eine lokale Instanz ohne Container setzt 127.0.0.1.
BIND = os.environ.get("GITPULS_BIND") or "0.0.0.0"


def takt():
    while True:
        try:
            lauf_starten()
        except Exception:   # noqa: BLE001, der Takt darf nie sterben
            traceback.print_exc()
        time.sleep(TAKT_MIN * 60)


# Harte Grenze des Knopf-Unterprozesses (26.09.2026): die Frist des Laufs plus 30 s, damit der Sammler zuerst selbst mit seiner lesbaren
# Meldung endet; hängt er trotzdem, wird er beendet. Vorher lief der Unterprozess ohne Grenze, und der Knopf stand für immer auf «läuft».
KNOPF_GRENZE_S = db.LAUFFRIST_S + 30


def lauf():
    """Der Sammler als Unterprozess im selben Bild, derselbe Aufruf wie in gitpuls-sammler.service.
    Startet der Timer gleichzeitig, wartet der spätere Sammler an db.schreibsperre() auf den früheren, seit dem 26.09.2026 höchstens bis
    zur Frist des Laufs (db.LAUFFRIST_S ab Beginn). Über KNOPF_GRENZE_S wird der Unterprozess beendet; der Laufzustand wird in jedem Fall
    zurückgesetzt (finally), und fehlt das Ende in lauf.json, schreibt es dieser Prozess mit dem Grund."""
    print(f"{lauf_seit[0]} Lauf per Knopf gestartet", flush=True)
    code, zeilen = None, []
    vorher = LAUF.stat().st_mtime_ns if LAUF.exists() else None   # hat der Unterprozess lauf.json selbst geschrieben?
    try:
        r = subprocess.run([sys.executable, "-m", "app.sammler"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=KNOPF_GRENZE_S)
        code, zeilen = r.returncode, r.stdout.strip().splitlines()
    except subprocess.TimeoutExpired as e:   # subprocess.run hat den Unterprozess beendet (kill), seine Sperre fällt mit ihm
        code = -9
        zeilen = ((e.stdout or "") if isinstance(e.stdout, str) else "").strip().splitlines() + [
            f"Lauf per Knopf nach {int(KNOPF_GRENZE_S)} s beendet, der Sammler hing über seiner Frist"]
    except Exception as e:   # noqa: BLE001, der Knopf darf nie hängen bleiben
        code, zeilen = -1, [f"Lauf per Knopf nicht startbar: {type(e).__name__}: {e}"]
    finally:
        try:
            for z in zeilen[-3:]:
                print("  " + z, flush=True)
            s = lauf_status(eigener=False)
            # Das Ende fehlt, der Unterprozess schrieb lauf.json gar nicht, oder er scheiterte, ohne selbst einen Fehler zu schreiben. Nicht über
            # Zeitstempel: sie sind sekundengenau, ein Lauf in der Sekunde des vorigen Endes sähe sonst dessen Ergebnis als seines (gemessen).
            geschrieben = LAUF.exists() and LAUF.stat().st_mtime_ns != vorher
            if not geschrieben or not s["fertig"] or (code != 0 and not s["exit"]):
                LAUF.write_text(json.dumps({"start": lauf_seit[0], "fertig": iso_utc(), "exit": code if code is not None else -1,
                                            "fehler": (zeilen or ["abgebrochen"])[-1][:160]}))
            print(f"{iso_utc()} Lauf per Knopf beendet, Exit {code}", flush=True)
        finally:
            lauf_seit[0] = None


# Octicon sync-16 aus primer/octicons (icons/sync-16.svg, abgeholt 2026-09-08), nicht nachgezeichnet.
KREISPFEIL = ('<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M1.705 8.005a.75.75 0 0 1 .834.656 5.5 5.5 0 0 0 9.592 2.97l-1.204-1.204a.25.25 0 0 1 .177-.427h3.646a.25.25 0 0 1 .25.25v3.646a.25.25 0 0 1-.427.177l-1.38-1.38A7.002 7.002 0 0 1 1.05 8.84a.75.75 0 0 1 .656-.834ZM8 2.5a5.487 5.487 0 0 0-4.131 1.869l1.204 1.204A.25.25 0 0 1 4.896 6H1.25A.25.25 0 0 1 1 5.75V2.104a.25.25 0 0 1 .427-.177l1.38 1.38A7.002 7.002 0 0 1 14.95 7.16a.75.75 0 0 1-1.49.178A5.5 5.5 0 0 0 8 2.5Z"/></svg>')

# Tag- und Nachtmodus, Umschalter nach Lea Verou (seit 11.09.2026 zwei Segmente statt drei Zustände):
# hervorgehoben ist immer der Modus, der gerade wirkt, bei Systemfolge also prefers-color-scheme. Ein Klick
# auf das andere Segment schaltet um; entspricht die Wahl dem Systemwert, wird der Schlüssel entfernt und
# die Seite folgt wieder dem System, sonst wird der Wert gespeichert. Das Kopfskript liest die Wahl VOR dem
# ersten Rendern, sonst sähe «Dunkel auf hellem Gerät» einen hellen Blitz; try/catch, weil localStorage
# werfen kann. Nur «hell» und «dunkel» gelten, ein alter Wert «system» wird wie kein Wert gelesen.
MODI = (("hell", "modus.hell"), ("dunkel", "modus.dunkel"))
MODUS_KOPF = ("<script>try{var m=localStorage.getItem('gitpuls-modus');"
              "if(m==='hell'||m==='dunkel')document.documentElement.dataset.modus=m}catch(e){}</script>")

SKRIPT = """<script>
(function(){
var T=/*TEXTE*/;
var wurzel=document.documentElement,knoepfe=document.querySelectorAll('.modus button'),mq=matchMedia('(prefers-color-scheme: dark)');
function systemModus(){return mq.matches?'dunkel':'hell';}
function zeigeModus(){var w=wurzel.dataset.modus||systemModus();knoepfe.forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.wahl===w));});}
knoepfe.forEach(function(b){b.addEventListener('click',function(){var w=b.dataset.wahl,folgt=w===systemModus();
  if(folgt)delete wurzel.dataset.modus;else wurzel.dataset.modus=w;
  try{if(folgt)localStorage.removeItem('gitpuls-modus');else localStorage.setItem('gitpuls-modus',w);}catch(e){}
  zeigeModus();});});
mq.addEventListener('change',zeigeModus);
zeigeModus();
// Reiter des Rückblicks (?rueckblick=): die Leiste schaltet nur die Fenster ihres Abschnitts, die übrigen Parameter der Adresse
// bleiben. Die Vorgabe steht ohne Parameter in der Adresse.
// Seit 30.09.2026 merkt sich eine Leiste mit data-speicher (Rückblick) die Wahl im localStorage; der Parameter in der Adresse geht vor.
document.querySelectorAll('.reiter').forEach(function(nav){
  var reiter=nav.querySelectorAll('button'),fenster=nav.parentNode.querySelectorAll('.fenster');
  function zeige(f){if(nav.dataset.speicher)wurzel.setAttribute('data-rb',f);reiter.forEach(function(x){x.setAttribute('aria-pressed',String(x.dataset.fenster===f));});
    fenster.forEach(function(x){x.hidden=x.dataset.fenster!==f;});}
  reiter.forEach(function(b){b.addEventListener('click',function(){var f=b.dataset.fenster,q=new URLSearchParams(location.search);
    zeige(f);if(nav.dataset.speicher)try{localStorage.setItem(nav.dataset.speicher,f);}catch(e){}
    if(f===nav.dataset.vorgabe)q.delete(nav.dataset.param);else q.set(nav.dataset.param,f);
    history.replaceState(null,'',(q.toString()?'/?'+q:'/')+location.hash);});});
  if(nav.dataset.speicher&&!new URLSearchParams(location.search).has(nav.dataset.param))try{var m=localStorage.getItem(nav.dataset.speicher);
    if(m&&nav.querySelector('button[data-fenster="'+m+'"]'))zeige(m);}catch(e){}});
// Umschalter der Kacheln 2 und 3 (30.09.2026): jedes Fenster trägt seinen Umschalter; ein Klick zeigt alle Fenster der Gruppe um (Kachel,
// bei k2 auch Lagesatz und Ring), merkt die Wahl im localStorage und gibt den Fokus an denselben Knopf im nun sichtbaren Fenster.
function zeigeGruppe(g,f){var alle=document.querySelectorAll('.fenster[data-gruppe="'+g+'"]');
  if(!document.querySelector('.fenster[data-gruppe="'+g+'"][data-fenster="'+f+'"]'))return;
  wurzel.setAttribute('data-'+g,f);alle.forEach(function(x){x.hidden=x.dataset.fenster!==f;});
  // Ein Umschalter ausserhalb der Fenster (Aufklappdetail, 03.10.2026) zeigt die Wahl selbst an.
  document.querySelectorAll('nav.umschalter[data-gruppe="'+g+'"]').forEach(function(n){if(!n.closest('.fenster'))
    n.querySelectorAll('button').forEach(function(x){x.setAttribute('aria-pressed',String(x.dataset.fenster===f));});});}
document.querySelectorAll('nav.umschalter').forEach(function(nav){var g=nav.dataset.gruppe;
  nav.querySelectorAll('button').forEach(function(b){b.addEventListener('click',function(){var f=b.dataset.fenster;zeigeGruppe(g,f);
    try{localStorage.setItem('gitpuls-'+g,f);}catch(e){}
    var z=document.querySelector('.fenster[data-gruppe="'+g+'"][data-fenster="'+f+'"] nav.umschalter button[data-fenster="'+f+'"]');if(z&&z!==b)z.focus();});});});
['k2','k3','pd'].forEach(function(g){try{var f=localStorage.getItem('gitpuls-'+g);if(f)zeigeGruppe(g,f);}catch(e){}});
var kn=document.getElementById('lauf'),info=document.getElementById('laufinfo'),t0=null,iv=null;
// Uhrzeit im Browser über Intl (ebenfalls CLDR) in der Format-Locale der Seite, en-US also 12 Stunden (26.09.2026, vorher fest h23).
// Seit 10.10.2026 in der Zone des Servers (T.zone), wie jede andere Uhrzeit der Seite.
function uhr(iso){var o={timeStyle:'short'};if(T.zone)o.timeZone=T.zone;return new Date(iso).toLocaleTimeString(T.locale,o);}
function laeuft(s){kn.disabled=true;kn.classList.add('laeuft');info.textContent=T.laeuft.replace('{uhr}',uhr(s.start))+(s.phase?' · '+s.phase:'');}
function bereit(){kn.disabled=false;kn.classList.remove('laeuft');}
function sperre(n){bereit();kn.disabled=true;clearInterval(iv);var zeige=function(){info.textContent=T.sperre.replace('{n}',n);};zeige();
  iv=setInterval(function(){n--;if(n<=0){clearInterval(iv);kn.disabled=false;info.textContent='';}else zeige();},1000);}
function status(){return fetch('/lauf/status?lang='+wurzel.lang,{cache:'no-store'}).then(function(r){return r.json();});}
function poll(){setTimeout(function(){status().then(function(s){
  if(s.zustand==='laeuft'){laeuft(s);poll();return;}
  if(s.fertig&&s.fertig>=t0){if(s.exit===0)location.reload();else{bereit();info.textContent=T.fehler+(s.fehler||'Exit '+s.exit);}}else poll();
}).catch(poll);},1000);}
kn.addEventListener('click',function(){kn.disabled=true;fetch('/lauf',{method:'POST'}).then(function(r){return r.json().then(function(j){return [r.status,j];});}).then(function(x){
  if(x[0]===202){t0=x[1].gestartet;laeuft({start:t0,phase:T.startet});poll();}
  else if(x[0]===409){t0=x[1].start;laeuft({start:t0});poll();}
  else if(x[0]===429){sperre(x[1].sperre_s);}
  else{bereit();info.textContent=x[1].fehler||('HTTP '+x[0]);}
}).catch(function(e){bereit();info.textContent=T.kein_lauf+e.message;});});
status().then(function(s){if(s.zustand==='laeuft'){t0=s.start;laeuft(s);poll();}else if(s.sperre_s)sperre(s.sperre_s);else if(s.exit&&s.fehler)info.textContent=T.gescheitert+s.fehler;}).catch(function(){});

// Ablese-Zeiger, eine Funktion für alle Grafiken, Form aus einem anderen eigenen Dienst (am 12.09.2026 gelesen):
// zeiger() trägt die Ereignisse einer Zeichenfläche, der Rest setzt Linie, Marken und Kopfzeile.
// Maus und Stift: ein Finger läuft nicht durch, damit die Seite auf dem Telefon weiter scrollt.
function zeiger(flaeche,zeige,aus){
  function ab(ev){if(ev.pointerType==='touch')return;var r=flaeche.getBoundingClientRect();zeige((ev.clientX-r.left)/r.width);}
  flaeche.addEventListener('pointermove',ab);
  flaeche.addEventListener('pointerleave',aus);
  flaeche.addEventListener('pointercancel',aus);
}
// Grafiken in einer aufklappbaren Projektzeile bekommen ihren Zeiger erst beim ersten Öffnen, nie gegen eine
// verborgene Fläche. Gemessen am 13.09.2026 in Chrome 153: das geschlossene details lieferte dort 376 px, keine
// Nullen; der Aufbau beim Öffnen sichert Browser ab, die ein geschlossenes details ohne Mass führen.
document.querySelectorAll('.zbox').forEach(function(box){
  var d=box.closest('details');
  if(!d){baue(box);return;}
  d.addEventListener('toggle',function erst(){if(!d.open)return;d.removeEventListener('toggle',erst);baue(box);});
});
function baue(box){
 try{
  var svg=box.querySelector('svg'),plot=box.querySelector('.plot'),lab=box.querySelector('.zlab'),
      zeile=box.querySelector('.zzeile'),strich=svg.querySelector('.zl'),
      W=svg.viewBox.baseVal.width,H=svg.viewBox.baseVal.height,
      // Je Stelle: x, Zeigerzeile, Marke und y der vollen Reihe, Marke und y der Vergleichsreihe.
      stellen=svg.dataset.stellen.split(';').map(function(s){return s.split('|');});
  function aus(){box.classList.remove('zan');lab.innerHTML='';zeile.textContent='';}
  function zeige(f){
    // Rasten auf den nächsten Messpunkt. Bei gleichen Abständen ist das Math.round(f*(n-1)); die
    // Heute-Kachel hat ungleiche (der Punkt «jetzt», an Zeitumstellungen zwei Stundenraster),
    // dort trifft nur der Abstandsvergleich.
    var ziel=f*W,st=stellen[0],best=Infinity;
    stellen.forEach(function(s){var d=Math.abs(+s[0]-ziel);if(d<best){best=d;st=s;}});
    var xf=+st[0]/W,sr=svg.getBoundingClientRect(),pr=plot.getBoundingClientRect(),
        x=sr.left-pr.left+xf*sr.width,top0=sr.top-pr.top,marken=[];
    strich.setAttribute('x1',st[0]);strich.setAttribute('x2',st[0]);
    // Keine Marke, wo die Reihe keinen Wert hat: der Zeichner hat die zwei Felder dann leer gelassen.
    [[2,3,'voll'],[4,5,'vgl']].forEach(function(r){if(st[r[0]]==='')return;
      marken.push({y:top0+ +st[r[1]]/H*sr.height,t:st[r[0]],k:r[2]});});
    marken.sort(function(a,b){return a.y-b.y;});
    var ys=[],vor=-99;
    marken.forEach(function(m){var y=m.y<vor+14?vor+14:m.y;vor=y;ys.push(y);});
    // Erst Abstand, dann die ganze Gruppe zurück in die Zeichenfläche. Zwei gleiche Werte auf der
    // Grundlinie schoben die zweite Marke sonst unter das Bild in den Achsentext (gemessen live am
    // 12.09.2026: Fläche endet bei 1251.9, die Marke lag von 1258.6 bis 1273.2, der Achsentext ab
    // 1255.9). Zuerst die Ober-, dann die Unterkante: passt die Gruppe nicht mehr in die Höhe, ragt
    // sie oben heraus, dort steht die Zeigerzeile und kein Text.
    // rand ist die halbe Höhe einer Marke: .zmarke steht auf .7rem mal Zeilenhöhe 1.3, gemessen
    // 14.6 px. Wächst die Schrift der Marke, wächst diese Zahl mit.
    var rand=8,lo=top0+rand,hi=top0+sr.height-rand,schub=0;
    if(ys[0]<lo)schub=lo-ys[0];
    if(ys[ys.length-1]+schub>hi)schub=hi-ys[ys.length-1];
    var inhalt='';
    marken.forEach(function(m,i){var y=ys[i]+schub;
      inhalt+='<span class="zmarke '+m.k+(xf>.8?' links':'')+'" style="left:'+x.toFixed(1)+'px;top:'+y.toFixed(1)+'px">'+m.t+'</span>';});
    lab.innerHTML=inhalt;zeile.textContent=st[1];box.classList.add('zan');
  }
  zeiger(svg,function(f){try{zeige(f);}catch(e){aus();}},aus);
 }catch(e){console.error('Zeiger:',e);}   // eine kaputte Grafik nimmt weder die anderen mit noch die Seite
}
// Sprung aus der Heute-Kachel (seit 25.09.2026): die Projektzeile des Repos öffnet sich. open=true löst dasselbe
// toggle aus wie ein Klick, der Zeiger wird also wie beim normalen Öffnen gebaut. Auch beim Laden mit Anker in der
// Adresse, und beim zweiten Klick auf denselben Link, wo kein hashchange mehr feuert.
function oeffne(id){var d=document.getElementById(id);if(!d||d.tagName!=='DETAILS')return;var g=d.parentNode.closest('details.gruppe');if(g)g.open=true;d.open=true;}
// Gruppe «Ohne Commits diese Woche» und seit 04.10.2026 «und N weitere Repos» der Heute-Kachel: ihr Zustand steht als ?ruhend=offen
// und ?heute=offen in der Adresse, die Parameter der Reiterleisten und ein Anker bleiben stehen.
[['ruhend','details.gruppe.ruhend'],['heute','details.gruppe.heute-rest']].forEach(function(p){document.querySelectorAll(p[1]).forEach(function(g){g.addEventListener('toggle',function(){var q=new URLSearchParams(location.search);
  if(g.open)q.set(p[0],'offen');else q.delete(p[0]);history.replaceState(null,'',(q.toString()?'/?'+q:'/')+location.hash);});});});
// Block Handlungsbedarf: alle Einträge stehen seit 04.10.2026 offen da, nur ein Warnungseintrag klappt auf; offen steht er als
// ?handlung=warn:<owner>/<repo> aus data-zeichen in der Adresse, die übrigen Parameter und ?lang bleiben.
function handlungZeichen(){var z=[];document.querySelectorAll('#handlung details.hbeintrag').forEach(function(e){if(e.open)z.push(e.dataset.zeichen);});return z.join(',');}
document.querySelectorAll('#handlung details.hbeintrag').forEach(function(g){g.addEventListener('toggle',function(){
  var q=new URLSearchParams(location.search),z=handlungZeichen();if(z)q.set('handlung',z);else q.delete('handlung');
  history.replaceState(null,'',(q.toString()?'/?'+q:'/')+location.hash);});});
// Sprung auf einen Eintrag im Handlungsbedarf (02.10.2026), aus Projektzeile und Kopf: ein Warnungseintrag öffnet sich selbst. Der
// Klick läuft vor dem Sprung, das Ziel ist also sichtbar, wenn gescrollt wird.
function zeigeEintrag(id){var e=document.getElementById(id);if(!e)return;
  var d=e.querySelector('details.hbeintrag');if(d)d.open=true;}
document.querySelectorAll('a[href^="#hb-"]').forEach(function(a){a.addEventListener('click',function(){zeigeEintrag(a.getAttribute('href').slice(1));});});
if(location.hash.indexOf('#hb-')===0)zeigeEintrag(decodeURIComponent(location.hash.slice(1)));
document.querySelectorAll('a[href^="#p-"]').forEach(function(a){a.addEventListener('click',function(){oeffne(a.getAttribute('href').slice(1));});});
if(location.hash.indexOf('#p-')===0)oeffne(decodeURIComponent(location.hash.slice(1)));
})();
</script>"""


def utc(d):
    return datetime.combine(d, datetime.min.time(), TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tageszaehlung(c, jetzt=None):
    """Eigene Commits je lokalem Tag bis jetzt (10.10.2026, Audit S18): ein Zukunftscommit zählt weder in Serie, Rekorden noch Rückblick. Ein
    Bestand nur aus Zukunftscommits liess den Rückblick mit einem ersten Tag nach heute brechen, Blockfehler und 503 auf /gesund."""
    return {date.fromisoformat(k): n for k, n in c.execute(f"SELECT date(zeitstempel_utc,'localtime') k, COUNT(*) FROM commits WHERE zeitstempel_utc < ? "
                                                           f"AND {IN_ADRESSEN} GROUP BY k", (obergrenze(jetzt or db.jetzt(timezone.utc)), *ADRESSEN))}


def wochen(c, heute, n=8):
    start = heute - timedelta(days=heute.weekday(), weeks=n - 1)
    gez = dict(c.execute(f"SELECT strftime('%G-W%V', zeitstempel_utc,'localtime') k, COUNT(*) FROM commits WHERE zeitstempel_utc >= ? AND {IN_ADRESSEN} GROUP BY k", (utc(start), *ADRESSEN)).fetchall())
    return [(k, gez.get(k, 0)) for k in ((start + timedelta(weeks=i)).strftime("%G-W%V") for i in range(n))]


def datum(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ).date()


def wochenmontag(k):
    """Der Montag einer ISO-Woche («2026-W38» -> 14.09.2026). Die Rechnung bleibt auf den Montagswochen von
    strftime('%G-W%V'), nur die Beschriftung ist seit dem 20.09.2026 ein Datum: «2026-W38» verlangt eine
    Umrechnung im Kopf, ein Datum nicht."""
    return datetime.strptime(k + "-1", "%G-W%V-%u").date()


def wochenspanne(k):
    """Eine Woche als Datumsspanne mit Jahr (02.10.2026, «Beste Woche» reicht über den ganzen Bestand): «15.–21.09.2026»."""
    mo = wochenmontag(k)
    return fmt.spanne_jahr(mo, mo + timedelta(days=6))


def achse(mx, fest=None):
    """Y-Achse von null bis zu einer runden Obergrenze, zwei bis fünf Marken. Eine leere Reihe
    (Höchstwert 0) bekommt 0 und 1 statt einer Division durch null; genau dort brach die alte
    Sparkline, die `max(werte) or 1` rechnete und keine Achse zeichnete.
    `fest` erzwingt die Obergrenze: der Auslastungsverlauf steht immer auf 0 bis 100, sonst
    schwankte sein Massstab mit dem gezeigten Ausschnitt und zwei Prozent sähen aus wie achtzig."""
    if fest:
        return fest, [fest * i // 4 for i in range(5)]
    if mx <= 0:
        return 1, [0, 1]
    for schritt in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000):
        if mx <= schritt * 4:
            oben = -(-mx // schritt) * schritt
            return oben, [i * schritt for i in range(oben // schritt + 1)]
    return mx, [0, mx]


def punkte(werte):
    """Zahlen gleichmässig über die Breite verteilen; (x, y)-Paare mit x in 0..1 bleiben, wie sie sind."""
    if not werte:
        return []
    if isinstance(werte[0], tuple):
        return list(werte)
    n = len(werte)
    return [(0.5 if n == 1 else i / (n - 1), v) for i, v in enumerate(werte)]


def ohne_angebrochene(werte, angebrochen):
    """(Index der Spitze, Mittel) nur über die vollen Wochen, eine Regel für Rückblick und Detailkurve. Eine angebrochene
    Woche wird nie auf sieben Tage hochgerechnet, das wäre eine erfundene Zahl; sie fällt aus jedem Vergleich heraus und bleibt
    gezeichnet. Unter zwei vollen Wochen (None, None): eine Spitze und ein Mittel über einen Rest sind keine Aussage."""
    voll = [i for i in range(len(werte)) if i not in angebrochen]
    if len(voll) < 2:
        return None, None
    return max(voll, key=lambda i: werte[i]), sum(werte[i] for i in voll) / len(voll)


# Gerenderte Höhe der Balkengrafiken in px (style.css: .chart.gross 13rem, .chart.mini 6rem bei 16 px je rem). Das SVG wird senkrecht
# genau auf diese Höhe gestreckt; daraus folgen Grundstrich (1 px) und Mindesthöhe (2 px) in Einheiten der viewBox.
# ponytail: an die zwei rem-Werte gebunden, tests/test_web.py prüft beide; wer die Höhe ändert, ändert sie hier mit.
BALKEN_PX_H = {"gross": 208, "klein": 96, "mini": 96}
BALKEN_ANTEIL = .7   # Breite eines Balkens am Platz seiner Periode, der Rest ist Abstand


def kurve(werte, x_erst, x_letzt, klasse="klein", bezug=None, max_marke=None, was="Verlauf", vergleich=None,
          stellen=None, stellen_v=None, angebrochen=(), achse_bis=None, laufend=False, balken=False, plaetze=None, xlabels=None,
          laeuft=False, vgl_klasse="schnitt"):
    """Eine lesbare Kurve nach der Bauform einer laufenden eigenen Vorlage (Port 8446, am 08.09.2026 per
    curl gelesen): y-Achse und x-Enden als HTML neben dem Bild, waagrechtes Raster als <line> im
    SVG, Fläche und Linie darüber. Das SVG wird mit preserveAspectRatio="none" auf die Breite
    gestreckt, deshalb steht keine Schrift und kein Kreis darin, beides würde mitverzerren. Genau
    darum trägt die Vorlage jede Beschriftung in absolut positionierten <span>.

    werte:      Zahlen (gleichmässig verteilt) oder (x, y) mit x in 0..1, etwa eine Kumulierung, die
                bei der aktuellen Uhrzeit endet und nicht am Fensterende.
    vergleich:  zweite Reihe (x, y), gestrichelt in Blau ohne Fläche: dieselbe Kumulierung im gleich
                langen Fenster davor. Die Achse gilt für beide Reihen.
    bezug:      (wert, text) für eine gestrichelte waagrechte Bezugslinie, etwa den Durchschnitt.
    max_marke:  Text für den benannten Höchstwert im Bild; None lässt ihn weg.
    stellen:    je Punkt der vollen Reihe der Text der Zeigerzeile über der Grafik (Uhrzeit, Datum,
                Kalenderwoche), stellen_v dasselbe für die Vergleichsreihe. Nur damit bekommt die
                Grafik den Ablese-Zeiger; ohne sie bleibt sie, wie sie war.
    achse_bis:  feste Obergrenze der y-Achse statt der aus den Werten gerechneten (Auslastung: 100).
    laufend:    der letzte Punkt ist der laufende Zeitraum (Detailkurve, 25.09.2026): sein Segment trägt die Akzentfarbe wie jeder
                laufende Wert, gestrichelt, weil er angebrochen ist, und am aktuellen Wert steht ein Punkt (HTML, nicht im SVG, das
                gestreckt wird). Eine einzige Woche ist dann ein Punkt mit Wert statt eines Strichs.
    angebrochen: Indizes der vollen Reihe, deren Periode angebrochen ist (nur am Anfang oder Ende). Ihr Segment wird
                gestrichelt in Beschriftungsfarbe ohne Fläche gezeichnet, und die benannte Spitze sucht nur unter den übrigen.
    balken:     Commits je Periode als Balken statt Linie (27.09.2026, Rückblick und Detailkurve), in der Form der Mikro-Balken:
                gedämpft, der laufende Wert (laufend) in Akzentfarbe, eine angebrochene Periode nur als Umriss in Akzentfarbe, eine
                leere als Grundstrich von 1 px, eine mit Commits mindestens 2 px hoch. Keine Fläche. Jeder Balken steht in der Mitte
                seines Platzes, dort liegen auch Spitzenmarke und Ablesestelle.
    plaetze:    nur mit balken: feste Zahl von Plätzen, die Werte füllen die ersten (Detail: 7 Tage, Werte bis heute). Ein Platz ohne Wert
                bleibt leer, ohne Balken und ohne Ablesestelle, weil stellen mit den Werten endet.
    xlabels:    je Platz eine Beschriftung, mittig unter ihm, statt Anfang und Ende.
    laeuft:     die letzte Periode läuft noch (30.09.2026, Kacheln 2 und 3, Rückblick): ihr Balken heller (.laeuft) mit dem title «läuft
                noch», ihre Ablesestelle trägt denselben Zusatz. Bei einer Linie (Kachel 3) nur dieser Zusatz: Linie, Fläche und
                Punkt enden am letzten Punkt.
    vgl_klasse: Klasse der Vergleichsreihe zu Balken, «schnitt» (gleitender Durchschnitt) oder «schnitt ref» (Vorwoche je Wochentag,
                gestrichelt).
    Ein einziger Wert wird als kurzer waagrechter Strich in der Mitte gezeichnet, nicht als Fläche
    über das ganze Fenster: eine Fläche würde behaupten, der Wert habe das Fenster über gegolten."""
    m = plaetze or len(werte)
    pts = [((i + .5) / m, v) for i, v in enumerate(werte)] if balken else punkte(werte)
    n = len(pts)
    if not n:
        return ""
    if n == 1 and not balken:
        # Ein einziger Punkt am Rand (Monat am 1.: x = 0) rückt so weit hinein, dass Strich (±40), runde Kappe und Punkt in der Zeichenfläche
        # liegen; Strich, Punkt und Zeiger lesen alle dieses pts (10.10.2026).
        pts = [(min(max(pts[0][0], .045), .955), pts[0][1])]
    if laeuft and stellen and len(stellen) >= n:
        stellen = list(stellen)
        stellen[n - 1] = f'{stellen[n - 1]} · {t("laeuft_noch")}'
    H = 260
    vgl = punkte(vergleich or [])
    # ceil: der gleitende Durchschnitt (30.09.2026) bringt Dezimalwerte, die Achse bleibt ganzzahlig.
    oben, marken = achse(math.ceil(max(v for _, v in pts + vgl)), achse_bis)
    x = lambda f: f * 1000.0
    y = lambda v: H - H * min(v, oben) / oben
    ylab = "".join(f'<span style="top:{100 * (1 - m / oben):.1f}%">{fmt.zahl(m)}</span>' for m in reversed(marken))
    raster = "".join(f'<line x1="0" y1="{y(m):.1f}" x2="1000" y2="{y(m):.1f}"/>' for m in marken)

    def linie(p, cls, flaeche):
        if len(p) == 1:
            return f'<line class="{cls}" x1="{x(p[0][0]) - 40:.1f}" y1="{y(p[0][1]):.1f}" x2="{x(p[0][0]) + 40:.1f}" y2="{y(p[0][1]):.1f}"/>'
        ecken = [f"{x(a):.1f},{y(b):.1f}" for a, b in p]
        # Die Fläche endet beim letzten Punkt der Reihe, nicht am Fensterrand: eine Kumulierung bis zur
        # aktuellen Uhrzeit darf den Rest des Tages nicht als gefüllt behaupten.
        poly = f'<polygon class="flaeche" points="{x(p[0][0]):.1f},{H} {" ".join(ecken)} {x(p[-1][0]):.1f},{H}"/>' if flaeche else ""
        return f'{poly}<path class="{cls}" d="M{" L".join(ecken)}"/>'

    def volle_reihe(p):
        if not angebrochen:
            return linie(p, "l", True)
        if len(p) == 1:
            return "" if laufend else linie(p, "l teil", False)
        # ponytail: angebrochene Punkte liegen nur am Rand, die übrigen sind deshalb zusammenhängend; ein einzelner übriger
        # Punkt bekommt keinen eigenen Strich, die zwei gestrichelten Segmente treffen sich an ihm.
        voll = [q for i, q in enumerate(p) if i not in angebrochen]
        aus = linie(voll, "l", True) if len(voll) > 1 else ""
        for i in range(len(p) - 1):
            if i in angebrochen or i + 1 in angebrochen:
                jetzt_ = " jetzt" if laufend and i + 1 == len(p) - 1 else ""
                aus += f'<path class="l teil{jetzt_}" d="M{x(p[i][0]):.1f},{y(p[i][1]):.1f} L{x(p[i + 1][0]):.1f},{y(p[i + 1][1]):.1f}"/>'
        return aus

    def balken_reihe():
        e = H / BALKEN_PX_H[klasse]   # Einheiten der viewBox je px
        b = 1000.0 / m * BALKEN_ANTEIL
        aus = []
        for i, (fx, v) in enumerate(pts):
            teil = i in angebrochen
            cls = "b" + (" teil" if teil else " jetzt" if laufend and i == n - 1 else "") + (" laeuft" if laeuft and i == n - 1 else "")
            tip = f'<title>{html.escape(t("laeuft_noch"))}</title>' if laeuft and i == n - 1 else ""
            h = max(2 * e, H - y(v)) if v else e
            if teil and v:
                # Umriss: die Kontur (1.5 px, nicht skaliert) liegt innen, sonst schnitte die Unterkante sie halb ab.
                rect = f'<rect class="{cls}" x="{x(fx) - b / 2:.1f}" y="{H - h + .75 * e:.2f}" width="{b:.1f}" height="{h - 1.5 * e:.2f}"'
            else:
                rect = f'<rect class="{cls}{" leer" if not v else ""}" x="{x(fx) - b / 2:.1f}" y="{H - h:.2f}" width="{b:.1f}" height="{h:.2f}"'
            aus.append(f"{rect}>{tip}</rect>" if tip else f"{rect}/>")
        return "".join(aus)

    # Zu Balken ist die Vergleichsreihe der gleitende Durchschnitt (Kachel letzte 7 Tage, 30.09.2026): eine volle Linie in Textfarbe, die
    # sich von den gedämpften Balken abhebt; gestrichelt in Beschriftungsfarbe verschwände sie in deren Oberkanten.
    koerper = (balken_reihe() if balken else volle_reihe(pts)) + (linie(vgl, vgl_klasse if balken else "hz", False) if vgl else "")
    # Ein Durchschnitt über einen einzigen Punkt ist kein Vergleich, und ein Höchstwert von null ist
    # keine Aussage: beide bleiben dann weg, statt eine Linie auf der Grundlinie zu behaupten.
    bez = ""
    if bezug and bezug[0] and n != 1:
        bez = f'<line class="hz" x1="0" y1="{y(bezug[0]):.1f}" x2="1000" y2="{y(bezug[0]):.1f}"/>'
    marke = ""
    if laufend:
        fx, fv = pts[-1]
        marke += (("" if balken else f'<span class="pjetzt" style="left:{x(fx) / 10:.1f}%;top:{100 * y(fv) / H:.1f}%" aria-hidden="true"></span>')
                  + (f'<span class="pmarke" style="left:{x(fx) / 10:.1f}%;top:{100 * y(fv) / H:.1f}%">{fmt.zahl(fv)}</span>' if n == 1 and not plaetze else ""))
    kandidaten = [i for i in range(n) if i not in angebrochen]
    if max_marke and kandidaten and max(pts[i][1] for i in kandidaten):
        i = max(kandidaten, key=lambda i: pts[i][1])
        seite = " rechts" if x(pts[i][0]) > 660 else ""
        marke = (f'<span class="pmarke{seite}" style="left:{x(pts[i][0]) / 10:.1f}%;top:{100 * y(pts[i][1]) / H:.1f}%">'
                 f'{html.escape(max_marke)}</span>')
    # Eine einzige Beschriftung (ein Punkt, oder Anfang gleich Ende) steht mittig unter dem Punkt, der bei einem Wert auf 50 % liegt, und
    # bleibt innerhalb der Zeichenfläche (25.09.2026). Vorher griff die Regel für die letzte Beschriftung (translateX(-100%)), und das
    # einzige Datum einer Detailkurve mit einer Woche rutschte nach links aus dem Bild (glyphlabs-web: «1.09.» statt «21.09.»).
    if xlabels:
        xlab = "".join(f'<span class="platz" style="left:{100 * (i + .5) / m:.1f}%">{html.escape(l)}</span>' for i, l in enumerate(xlabels))
    elif n == 1 or x_letzt == x_erst:
        xlab = f'<span class="einzeln">{html.escape(x_erst)}</span>'
    else:
        xlab = f'<span style="left:0%">{html.escape(x_erst)}</span><span style="left:100%">{html.escape(x_letzt)}</span>'
    # Ablesestellen des Zeigers, je Stelle sechs Felder, durch «|» getrennt und durch «;» gereiht:
    #   x | Text der Zeigerzeile | Marke volle Reihe | ihr y | Marke Vergleichsreihe | ihr y
    # Hier stehen die Werte fertig da, das Skript liest sie nur ab und rechnet nichts zurück. Wo eine
    # Reihe keinen Punkt hat, bleiben ihre zwei Felder leer, und dort steht dann keine Marke. Die volle
    # Reihe legt die Stelle an, sie gewinnt bei gemeinsamer x-Koordinate die Zeigerzeile.
    st = {}
    for i, (p, texte) in enumerate(((pts, stellen), (vgl, stellen_v))):
        for (fx, v), text in zip(p, texte or []):
            e = st.setdefault(f"{x(fx):.1f}", [text, "", "", "", ""])
            e[1 + 2 * i], e[2 + 2 * i] = fmt.zahl(v) if float(v).is_integer() else fmt.dezimal(v, 1), f"{y(v):.1f}"
    zdaten = ";".join("|".join([k] + e) for k, e in sorted(st.items(), key=lambda kv: float(kv[0])))
    zattr = f' data-stellen="{html.escape(zdaten)}"' if zdaten else ""
    zlinie = f'<line class="zl" x1="0" y1="0" x2="0" y2="{H}"/>' if zdaten else ""
    zlab = '<div class="zlab" aria-hidden="true"></div>' if zdaten else ""
    # Punkt und Marken stehen in .zf, das genau so gross ist wie das SVG (25.09.2026): in .plot rechneten ihre Prozente auch die
    # x-Beschriftung und im Detail dessen padding-right mit, der Punkt der laufenden Woche lag bis 30 px unter und 12 px rechts
    # neben dem Ende seiner Linie.
    bild = (f'<div class="chart {klasse}"><div class="ylab">{ylab}</div><div class="plot"><div class="zf">'
            f'<svg viewBox="0 0 1000 {H}" preserveAspectRatio="none" role="img" '
            f'aria-label="{html.escape(t("kurve.aria", was, n, fmt.zahl(max(v for _, v in pts)), fmt.zahl(oben)))}"{zattr}>'
            f'<g class="grid">{raster}</g>{koerper}{bez}{zlinie}</svg>{marke}</div>{zlab}'
            f'<div class="xlab">{xlab}</div></div></div>')
    # Ein Element bleibt ein Element: die Karten teilen ein Subgrid mit fester Zeilenzahl, die Zeigerzeile
    # darf dort keine eigene Zeile werden.
    return f'<div class="zbox"><div class="zzeile"></div>{bild}</div>' if zdaten else bild


def sw(fein=False, balken=False, schnitt=False, ref=False):
    """Farbfleck der Legende, wie die Vorlage ihn im Satz unter der Grafik führt (.sub i.sw); balken: ein kleiner Balken statt des Strichs;
    schnitt: der volle Strich des gleitenden Durchschnitts in Textfarbe."""
    return f'<i class="sw{" fein" if fein else ""}{" balken" if balken else ""}{" schnitt" if schnitt else ""}{" ref" if ref else ""}" aria-hidden="true"></i>'


def zeigt_bezug(werte, wert):
    """Wird die Bezugslinie überhaupt gezeichnet? Nur dann darf die Legende sie nennen."""
    return bool(wert) and len(werte) != 1


def wochenreihen(c, heute, jetzt, n=12, zeilen=False):
    """Je Repo die Commits je ISO-Woche über n Wochen, plus die Wochenschlüssel. Eine Abfrage. Obergrenze jetzt: ein Commit
    mit Zeitstempel in der Zukunft zählte sonst in der laufenden Woche mit (gemessen 13.09.2026, Wochenwert 0 wurde 1).
    zeilen=True (25.09.2026) liefert als drittes je Repo (plus, minus, offen) der laufenden Woche aus DERSELBEN Abfrage wie der
    Wochenwert der Projektzeile, als viertes (27.09.2026) je Repo die Commits je Tag der laufenden Woche, Montag bis Sonntag, nach
    Ortsdatum wie die Wochenkachel und aus denselben Zeilen: ihre Summe ist der Wochenwert."""
    start = heute - timedelta(days=heute.weekday(), weeks=n - 1)
    montag = heute - timedelta(days=heute.weekday())
    schluessel = [(start + timedelta(weeks=i)).strftime("%G-W%V") for i in range(n)]
    reihen, jetzt_zeilen, jetzt_tage = {}, {}, {}
    # Je Zeile von commits die Codezeilen aus commit_dateien (ZEILEN_SQL), in Python je Repo und Woche summiert.
    je = {}
    for repo, k, tag, plus, minus, _, _, geladen in c.execute(f"SELECT repo, strftime('%G-W%V', zeitstempel_utc,'localtime') k, date(zeitstempel_utc,'localtime'), "
                                                              f"{ZEILEN_SQL if zeilen else '0, 0, 0, 0, 1'} FROM commits "
                                                              f"WHERE zeitstempel_utc >= ? AND zeitstempel_utc < ? AND {IN_ADRESSEN}", (utc(start), obergrenze(jetzt), *ADRESSEN)):
        m_, p_, mi_, o_ = je.get((repo, k), (0, 0, 0, 0))
        je[(repo, k)] = (m_ + 1, p_ + plus, mi_ + minus, o_ + (geladen is None))
        if k == schluessel[-1]:
            jetzt_tage.setdefault(repo, [0] * 7)[(date.fromisoformat(tag) - montag).days] += 1
    for (repo, k), (m, plus, minus, offen) in je.items():
        reihen.setdefault(repo, {})[k] = m
        if k == schluessel[-1]:
            jetzt_zeilen[repo] = (plus, minus, offen)
    aus = schluessel, {repo: [d.get(k, 0) for k in schluessel] for repo, d in reihen.items()}
    assert all(sum(v) == aus[1][r][-1] for r, v in jetzt_tage.items()), "Tage der Woche und Wochenwert aus denselben Zeilen"
    return (*aus, jetzt_zeilen, jetzt_tage) if zeilen else aus


def karenzgrenze(c):
    """Ab diesem Zeitstempel gilt ein Repo als noch vorhanden: der letzte erfolgreiche Sammellauf aus laeufe (der
    Lauf, der die Liste vollständig geholt und alles festgeschrieben hat) minus KARENZ_H. Nicht MAX(gesehen) über
    die Repos: ein abgebrochener Lauf, der nur einen Teil der Stempel gesetzt hätte, verschöbe sonst die Grenze
    für alle anderen (Codex-Befund 11.09.2026). Ohne Lauf gilt alles als vorhanden."""
    letzter = c.execute("SELECT MAX(gestartet_utc) FROM laeufe").fetchone()[0]
    if not letzter:
        return ""
    return (datetime.fromisoformat(letzter.replace("Z", "+00:00")) - timedelta(hours=KARENZ_H)).strftime("%Y-%m-%dT%H:%M:%SZ")




def ci_text(ci):
    return t("ci.text", ci["workflow"], ci["alter"])


STUFEN_RANG = {"critical": 0, "high": 1, "medium": 2, "low": 3}   # Sortierung der Warnungen, höchste zuerst


def gh_link(url, text, title=""):
    """Ein Link auf GitHub in einem neuen Tab, ohne Zugriff der Zielseite auf dieses Fenster (rel=noopener)."""
    titel = f' title="{html.escape(title)}"' if title else ""
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener"{titel}>{html.escape(text)}</a>'


def paket_title(w):
    return t("warn.paket_title", w["paket"], t("stufe", w["stufe"]), w["behoben"])


def warn_text(w):
    return t("warn.text", w["n"], t("stufe", w["stufe"]))


# Ein Dependabot-Zweig heisst dependabot/<ökosystem>/<paket>-<version> (02.10.2026). Die Version beginnt mit einer Ziffer und trägt
# mindestens einen Punkt, damit eine Gruppe wie «dev-bcd18dff68» nicht als Paket «dev» in Version «bcd…» gelesen wird; ein Zweig
# ausserhalb des Musters steht mit seinem Namen.
DEPENDABOT_MUSTER = re.compile(r"dependabot/[^/]+/(.+)-(\d+(?:\.[\w-]+)+)")
# Ein Gruppenupdate heisst dependabot/<ökosystem>/[<verzeichnis>/]<gruppe>-<10 Hex-Zeichen> (03.10.2026). Der Dienst-Token darf keine
# Pull Requests lesen (403 «Resource not accessible by personal access token», gemessen im Container am 03.10.2026), die Pakete der
# Gruppe kennt er also nicht: angezeigt wird «Gruppe dev (npm)», Gruppenname ohne Hash, Ökosystem lesbar.
DEPENDABOT_GRUPPE = re.compile(r"dependabot/([^/]+)/(?:.+/)?([^/]+)-[0-9a-f]{10}")
OEKOSYSTEME = {"npm_and_yarn": "npm", "github_actions": "GitHub Actions", "go_modules": "Go", "gitsubmodule": "Git submodules"}


def id_teil(text):
    """Ein Name als Teil einer HTML-id, umkehrbar eindeutig (Codex-Audit 11.10.2026, Befund 11): Buchstaben, Ziffern und «-» bleiben, jedes andere
    Zeichen wird «_<hex>_». Vorher wurde jedes solche Zeichen «-», und fix/a, fix-a und fix.a ergaben dieselbe id."""
    return "".join(z if z.isascii() and z.isalnum() or z == "-" else f"_{ord(z):x}_" for z in text)


def hb_id(art, repo, zweig=""):
    """Die id eines Eintrags im Block Handlungsbedarf, Ziel der Befundangaben in Projektzeile und Kopf (02.10.2026). «~» trennt den Zweig,
    es kommt in id_teil nie vor: o/a-b mit Zweig c und o/a mit Zweig b-c bleiben verschieden."""
    return f"hb-{art}-{id_teil(repo)}" + (f"~{id_teil(zweig)}" if zweig else "")


def dependabot_update(repo_url, zweig, seit):
    """Ein offenes Dependabot-Update: Paket und Zielversion aus dem Zweignamen, Link auf die gefilterte PR-Liste dieses Zweigs (ohne
    Leserecht auf Pull Requests kennt der Dienst die PR-Nummer nicht; die Liste zeigt genau den einen PR)."""
    m = DEPENDABOT_MUSTER.fullmatch(zweig)
    g = None if m else DEPENDABOT_GRUPPE.fullmatch(zweig)
    paket = m[1] if m else t("hb.gruppe", g[2], OEKOSYSTEME.get(g[1], g[1].replace("_", " "))) if g else zweig
    return {"zweig": zweig, "paket": paket, "version": m[2] if m else "", "seit": seit, "gruppe": bool(g),
            "url": repo_url + "/pulls?q=" + quote(f"is:pr is:open head:{zweig}")}


def dependabot_wert(k):
    """Die Updates eines Repos im Aufklappdetail: je Update Paket und Zielversion als Link, gedämpft das Alter. Der Block Handlungsbedarf
    setzt seit 03.10.2026 je Update eine eigene Zeile (update_eintrag in handlung_block)."""
    return ", ".join(f'<a href="{html.escape(u["url"])}" target="_blank" rel="noopener">'
                     + (html.escape(u["paket"]) if u["gruppe"] else f'<span translate="no">{html.escape(u["paket"])}</span>')
                     + (f' {html.escape(u["version"])}' if u["version"] else "") + "</a>"
                     + (f' <span class="hbrest">{html.escape(seit_text(u["seit"]))}</span>' if u["seit"] is not None else "")
                     for u in k["updates"])


def server_wert(k):
    """Der Stand des Servers: laufender Tag, bei einem Befund dazu die Zahl hinter main und die Betreffe der nicht ausgerollten Commits
    (aus deploy.json, geschrieben von tools/deploy-stand.sh). Im Aufklappdetail; der Block Handlungsbedarf setzt sie seit 03.10.2026 als Zeilen."""
    d = k["server"]
    wert = f'<span translate="no">{html.escape(d["tag"])}</span>'
    if not k.get("server_befund"):
        return wert
    # Bei einem Release-Bild nennt der Text die neuere Version selbst, die Zeilen aus deploy.json («v1.0.4 neuere Version verfügbar») wiederholten ihn.
    commits = "" if d.get("neuer") else "; ".join(f'<span translate="no">{html.escape(z.get("sha", ""))} {html.escape(z.get("betreff", ""))}</span>' for z in d.get("commits") or ())
    return f'{wert}, {html.escape(hinter_text("ps.hinter", d, k["branch"]))}' + (f": {commits}" if commits else "")


# Texte für ein Release-Bild mit neuerem Tag v* statt «hinter main»; BEIDES, wenn dazu ein Commit-Bild hinter main läuft (14.10.2026).
NEUER = {"ps.hinter": "ps.hinter_neuer", "kurz.server": "kurz.server_neuer", "bf.server": "bf.server_neuer", "lage.server": "lage.server_neuer"}
BEIDES = {"bf.server": "bf.server_beides", "lage.server": "lage.server_beides"}


def hinter_text(schluessel, d, branch):
    """Der Rückstand eines Dienstes: ein Release-Bild mit neuerem Tag v* («neuer» in deploy.json, seit e74e281) nennt die neuere Version,
    ein Commit-Bild bleibt «hinter main» (14.10.2026)."""
    return t(NEUER[schluessel], d["hinter"], f'v{d["neuer"]}') if d.get("neuer") else t(schluessel, d["hinter"], branch)


# deploy.json ist veraltet, wenn ein Eintrag älter ist als zwei Takte von gitpuls-deploy.timer (stündlich, deploy/gitpuls-deploy.timer):
# ein Takt darf ausfallen, zwei heissen, der Timer läuft nicht (10.10.2026, Audit S14). Vorher galt jeder Eintrag als frisch.
DEPLOY_ALT_S = 2 * 3600


def deploy_alter_s(d, jetzt):
    """Sekunden seit der Messung eines Eintrags aus deploy.json, None ohne gültigen Zeitpunkt (fehlt, ungültig, ohne Zone). None heisst
    seit 11.10.2026 nicht gemessen, nie frisch (Codex-Audit, Befund 7)."""
    return sekunden_seit(d.get("gemessen") if isinstance(d, dict) else None, jetzt)


def deploy_stand(d):
    """Ein Eintrag aus deploy.json mit einem gelungenen Stand: tag, origin_main und branch als Text, hinter und bildrelevant als ganze Zahl,
    commits als Liste. Sonst False; der Eintrag gilt dann als nicht gemessen (Typprüfung, Codex-Audit 11.10.2026)."""
    zahl_ = lambda v: isinstance(v, int) and not isinstance(v, bool)
    return (isinstance(d, dict) and isinstance(d.get("tag"), str) and isinstance(d.get("origin_main"), str) and zahl_(d.get("hinter"))
            and zahl_(d.get("bildrelevant")) and isinstance(d.get("commits", []), list) and isinstance(d.get("branch", ""), (str, type(None))))


def deploy_gleich(d):
    """Läuft genau der Kopf des Default-Branch? Seit 11.10.2026 aus «gleich» (tools/deploy-stand.sh vergleicht die Commits); ein Eintrag von
    davor vergleicht die Hashes als Präfix. 0 hinter allein heisst das nicht (Codex-Audit, Befund 13)."""
    if "gleich" in d:
        return d["gleich"] is True
    a, b = d["tag"], d["origin_main"]
    return len(min(a, b, key=len)) >= 7 and (a.startswith(b) or b.startswith(a))


def deploy_lesen():
    """deploy.json vom Host-Timer: (Einträge je Dienst, Fehler); jeder Eintrag trägt sein Repo (seit 11.10.2026, Codex-Audit Befund 9). Fehlt
    die Datei oder ist sie kein JSON-Objekt, ist der Deploy-Stand nicht gemessen (03.10.2026, Codex-Audit); vorher wurde daraus «kein Befund»."""
    try:
        deploy = json.loads(DEPLOY.read_text())
    except OSError:
        return {}, t("dn.nicht_gemessen")
    except ValueError:
        return {}, t("dn.unlesbar")
    return (deploy, None) if isinstance(deploy, dict) else ({}, t("dn.unlesbar"))


def projekte(c, heute, jetzt):
    """Ein Eintrag je Repo: Zustand, Befunde, Chips, letzte fünf Betreffzeilen. Letzter Commit und Betreffzeilen enden bei jetzt,
    gleich wie die Kacheln (fenster_commits): ein Zukunftscommit machte tage_her negativ, das Alter «vor 1 Min.» und seinen Betreff
    zum jüngsten (Codex-Befund 13.09.2026)."""
    bis = obergrenze(jetzt)
    deploy, deploy_fehler = deploy_lesen()
    # Die Einträge je Repo, je Dienst einer (Befund 9: Produktion und Staging eines Repos überschrieben sich vorher).
    je_repo = {}
    for name, e in sorted(deploy.items()):
        if isinstance(e, dict) and isinstance(e.get("repo"), str):
            je_repo.setdefault(e["repo"], []).append((name, e))
    # Repos mit Dienst auf dem Server (aus server.json und deploy.json): nur für sie ist ein fehlender Deploy-Stand eine Messlücke. Ist
    # server.json unlesbar, bleiben die Repos aus deploy.json, und die Lücke selbst steht im Handlungsbedarf (Befund 6).
    dienste = (server_daten() or {}).get("dienste")
    dienst_repos = {d["repo"] for d in (dienste if isinstance(dienste, list) else ()) if isinstance(d, dict) and isinstance(d.get("repo"), str)} | set(je_repo)
    zweige = {}
    for z in c.execute("SELECT repo, name, ahead, behind, letzter_utc FROM zweige ORDER BY ahead DESC"):
        zweige.setdefault(z["repo"], []).append(z)
    pruef = {p["repo"]: p for p in c.execute("SELECT * FROM pruefungen")}
    warn_je = {}
    for w in c.execute("SELECT repo, nummer, paket, oekosystem, manifest, stufe, behoben, url, summary, scope, geoeffnet, bereich, ghsa, cve FROM warnungen"):
        warn_je.setdefault(w["repo"], []).append(dict(w))
    for ws in warn_je.values():
        ws.sort(key=lambda w: (STUFEN_RANG.get(w["stufe"], 9), w["paket"], w["nummer"]))
    karten = []
    # Eine Karte je Zeile in repos, die Zeile steht für die GitHub-ID (repos.id); der Name ist nur die Anschrift
    # und wird vom Sammler bei einer Umbenennung in allen Tabellen nachgezogen.
    for r in c.execute("SELECT repo, default_branch, sprache, pushed_at, id, html_url, fehler, fehler_utc FROM repos WHERE gesehen >= ? ORDER BY repo", (karenzgrenze(c),)):
        letzter = c.execute(f"SELECT MAX(zeitstempel_utc) FROM commits WHERE repo = ? AND zeitstempel_utc < ? AND {IN_ADRESSEN}", (r["repo"], bis, *ADRESSEN)).fetchone()[0]
        letzter_tag = datum(letzter) if letzter else None
        tage_her = (heute - letzter_tag).days if letzter_tag else None
        if tage_her is None:
            zustand = "kein eigener Commit"
        elif tage_her <= SCHWELLEN["aktiv"]:
            zustand = "aktiv"
        elif tage_her <= SCHWELLEN["ruht"]:
            zustand = f"ruht seit {tage_her} Tagen"
        else:
            zustand = f"liegt still, seit {tage_her} Tagen"
        befunde, kurz, messung = [], [], []
        eintraege = je_repo.get(r["repo"], [])
        # Messvertrag (03.10.2026): ein Eintrag mit «fehler» trägt den Stand der letzten gelungenen Messung, ohne «tag» gab es keine. Je Dienst
        # geprüft; bei mehreren Diensten eines Repos steht der Dienst vor dem Grund.
        if r["repo"] in dienst_repos and (deploy_fehler or not eintraege):
            messung.append(("server", deploy_fehler or t("dn.nicht_gemessen"), None))
        elif r["repo"] in dienst_repos:
            for name, e in eintraege:
                wer = f"{name}: " if len(eintraege) > 1 else ""
                alter = deploy_alter_s(e, jetzt)
                if e.get("fehler") or not deploy_stand(e):
                    messung.append(("server", wer + (str(e.get("fehler") or t("dn.nicht_gemessen"))), e.get("gemessen") if alter is not None else None))
                elif alter is None:   # ein ungültiger Zeitpunkt ist nicht gemessen, nie frisch (Befund 7)
                    messung.append(("server", wer + t("dn.zeit_ungueltig"), None))
                elif alter > DEPLOY_ALT_S:   # veraltet (Audit S14), der Stand bleibt sichtbar
                    messung.append(("server", wer + t("dn.veraltet", zeitpunkt(e["gemessen"])), e["gemessen"]))
        # Der Stand der Zeile ist der Dienst mit dem grössten Rückstand, der das Bild berührt: eine veraltete Produktion verschwindet nicht
        # hinter einem aktuellen Staging desselben Repos.
        staende = [e for _, e in eintraege if deploy_stand(e)]
        d = max(staende, key=lambda e: (bool(e["hinter"] and e["bildrelevant"]), e["bildrelevant"], e["hinter"]), default=None)
        # Server hinter main ist seit dem Serverstand eine Dienstzeile (server_html), kein GitHub-Befund mehr.
        # Auf der Karte bleibt die Kurzform, wenn Commits das Bild berühren (Massstab des Care-Tag-Wächters).
        # Seit 02.10.2026 ist er eine eigene Art im Handlungsbedarf. Jede Befundangabe der Zeile ist ein Sprunglink auf ihren Eintrag dort.
        sprung = lambda art, text, zweig="": f'<a href="#{hb_id(art, r["repo"], zweig)}">{html.escape(text)}</a>'
        server_befund = bool(d and d.get("hinter") and d.get("bildrelevant"))
        if server_befund:
            kurz.append(sprung("server", hinter_text("kurz.server", d, r["default_branch"])))
        # Zweige mit dem Präfix dependabot/ (25.09.2026) zählen nicht einzeln: je Repo eine Zahl, im Block eine Zeile mit Link auf die
        # offenen Pull Requests von Dependabot, in der Befundspalte «N Dependabot-Updates».
        dep_seit = []
        for z in zweige.get(r["repo"], []):
            if z["ahead"] > 0:
                seit = (heute - datum(z["letzter_utc"])).days if z["letzter_utc"] else None
                if z["name"].startswith(DEPENDABOT_PRAEFIX):
                    dep_seit.append(dependabot_update(r["html_url"] or f'https://github.com/{r["repo"]}', z["name"], seit))
                    continue
                # Ohne Zeitstempel bleibt der Zweig ein Befund: ein unbekanntes Alter wird nicht als frisch gewertet. Ein frischer Zweig
                # steht als Information gedämpft in der Zeile, ohne Link, weil er keinen Eintrag im Handlungsbedarf hat.
                if z["letzter_utc"] and jetzt - datetime.fromisoformat(z["letzter_utc"].replace("Z", "+00:00")) <= ZWEIG_FRIST:
                    kurz.append(html.escape(t("kurz.zweig", z["name"], z["ahead"])))
                    continue
                kurz.append(sprung("zweige", t("kurz.zweig", z["name"], z["ahead"]), z["name"]))
                # Teile statt eines fertigen Satzes: der Block Handlungsbedarf setzt Zweigname, Befund und Alter in eigene Zellen.
                befunde.append({"zweig": z["name"], "ahead": z["ahead"], "behind": z["behind"], "seit": seit, "branch": r["default_branch"]})
        dep_seit.sort(key=lambda u: u["gruppe"])   # Gruppenupdates ans Ende des Eintrags (03.10.2026), sonst Reihenfolge der Zweige
        if dep_seit:
            kurz.append(sprung("dependabot", t("kurz.dependabot", len(dep_seit))))
        # CI und Sicherheitswarnungen (25.09.2026) als Befund wie die Zweige: kein Rot, der Text in der Befundspalte der Zeile.
        p = pruef.get(r["repo"])
        ci = {"workflow": p["ci_workflow"] or t("ci.workflow"), "alter": alter_text(p["ci_utc"], (heute - datum(p["ci_utc"])).days, jetzt)} \
            if p and p["ci"] == "fehlgeschlagen" and p["ci_utc"] else None
        warn = {"n": p["warnungen"], "stufe": p["warn_stufe"]} if p and p["warnungen"] else None
        # Die Befundspalte trägt fertiges HTML: die Sicherheitswarnungen sind ein Link auf die Dependabot-Liste des Repos (25.09.2026).
        dependabot = (r["html_url"] or f'https://github.com/{r["repo"]}') + "/security/dependabot"
        # Abruf-Fehler (26.09.2026, Codex-Review): GitHub wies das Repo im letzten Lauf ab (403, 404, 451 …); seine Zahlen sind dann auf
        # dem Stand des letzten gelungenen Laufs. Code, Meldung und der erste Lauf der Reihe aus repos.fehler und repos.fehler_utc.
        abruf = None
        if r["fehler"]:
            abruf = {"code": r["fehler"].split(" ", 1)[0], "text": r["fehler"],
                     "seit": (heute - datum(r["fehler_utc"])).days if r["fehler_utc"] else None}
            kurz.insert(0, sprung("abruf", t("kurz.abruf", abruf["code"])))
        # Messlücken bei CI und Sicherheit (03.10.2026): der letzte Stand bleibt sichtbar, die Lücke ist ein eigener Befund.
        for was, fehler_, gemessen in (("ci", "ci_fehler", "ci_gemessen"), ("sicherheit", "sicher_fehler", "sicher_gemessen")):
            if p and p[fehler_]:
                messung.append((was, p[fehler_], p[gemessen]))
        if messung:
            kurz.insert(1 if abruf else 0, sprung("messung", t("kurz.messung", ", ".join(dict.fromkeys(t(MESSGROESSE[w]) for w, _, _ in messung)))))
        if ci:
            kurz.append(sprung("ci", ci_text(ci)))
        if warn:
            kurz.append(sprung("warn", warn_text(warn)))
        letzte = c.execute(f"SELECT zeitstempel_utc, betreff FROM commits WHERE repo = ? AND zeitstempel_utc < ? AND {IN_ADRESSEN} ORDER BY zeitstempel_utc DESC LIMIT 5", (r["repo"], bis, *ADRESSEN)).fetchall()
        karten.append({
            "repo": r["repo"], "id": r["id"], "name": r["repo"].split("/")[1], "zustand": zustand, "befunde": befunde,
            "sprache": r["sprache"] or "–", "branch": r["default_branch"], "letzter": letzter_tag, "tage_her": tage_her,
            "letzte": letzte, "server": d, "kurz": kurz, "letzter_utc": letzter or "", "abruf": abruf,
            "ci": ci, "warn": warn, "ci_zustand": p["ci"] if p else None, "sicher_zustand": p["sicherheit"] if p else None,
            "ci_fehler": p["ci_fehler"] if p else None, "sicher_fehler": p["sicher_fehler"] if p else None, "messung": messung,
            "warnliste": warn_je.get(r["repo"], []) if warn else [], "dependabot": dependabot,
            "url": r["html_url"] or f'https://github.com/{r["repo"]}', "dependabot_zweige": len(dep_seit),
            "updates": dep_seit, "server_befund": server_befund,
        })
    # Nach letztem Commit, neuester zuerst, unabhängig von Zustand oder Befund: der Befund steht als
    # Zweigzeile in der Karte und im Lagesatz, nicht als Rang und nicht als blauer Rahmen (Blau liest
    # sich als «ausgewählt», gemessen 11.09.2026).
    karten.sort(key=lambda k: k["letzter_utc"], reverse=True)
    return karten


def befund(ser, karten, summe, sieben=False):
    """Lagesatz, Serienzahl, Rangliste der Woche und die Befundgruppen. Eine Funktion, damit Satz und Zahlen nicht
    auseinanderlaufen. Seit 25.09.2026 rechnen Satz und Rangliste auf der Kalenderwoche statt über sieben rollende Tage:
    `k["woche"]` ist der Wochenwert der Projektzeile (wochenreihen), `summe` die Zahl der Wochenkachel, also alle eigenen
    Commits ab Montag 00:00 bis jetzt, auch in Repos ausserhalb der Karenz. Vorher stand im Kopf «89 Commits in velotrip,
    letzte 7 Tage» und in der Projektzeile 51 (gemessen 24.09.2026, 23:59).
    Die Rangliste: die drei Repos mit den meisten Commits der Woche, (Name, Zahl, Anteil an summe), bei Gleichstand nach
    letztem Commit (Reihenfolge der Karten). Die Befunde kommen gruppiert: ein Eintrag je Zweigname und Stand."""
    teile, zahlen = [], []
    if ser["aktuell"] >= 2:
        teile.append(t("lage.serie", ser["aktuell"]))
        zahlen.append((ser["aktuell"], t("serie.tage", ser["aktuell_seit"])))
    elif ser["aktuell"] == 1 and ser.get("heute"):   # nur mit einem Commit von heute (Audit S20), eine Serie bis gestern ist eine Pause
        teile.append(t("lage.heute_wieder"))
        zahlen.append((1, t("serie.heute")))
    else:
        pause = min((k["tage_her"] for k in karten if k["tage_her"] is not None), default=None)
        teile.append(t("lage.pause", pause) if pause else t("lage.noch_keiner"))
        zahlen.append((pause or 0, t("serie.pause", pause)))
    # "Vor allem X" behauptete Vorherrschaft, wo eine Mehrheit von 27 % stand; der Anteil sagt, wie viel
    # "am meisten" hier wert ist.
    rang = [(k["name"], k["woche"], k["woche"] * 100 / summe) for k in sorted((k for k in karten if k["woche"]), key=lambda k: -k["woche"])[:3]]
    # Der Satz folgt der Reihe darunter (25.09.2026): zuerst die Woche, dann die Serie, dann die Zweige.
    # sieben (30.09.2026): Rangliste und Satz über die letzten 7 Tage, `k["woche"]` trägt dann deren Wert (Umschalter der Kachel 2).
    teile.insert(0, t("lage.am_meisten7" if sieben else "lage.am_meisten", rang[0][0], rang[0][2]) if rang else t("lage.leer7" if sieben else "lage.woche_leer"))
    # Server hinter main zählt seit 02.10.2026 mit, aus derselben Kartenmenge wie Kopfzahl und Handlungsbedarf.
    server = sum(1 for k in karten if k.get("server_befund"))
    zweige = sum(len(k["befunde"]) for k in karten)   # jeder Befund ist ein Zweig vor main, je Repo einer
    rot = sum(1 for k in karten if k.get("ci"))
    gewarnt = sum(1 for k in karten if k.get("warn"))
    updates = sum(1 for k in karten if k.get("dependabot_zweige"))   # je Repo ein Punkt, nicht je Zweig
    abruf = sum(1 for k in karten if k.get("abruf"))   # Repos, die GitHub im letzten Lauf abwies (26.09.2026)
    messung = sum(1 for k in karten if k.get("messung"))   # Repos mit einem nicht gemessenen Stand (03.10.2026)
    # Eine Art: ihr eigener Satz wie bisher (bis 13.09.2026 hängte ein «n» an «wartet» und schrieb «wartetn»). Mehrere Arten: ein Satz
    # «N Punkte mit Handlungsbedarf.» (25.09.2026), damit der Lagesatz bei 1280 px einzeilig bleibt; die Arten nennt Kopfspalte 3.
    arten = [(n, s_, a) for n, s_, a in ((abruf, "lage.abruf", "abruf"), (messung, "lage.messung", "messung"), (zweige, "lage.zweige", "zweige"), (rot, "lage.ci", "ci"),
                                          (server, "lage.server", "server"), (gewarnt, "lage.warn", "warn"), (updates, "lage.dependabot", "dependabot")) if n]
    if len(arten) == 1:
        teile.append(art_satz(arten[0][2], arten[0][1], arten[0][0], karten))
    elif arten:
        teile.append(t("lage.punkte", abruf + messung + zweige + rot + server + gewarnt + updates))
    # Gleicher Zweigname und gleicher Stand (vor, dahinter, Zielzweig, Alter) ergeben eine Zeile mit allen Repos. Das Alter
    # gehört zum Stand: ein Sammelalter über verschieden alte Zweige wäre eine erfundene Zahl.
    gruppen = {}
    for k in karten:
        for b in k["befunde"]:
            gruppen.setdefault((b["zweig"], b["ahead"], b["behind"], b["branch"], b["seit"]), (b, []))[1].append(k)
    handlungen = [(ks, b) for b, ks in gruppen.values()]
    return " ".join(teile), zahlen, rang, handlungen


def seit_text(tage):
    """«seit heute», «seit 1 Tag», «seit N Tagen»: bis 25.09.2026 stand «seit 1 Tagen»."""
    return t("seit", tage)


def ez(n, eins, mehr):
    return f"{n} {eins if n == 1 else mehr}"


def kuerzen(s, n=80):
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


DELTA_MIN = 10   # unter so vielen Commits in der Vorwoche sagt ein Prozentwert nichts («+800 %» aus 1 gegen 9)


def delta_html(a, b, was):
    """Grosse Differenz in Prozent mit Pfeil neben der Zahl, Vorperiode klein darunter. Keine Farbe. Liegt die Vorperiode unter
    DELTA_MIN, steht statt des Prozentwerts «Vorwoche N» (25.09.2026): gitpuls zeigte «↑ +800 %» gegen eine Vorwoche mit 1."""
    if b < DELTA_MIN:
        return f'<span class="delta">{t("delta.vorwoche", fmt.zahl(b))}</span>', f'{was} {fmt.zahl(b)}'
    d = (a - b) * 100 / b
    pfeil = "↑" if d > 0 else "↓" if d < 0 else "→"
    return f'<span class="delta">{pfeil} {fmt.prozent(d, vorzeichen=True)}</span>', f'{was} {fmt.zahl(b)}'


def karte_kurve(tage, heute):
    """Die Grafik im Aufklappdetail (27.09.2026): die sieben Tage der laufenden Kalenderwoche, Montag bis Sonntag, ein Balken je Tag auf
    sieben festen Plätzen, für jedes Repo dieselbe Breite. Vorher zwölf Wochen ab der ersten mit Commits, auf die volle Breite gestreckt:
    ein Repo mit einer Woche stand als ein Balken über die ganze Fläche. Die Tage kommen aus wochenreihen, derselben Menge wie der
    Wochenwert der Zeile. Heute in Akzentfarbe, Tage nach heute behalten ihren Platz ohne Balken und ohne Ablesestelle. Keine Mittellinie:
    der Bezug ist «Vorwoche» im Stand daneben."""
    montag = heute - timedelta(days=heute.weekday())
    woche = [montag + timedelta(days=i) for i in range(7)]
    bisher = tage[:heute.weekday() + 1]
    oben, _ = achse(max(bisher))
    # Eine Zeile (27.09.2026): ohne Mittel ist die Legende kurz genug, die zweite Zeile mit «Achse bis N» entfällt.
    legende = f'<span class="lz">{sw(balken=True)} {t("kk.reihe")} · {t("kk.achse", fmt.zahl(oben))}</span>'
    grafik = kurve(bisher, "", "", klasse="mini", was=t("kk.reihe"), stellen=[fmt.wochentag_datum(d) for d in woche], laufend=True, balken=True,
                   plaetze=7, xlabels=[fmt.wochentag(d) for d in woche])
    return f'<div class="pkurve">{grafik}<p class="sub legende2">{legende}</p></div>'


def tage_kurve(je_tag, heute):
    """30 oder 90 Tage bis heute im Aufklappdetail (03.10.2026, Umschalter 7/30/90 Tage), aus derselben Abfrage wie die Mikrokurve
    (tagesreihen). Bis TAGESPUNKTE_MAX Tage ein Balken je Tag, heute in Akzentfarbe wie in der Wochengrafik; darüber einer je Kalenderwoche ab
    Montag nach der Regel des Rückblicks: die erste Woche, wenn der Fensterrand sie schneidet, und die laufende sind angebrochen, zählen nur ihre
    Tage im Fenster und stehen als Umriss. 90 Tagesbalken lägen in der 26rem breiten Detailgrafik keine 4 px auseinander."""
    von = heute - timedelta(days=len(je_tag) - 1)
    woche = len(je_tag) > TAGESPUNKTE_MAX
    if not woche:
        werte, stellen, angebrochen = list(je_tag), [fmt.wochentag_datum(von + timedelta(days=i)) for i in range(len(je_tag))], set()
    else:
        montag0 = von - timedelta(days=von.weekday())
        werte = [0] * ((heute - montag0).days // 7 + 1)
        for i, v in enumerate(je_tag):
            werte[(von + timedelta(days=i) - montag0).days // 7] += v
        montage = [montag0 + timedelta(weeks=w) for w in range(len(werte))]
        stellen = [fmt.spanne(max(m, von), min(m + timedelta(days=6), heute)) for m in montage]
        angebrochen = {i for i, m in enumerate(montage) if m < von} | {len(montage) - 1}
    oben, _ = achse(max(werte))
    legende = f'<span class="lz">{sw(balken=True)} {t("rb.was", woche)} · {t("kk.achse", fmt.zahl(oben))}</span>'
    grafik = kurve(werte, fmt.datum(von), fmt.datum(heute), klasse="mini", was=t("rb.was", woche), stellen=stellen, angebrochen=angebrochen,
                   laufend=not woche, balken=True, laeuft=woche)
    return f'<div class="pkurve">{grafik}<p class="sub legende2">{legende}</p></div>'


def kpi_html(n, label, zeile1_html, meta_html, grafik, legende, nav="", rahmen=True):
    """Zwei Gruppen (26.09.2026): oben Zahl, Bezeichnung und Vergleichszeile, darunter abgesetzt die Metazeilen (.kmeta), dann Kurve
    oder Aufteilung und die Legende. Alle drei Kacheln gleich gebaut, damit Zahl, Titel und Vergleichszeile über die Reihe auf einer
    Linie liegen; .kmeta hält zwei Zeilen Höhe, auch wo nur eine steht. zeile1_html und meta_html sind schon escapet."""
    # Mit Umschalter steht er seit 01.10.2026 fest rechts oben auf Höhe der Zahl, Titel und Vergleichszeile laufen darunter über die volle
    # Breite (vorher neben dem Titel, wo «Commits diesen Monat» ihn umbrechen liess); ohne Rahmen liefert die Funktion nur den Inhalt,
    # den umschalt_kachel je Fenster in eine gemeinsame Kachel stapelt.
    inhalt = (f'<div class="wert{" mit-nav" if nav else ""}"><span class="big mid">{fmt.zahl(n)}</span>{nav}<span class="lbl">{label}</span>'
              f'<span class="delta">{zeile1_html}</span></div>'
              f'<div class="kmeta">{meta_html}</div>{grafik}' + (f'<p class="sub">{legende}</p>' if legende else ""))
    return f'<div class="card tile kpi">{inhalt}</div>' if rahmen else inhalt


def meta_zeile(inhalt_html, klasse="", title=""):
    """Eine Metazeile einer Kachel, gedämpft; inhalt_html ist schon escapet."""
    t_attr = f' title="{html.escape(title)}"' if title else ""
    return f'<span class="delta bezug{klasse}"{t_attr}>{inhalt_html}</span>'


def kumuliert(werte):
    aus, summe = [], 0
    for v in werte:
        summe += v
        aus.append(summe)
    return aus


def tag0(tag):
    """Mitternacht dieses lokalen Kalendertages als UTC-Zeitpunkt. Eine Grenze für die ganze Seite: Heute-Kachel,
    Kalenderwoche, Rückblick und Projektzeilen. Der Tagesbeginn 06:00, der am 20.09.2026 nur wenige Stunden hielt,
    ist zurückgebaut — ein Tag läuft von 00:00 bis 23:59:59, und in einer frei weitergegebenen Fassung ist eine
    eigenwillige Tagesgrenze ein Fehler und keine Eigenheit."""
    return datetime.combine(tag, datetime.min.time(), TZ).astimezone(timezone.utc)


def vorwoche_fenster(heute, jetzt):
    """Die Vorwoche zum selben Zeitpunkt: ab Montag 00:00 der Vorwoche bis zum selben Wochentag im selben Abstand ab dessen Tagesbeginn.
    Eine Stelle für Wochenkachel und Projektzeilen (seit 25.09.2026; vorher rechneten die Projektzeilen bis Ende des gleichen
    Wochentags nach Datum, und «zum selben Zeitpunkt» stimmte dort nicht). Der Abstand zählt ab tag0(heute), nicht «jetzt minus
    sieben Tage», das läge an Wechseltagen der Sommerzeit eine Stunde daneben."""
    return vorfenster(heute - timedelta(days=heute.weekday()), heute, jetzt, 7)


def vorfenster(von, heute, jetzt, tage):
    """Die Vorperiode zum selben Zeitpunkt für ein Fenster von `von` 00:00 bis jetzt: `tage` früher, ab tag0(von - tage) bis zum Tag
    `heute - tage` im selben Abstand ab dessen Tagesbeginn. Eine Fensterlogik für Woche, 7 und 30 Tage und Projektzeilen (Codex-Audit
    11.10.2026, Befund 10: 7 und 30 Tage verglichen vorher mit ganzen Vortagen, die laufende Periode endete aber jetzt)."""
    return tag0(von - timedelta(days=tage)), tag0(heute - timedelta(days=tage)) + (jetzt.astimezone(timezone.utc) - tag0(heute))


def obergrenze(ende):
    """Halboffene Obergrenze als ISO-Zeitstempel. Die Zeitstempel sind sekundengenau: ein «jetzt» mit Bruchteilen zählt die
    laufende Sekunde mit, volle Sekunden (Mitternacht, Tests) bleiben die halboffene Grenze."""
    if ende.microsecond:
        ende = ende.replace(microsecond=0) + timedelta(seconds=1)
    return iso_utc(ende.astimezone(timezone.utc))


def fenster_commits(c, start, ende):
    """Eigene Commits mit start <= Zeitstempel < ende (aware), jüngster zuerst: (zeitstempel_utc, repo, betreff).
    Zahl und Liste der Heute-Kachel kommen aus diesen Zeilen."""
    return c.execute(f"SELECT zeitstempel_utc, repo, betreff FROM commits WHERE zeitstempel_utc >= ? AND zeitstempel_utc < ? AND {IN_ADRESSEN} "
                     "ORDER BY zeitstempel_utc DESC", (iso_utc(start), obergrenze(ende), *ADRESSEN)).fetchall()


def repo_aufteilung(commits):
    """Die Zeilen von fenster_commits je Repo: (repo, Zahl, letzter Zeitstempel), nach Zahl absteigend, bei Gleichstand
    nach letztem Commit, jüngster zuerst. Keine eigene Abfrage: die Aufteilung darf kein zweites Fenster haben."""
    je = {}
    for t, r, _ in commits:
        n, letzt = je.get(r, (0, ""))
        je[r] = (n + 1, max(letzt, t))
    return sorted(((r, n, t) for r, (n, t) in je.items()), key=lambda x: (x[1], x[2]), reverse=True)


def fensterwerte(c, von, bis, jetzt=None):
    """Ein Fenster, eine Abfrage, ein Rand: Commits je Tag (Kurve), Summe (Zahl) und verschiedene Repos aus
    denselben Zeilen. Vorher zählte die Repozahl nur nach unten begrenzt, ein Commit mit Zeitstempel in der
    Zukunft hätte sie erhöht, Zahl und Kurve aber nicht (Codex-Befund 11.09.2026). Seit 10.10.2026 endet das Fenster
    auch bei jetzt (Audit S18): ein Zukunftscommit am heutigen Kalendertag zählte in der 30-Tage-Kachel mit."""
    zeilen = c.execute(f"SELECT date(zeitstempel_utc, 'localtime') k, repo FROM commits WHERE date(zeitstempel_utc, 'localtime') BETWEEN ? AND ? "
                       f"AND zeitstempel_utc < ? AND {IN_ADRESSEN}",
                       (von.isoformat(), bis.isoformat(), obergrenze(jetzt or db.jetzt(timezone.utc)), *ADRESSEN)).fetchall()
    je_tag = {}
    for k, _ in zeilen:
        je_tag[k] = je_tag.get(k, 0) + 1
    return [je_tag.get((von + timedelta(days=i)).isoformat(), 0) for i in range((bis - von).days + 1)], len({r for _, r in zeilen})


def zeitfensterwerte(c, start, ende, tage, zeilen=False):
    """Wie fensterwerte, aber mit Zeitgrenzen statt Datumsgrenzen: Commits je lokalem Kalendertag über `tage` Tage
    ab dem Tag von `start`, dazu die verschiedenen Repos, aus einer Abfrage mit beiden Rändern. Die Wochenkachel
    braucht das, weil ihr Fenster bei «jetzt» endet und das Vergleichsfenster beim selben Wochentag zur selben
    Uhrzeit; eine Zählung nach Datum träfe dort den ganzen Tag (Harte Grenze: eine Fenstermenge je Kachel).
    zeilen=True (25.09.2026) liefert als drittes (plus, minus, offen) aus DENSELBEN Zeilen: Summe der Codezeilen und die Zahl der
    Commits, deren Zeilen noch nicht geladen sind."""
    zeilen_ = c.execute(f"SELECT date(zeitstempel_utc, 'localtime') k, repo{', ' + ZEILEN_SQL if zeilen else ''} FROM commits WHERE zeitstempel_utc >= ? AND zeitstempel_utc < ? AND {IN_ADRESSEN}",
                        (iso_utc(start), obergrenze(ende), *ADRESSEN)).fetchall()
    je_tag = {}
    for k, *_ in zeilen_:
        je_tag[k] = je_tag.get(k, 0) + 1
    tag1 = start.astimezone(TZ).date()
    werte = [je_tag.get((tag1 + timedelta(days=i)).isoformat(), 0) for i in range(tage)], len({z[1] for z in zeilen_})
    if not zeilen:
        return werte
    # (plus, minus, offen, ausgeschlossene Zeilen, ausgeschlossene Dateien, ohne Zeilenangabe) aus DENSELBEN Zeilen wie Zahl und
    # Repozahl. offen: noch nicht geladen (NULL); ohne Zeilenangabe: nicht verfügbar (0), trägt keine Dateizeile und damit nichts zur Summe.
    return (*werte, (sum(z[2] for z in zeilen_), sum(z[3] for z in zeilen_), sum(1 for z in zeilen_ if z[6] is None),
                     sum(z[4] for z in zeilen_), sum(z[5] for z in zeilen_), sum(1 for z in zeilen_ if z[6] == 0)))


# Codezeilen je Commit aus commit_dateien nach der Regel db.zaehlt() (25.09.2026), als Teilabfragen an eine Zeile von commits: gezählt
# hinzugefügt, gezählt entfernt, ausgeschlossene Zeilen, ausgeschlossene Dateien, dazu dateien_geladen. Eine Stelle für Kachel und
# Projektzeile.
_D = "FROM commit_dateien d WHERE d.sha = commits.sha"
ZEILEN_SQL = (f"(SELECT COALESCE(SUM(CASE WHEN zaehlt(d.pfad, d.plus, d.minus) THEN d.plus END), 0) {_D}), "
              f"(SELECT COALESCE(SUM(CASE WHEN zaehlt(d.pfad, d.plus, d.minus) THEN d.minus END), 0) {_D}), "
              f"(SELECT COALESCE(SUM(CASE WHEN zaehlt(d.pfad, d.plus, d.minus) THEN 0 ELSE d.plus + d.minus END), 0) {_D}), "
              f"(SELECT COUNT(*) {_D} AND NOT zaehlt(d.pfad, d.plus, d.minus)), dateien_geladen")


# Zwischen einer Zahl (Ziffern oder Zahlwort beider Sprachen) und dem folgenden Wort steht in den Lagesätzen ein geschütztes Leerzeichen
# (25.09.2026): «Seit 2 / Tagen» oder «Sieben / Punkte» brach sonst über zwei Zeilen.
# Eine Zahl endet nie auf einem Satzzeichen (26.09.2026): «Exit 1. gitpuls: Timer …» band sonst den Punkt samt Leerzeichen an «gitpuls:»
# und nahm der Satzanfangsregel ihr Leerzeichen.
ZAHL_WORT = re.compile(r"(?<![\w'’.,-])(\d(?:[\d'’.,\u00a0\u202f]*\d)?%?|(?i:" + "|".join(sorted({*texte_de.WORTE.values(), *texte_en.WORTE.values()}, key=len, reverse=True)) + r")) (?=\S)")


# Das erste Wort jedes Satzes (am Anfang oder nach «. », «! », «? ») mit dem folgenden verbunden (26.09.2026): sonst hing der Anfang des
# nächsten Satzes am Zeilenende («… Seit / 3 Tagen ohne Unterbruch»).
SATZANFANG = re.compile(r"(^|[.!?][ \u00a0])([^\s]+) (?=\S)")


# Satzgrenze: nach «.», «!» oder «?» ein normales Leerzeichen. Die Bindungen darüber lassen genau dieses Leerzeichen stehen (eine Zahl nimmt keinen
# Satzpunkt mit, siehe ZAHL_WORT).
SATZENDE = re.compile(r"(?<=[.!?]) (?=\S)")


def geschuetzt(satz):
    """Der Lagesatz als HTML: Zahl und folgendes Wort sowie das erste Wort jedes Satzes und das folgende mit geschütztem Leerzeichen
    verbunden. Beide Zonen gehen hier durch (lage_block, server_lage_block). Seit dem 26.09.2026 (zweiter Lauf) ist zudem jeder Satz ein
    eigenes Element .satz (inline-block, höchstens so breit wie die Zeile): ein Satz, der auf eine Zeile passt, bricht nie um, die Umbrüche
    fallen zwischen die Sätze; nur ein Satz, der länger als eine Zeile ist, bricht in sich um, und dort gelten die Bindungen weiter. Vorher
    stand live «… 31 % der Commits. Seit 3 Tagen» am Ende der ersten Zeile und «ohne Unterbruch.» auf der zweiten."""
    gebunden = SATZANFANG.sub("\\1\\2\u00a0", ZAHL_WORT.sub("\\1\u00a0", html.escape(satz)))
    return " ".join(f'<span class="satz">{teil}</span>' for teil in SATZENDE.split(gebunden))


def zeilen_werte(plus, minus, als_html=False):
    """«+1'234 / −56» (26.09.2026, vorher «1'234 Zeilen dazu, 56 entfernt»): Plus und echtes Minuszeichen. Im HTML trägt das Plus die
    Akzentfarbe (.zplus), das Minus bleibt gedämpft (.zminus); kein Rot und kein Grün."""
    p, m = fmt.zeilen(plus, minus)
    return f'<span class="zplus">{p}</span> / <span class="zminus">{m}</span>' if als_html else f"{p} / {m}"


def zeilen_text(plus, minus, als_html=False):
    """«+1'234 / −56 Zeilen»."""
    return t("zeilen", zeilen_werte(plus, minus, als_html))


def prozent(n, vor):
    """«, +16 %» mit echtem Minuszeichen («, −23 %»), leer ohne Vergleichswert; Schreibweise aus dem Formatmodul."""
    return f', {fmt.prozent((n - vor) * 100 / vor, vorzeichen=True)}' if vor else ""


def vergleich(beschriftung, n, vor):
    """Vergleichszeile einer Kennzahl-Kachel (25.09.2026): «gestern: 133, −23 %». Text und HTML aus derselben Vorlage der Texttabelle;
    im HTML trägt der Vergleichswert die volle Textfarbe (.vwert), Beschriftung und Prozent bleiben gedämpft."""
    return {"zeile1": t("kachel.vergleich", beschriftung, fmt.zahl(vor), prozent(n, vor)),
            "zeile1_html": t("kachel.vergleich", html.escape(beschriftung), f'<span class="vwert">{fmt.zahl(vor)}</span>', html.escape(prozent(n, vor)))}


def kacheldaten(c, heute, jetzt):
    """Die drei Kennzahl-Kacheln: jede zeigt genau den Vergleich ihrer Zahl. Kumulierte Commits im eigenen
    Fenster als volle Linie, dieselbe Kumulierung im Vergleichsfenster gestrichelt, die grosse Zahl ist der
    Endpunkt der vollen Linie. Heute zeigt statt einer Kurve die Commits seit lokaler Mitternacht als Liste.
    «in N Repos» zählt die verschiedenen Repos genau der Commits, die auch in Zahl und Kurve stecken, ohne
    Schnitt mit der Kartenmenge nach Karenz: sonst zählt ein ausgeschiedenes Repo in der Zahl, aber nicht in
    der Repozahl (Codex-Befund 11.09.2026, live «686 Commits in 7 Repos» bei 8 Repos in derselben Abfrage).
    Kopf und Karten bleiben bei der Kartenzählung."""
    # Heute: der ganze laufende Kalendertag bis jetzt (Obergrenze jetzt, damit ein Zeitstempel aus der Zukunft nicht
    # mitzählt) gegen den ganzen gestrigen, je von Mitternacht zu Mitternacht in der Zone des Systems. Das ist die
    # eine benannte Ausnahme von der Regel «nie eine angebrochene Periode gegen eine ganze» (Entscheid 20.09.2026):
    # die Kernzeit liegt von 21 bis 6 Uhr, ein Uhrzeitschnitt innerhalb von 24 Stunden trennt eine Arbeitsnacht
    # mitten durch. Der Preis ist benannt: am Morgen steht ein angebrochener Tag gegen einen ganzen, der Prozentwert
    # fällt dann strukturell negativ aus. Die Beschriftung bleibt «gestern N, +x %».
    # Seit 13.09.2026 ohne 24-Stunden-Kurve: sie trug nichts, solange der Tag leer oder fast leer war.
    # Seit 25.09.2026 statt der Commitliste die Aufteilung je Repo (Entscheid 25.09.2026, ersetzt den vom 13.09.): Zahl,
    # Repozahl und Aufteilung aus derselben Menge fenster_commits. Ist der Tag leer, zeigt die Kachel den letzten
    # Kalendertag mit Commits (nicht zwingend gestern), wieder über fenster_commits von dessen Mitternacht bis zur nächsten.
    heute0, gestern0 = tag0(heute), tag0(heute - timedelta(days=1))
    commits_heute = fenster_commits(c, heute0, jetzt.astimezone(timezone.utc))
    vor1 = len(fenster_commits(c, gestern0, heute0))
    # Aufteilung und Commitliste (seit 25.09.2026 wieder unter der Aufteilung) kommen aus denselben Zeilen `tag_zeilen`.
    zuletzt, tag_zeilen = None, commits_heute
    if not commits_heute:
        letzter = c.execute(f"SELECT MAX(zeitstempel_utc) FROM commits WHERE zeitstempel_utc < ? AND {IN_ADRESSEN}", (iso_utc(heute0), *ADRESSEN)).fetchone()[0]
        if letzter:
            zuletzt = datum(letzter)
            tag_zeilen = fenster_commits(c, tag0(zuletzt), tag0(zuletzt + timedelta(days=1)))
    aufteilung = repo_aufteilung(tag_zeilen)
    aus = [{"n": len(commits_heute), "vor": vor1, "aufteilung": aufteilung, "commits": tag_zeilen, "zuletzt": zuletzt, "label": t("kachel.heute"),
            **vergleich(t("kachel.gestern"), len(commits_heute), vor1), "zeile2": ""}]

    # Letzte 7 Tage (30.09.2026, ersetzt die Kalenderwoche): heute und die sechs Kalendertage davor bis jetzt, ein Balken je Tag, heute
    # rechts, verglichen mit den ganzen sieben Tagen davor. Darüber der gleitende 7-Tage-Durchschnitt je Tag (Mittel der sieben Tage bis
    # und mit diesem Tag), dafür reicht das Vergleichsfenster: die Werte der 14 Tage aus denselben zwei Fenstern wie Zahl und Vergleichswert.
    start7, jetzt_u = heute - timedelta(days=6), jetzt.astimezone(timezone.utc)
    tage_w, beruehrt, (plus_w, minus_w, offen_w, aus_w, dateien_w, ohne_w) = zeitfensterwerte(c, tag0(start7), jetzt_u, 7, zeilen=True)
    # Vergleichswert und Codezeilen davor bis zum selben Zeitpunkt (vorfenster, Befund 10); der gleitende Durchschnitt braucht die ganzen Tage.
    tage_v, _, (plus_v, minus_v, offen_v, _, _, ohne_v) = zeitfensterwerte(c, *vorfenster(start7, heute, jetzt, 7), 7, zeilen=True)
    vierzehn = zeitfensterwerte(c, tag0(heute - timedelta(days=13)), tag0(start7), 7)[0] + tage_w
    schnitt7 = [sum(vierzehn[i + 1:i + 8]) / 7 for i in range(7)]
    # Codezeilen (25.09.2026) aus denselben zwei Fenstern wie Zahl und Vergleichswert; solange der Altbestand für eines davon nicht
    # geladen ist, steht gedämpft «Zeilen werden nachgeladen» statt einer halben Zahl. Die sieben Tage davor stehen im title.
    zeile3 = html.escape(t("kachel.nachladen")) if offen_w or offen_v else zeilen_text(plus_w, minus_w, als_html=True)
    tage7 = [start7 + timedelta(days=i) for i in range(7)]
    aus.append({"n": sum(tage_w), "vor": sum(tage_v), "tage": tage_w, "schnitt": schnitt7, "label": t("kachel.label30", 7),
                "stellen": [fmt.wochentag_datum(d) for d in tage7], "xlabels": [fmt.wochentag(d) for d in tage7],
                **vergleich(t("kachel.davor", 7), sum(tage_w), sum(tage_v)),
                "zeile2": t("kachel.an_tagen", sum(1 for v in tage_w if v), 7, beruehrt), "paare": (sum(1 for v in tage_w if v), 7, beruehrt),
                "zeile3": zeile3, "zeile3_title": "" if offen_w or offen_v else
                    t("kachel.zeilen_davor", zeilen_text(plus_v, minus_v)) + "\n" + t("kachel.aus_title", t("kachel.aus_7"), fmt.zahl(aus_w), dateien_w, fmt.zahl(db.EINZEL_MAX), ohne_w + ohne_v),
                "legende": t("kachel.leg7", sw(balken=True), sw(schnitt=True)), "was": t("kachel.was7")})

    # 30 Tage: rollendes Fenster nach lokalem Datum gegen die 30 Tage davor. Die gestrichelte Linie zeigt die ganzen Vortage, der Vergleichswert
    # zählt sie bis zum selben Zeitpunkt (vorfenster, Befund 10) wie Woche und 7 Tage.
    n30 = 30
    tage_t, beruehrt = fensterwerte(c, heute - timedelta(days=n30 - 1), heute, jetzt)
    tage_v, _ = fensterwerte(c, heute - timedelta(days=2 * n30 - 1), heute - timedelta(days=n30), jetzt)
    voll, vor = kumuliert(tage_t), kumuliert(tage_v)
    vor_n = sum(zeitfensterwerte(c, *vorfenster(heute - timedelta(days=n30 - 1), heute, jetzt, n30), n30)[0])
    aus.append({"n": voll[-1], "vor": vor_n, "voll": voll, "vergleich": vor, "label": t("kachel.label30", n30),
                "stellen": [fmt.datum_jahr(heute - timedelta(days=n30 - 1 - i)) for i in range(n30)],
                "stellen_v": [fmt.datum_jahr(heute - timedelta(days=2 * n30 - 1 - i)) for i in range(n30)],
                **vergleich(t("kachel.davor", n30), voll[-1], vor_n),
                "zeile2": t("kachel.an_tagen", sum(1 for v in tage_t if v), n30, beruehrt), "paare": (sum(1 for v in tage_t if v), n30, beruehrt), "x": (t("kachel.tag", 1), t("kachel.tag", n30)),
                "legende": t("kachel.leg30", sw(), sw(True), n30), "was": t("kachel.was30", n30), "laeuft": True})
    return aus


def kachelvarianten(c, heute, jetzt):
    """Die zweiten Fenster der Kacheln 2 und 3 für die Umschalter (30.09.2026): Kalenderwoche und laufender Monat; der Reiter
    Vormonat ist am 04.10.2026 entfallen. Die Vorgaben (7 Tage, 30 Tage) kommen aus kacheldaten. Jede Variante rechnet Zahl, Vergleich, aktive
    Tage und Repos aus ihren eigenen Fenstern."""
    jetzt_u = jetzt.astimezone(timezone.utc)
    # Woche: Montag bis jetzt gegen die Vorwoche zum selben Zeitpunkt (vorwoche_fenster, wie die Projektzeilen); sieben feste Plätze
    # Mo bis So, künftige Tage bleiben leer. Gestrichelt die ganze Vorwoche je Wochentag (01.10.2026, vorher eine waagrechte Linie auf
    # ihrem Tagesmittel, die nichts verglich), auf den Balkenmitten wie der gleitende Durchschnitt bei 7 Tagen.
    montag, n_tage = heute - timedelta(days=heute.weekday()), heute.weekday() + 1
    tage_w, beruehrt, (plus_w, minus_w, offen_w, aus_w, dateien_w, ohne_w) = zeitfensterwerte(c, tag0(montag), jetzt_u, n_tage, zeilen=True)
    tage_v, _, (plus_v, minus_v, offen_v, _, _, ohne_v) = zeitfensterwerte(c, *vorwoche_fenster(heute, jetzt), n_tage, zeilen=True)
    vorwoche, _ = fensterwerte(c, montag - timedelta(days=7), montag - timedelta(days=1), jetzt)
    woche = [montag + timedelta(days=i) for i in range(7)]
    an_w = sum(1 for v in tage_w if v)
    k2 = {"n": sum(tage_w), "vor": sum(tage_v), "tage": tage_w, "vorwoche": vorwoche, "label": t("kachel.woche"),
          "stellen": [fmt.wochentag_datum(d) for d in woche], "stellen_v": [fmt.wochentag_datum(d - timedelta(days=7)) for d in woche], "xlabels": [fmt.wochentag(d) for d in woche],
          **vergleich(t("kachel.vorwoche"), sum(tage_w), sum(tage_v)),
          "zeile2": t("kachel.an_tagen", an_w, n_tage, beruehrt), "paare": (an_w, n_tage, beruehrt),
          "zeile3": html.escape(t("kachel.nachladen")) if offen_w or offen_v else zeilen_text(plus_w, minus_w, als_html=True),
          "zeile3_title": "" if offen_w or offen_v else
              t("kachel.zeilen_vor", zeilen_text(plus_v, minus_v)) + "\n" + t("kachel.aus_title", t("kachel.aus_woche"), fmt.zahl(aus_w), dateien_w, fmt.zahl(db.EINZEL_MAX), ohne_w + ohne_v),
          "legende": t("kachel.leg_woche", sw(balken=True), sw(schnitt=True, ref=True)), "was": t("kachel.was_woche")}

    # Monat: vom 1. bis jetzt kumuliert auf der x-Achse des ganzen Monats, gestrichelt der ganze Vormonat kumuliert. Der Vergleichswert
    # zählt den Vormonat vom 1. bis zum selben Tag im selben Abstand ab dessen Tagesbeginn; ist der Vormonat kürzer, gilt sein letzter Tag.
    m0 = heute.replace(day=1)
    m_tage = calendar.monthrange(heute.year, heute.month)[1]
    pm_ende = m0 - timedelta(days=1)
    pm0 = pm_ende.replace(day=1)
    tag_v = pm0 + timedelta(days=min(heute.day, pm_ende.day) - 1)
    tage_m, ber_m = zeitfensterwerte(c, tag0(m0), jetzt_u, heute.day)
    vor_m = sum(zeitfensterwerte(c, tag0(pm0), tag0(tag_v) + (jetzt_u - tag0(heute)), tag_v.day)[0])
    tage_pm, _ = fensterwerte(c, pm0, pm_ende, jetzt)
    lage_x = lambda i, m: .5 if m == 1 else i / (m - 1)
    # ponytail: ein Vormonat mit mehr Tagen als der laufende wird auf dessen Länge gekürzt, sein letzter Tag fehlt dann in der Linie.
    vgl_m = kumuliert(tage_pm)[:m_tage]
    an_m = sum(1 for v in tage_m if v)
    monat = {"n": sum(tage_m), "vor": vor_m, "voll": [(lage_x(i, m_tage), v) for i, v in enumerate(kumuliert(tage_m))],
             "vergleich": [(lage_x(i, m_tage), v) for i, v in enumerate(vgl_m)], "label": t("kachel.monat"),
             "stellen": [fmt.datum_jahr(m0 + timedelta(days=i)) for i in range(heute.day)],
             "stellen_v": [fmt.datum_jahr(pm0 + timedelta(days=i)) for i in range(len(vgl_m))],
             **vergleich(t("kachel.vormonat"), sum(tage_m), vor_m),
             "zeile2": t("kachel.an_tagen", an_m, heute.day, ber_m), "paare": (an_m, heute.day, ber_m),
             "x": (fmt.datum(m0), fmt.datum(m0 + timedelta(days=m_tage - 1))), "laeuft": True,
             "legende": t("kachel.leg_monat", sw(), sw(True), fmt.monat(m0), fmt.monat(pm0)), "was": t("kachel.was_monat", fmt.monat(m0))}

    return {"k2": [("woche", k2)], "k3": [("monat", monat)]}


def schnitt(werte):
    return sum(werte) / len(werte) if werte else 0


def server_lesen():
    """server.json vom Host-Timer: (Daten, Fehler). Fehlt die Datei, ist der Serverstand noch nicht eingerichtet: (None, None). Ist sie da,
    aber unlesbar, kein JSON oder kein Objekt, ist das eine ausgefallene Messung mit Grund: (None, Grund) (Codex-Audit 11.10.2026, Befund 6).
    Vorher wurde beides zu «Noch kein Serverstand», und die Deploy-Befunde der Dienste verschwanden still. Eine Liste oder Zahl als Wurzel
    brach den Block mit TypeError (10.10.2026, Audit O2)."""
    try:
        s = json.loads(SERVER.read_text())
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"[:200]
    return (s, None) if isinstance(s, dict) else (None, f"Wurzel ist {type(s).__name__}, kein Objekt")


def server_daten():
    return server_lesen()[0]


def nicht_gemessen(s, schluessel, klasse="muted"):
    """«nicht gemessen», der Grund aus server.json im title (10.10.2026, Audit B2 und S11): tools/server-stand misst jede Grösse für sich und
    führt eine gescheiterte Messung mit Grund unter ungemessen, statt abzubrechen."""
    grund = (s.get("ungemessen") or {}).get(schluessel)
    return f'<span class="{klasse}"{f' title="{html.escape(grund)}"' if grund else ""}>{t("dn.nicht_gemessen")}</span>'


def gb(b):
    return fmt.dezimal(b / 1e9, 1 if b < 1e10 else 0)


def uhr(iso_s, heute):
    d = datetime.fromisoformat(iso_s.replace("Z", "+00:00")).astimezone(TZ)
    return fmt.uhrzeit(d) if d.date() == heute else fmt.zeitpunkt(d, mit_jahr=False)


def firewall_befunde(fw):
    """Die zweite Reihe als Befunde. Eine leere Kette ist ein Befund und kein grüner Zustand: DOCKER-USER
    leer heisst, dass Dockers veröffentlichte Ports an der INPUT-Kette vorbei erreichbar sind. Fehlt die
    Persistenz oder weicht der laufende Stand vom gespeicherten ab, überlebt die Reihe keinen Neustart."""
    if not fw:
        return []
    if fw["art"] == "keine":
        return [t("fw.keine_reihe")]
    # Eine Familie, deren Messung scheiterte (03.10.2026), ist eine Abweichung; vorher fiel sie still weg.
    ab = [t("fw.nicht_gemessen", name) for name in fw.get("nicht_gemessen") or ()]
    # Eine Kette, die es nicht gibt (DOCKER-USER ohne Docker im Netz des Hosts, 11.10.2026), ist nicht leer
    ab += [t("fw.kette_leer", name, kette) for name, k in fw["familien"].items() for kette in KETTEN if kette in k and not k[kette]]
    if fw["art"] == "iptables" and fw["persistent"] is False:   # None heisst nicht gemessen, das ist keine Aussage
        ab.append(t("fw.nicht_persistent"))
    if fw["familien"] and fw["wie_gespeichert"] is False:   # None: kein iptables-persistent, nicht gemessen (11.10.2026)
        ab.append(t("fw.abweichung"))
    return ab


def gedeckt(zustand, quelle):
    """Zieht der Automatismus diese Quelle? True nur, wenn unattended-upgrades aktiv ist und die tatsächlich erlaubten Quellen sie decken
    (quellen_auto aus tools/server-stand), False, wenn eines davon gemessen nicht zutrifft, sonst None (10.10.2026, Audit S12). Vorher galt
    jede Quelle mit «Debian» vorn als gedeckt; trixie-updates deckt das Origins-Pattern des VPS aber nicht (gemessen 10.10.2026)."""
    aktiv, deckt = zustand.get("unattended_upgrades"), (zustand.get("quellen_auto") or {}).get(quelle)
    if aktiv is False or (aktiv and deckt is False):
        return False
    return True if aktiv and deckt else None


def von_hand(zustand):
    """Upgradebare Pakete je Quelle, die der Automatismus nicht nachweislich zieht; ein Handbetrieb ohne Zähler wird nicht geprüft."""
    return {q: n for q, n in (zustand.get("pakete_quellen") or {}).items() if n and not gedeckt(zustand, q)}


def timer_aktiv(tm_):
    """Ist die Timer-Unit selbst aktiv? Seit 03.10.2026 misst tools/server-stand das getrennt vom Service (timer_aktiv); ein gestoppter
    Timer plant nichts mehr, auch wenn sein letzter Lauf sauber war (Codex-Audit). Ältere server.json ohne den Schlüssel gelten als aktiv."""
    return tm_.get("timer_aktiv", "active") == "active"


def dienst_ok(d):
    """Ein Dienst aus server.json, den die Seite auswerten kann: Objekt mit Namen, Container und Timer als Listen von Objekten, ohne "fehler"
    von tools/server-stand (Typprüfung, Codex-Audit 11.10.2026, Befund 8). Ein anderer Eintrag steht als nicht gemessen und betrifft nur sich."""
    return (isinstance(d, dict) and not d.get("fehler") and isinstance(d.get("name"), str)
            and all(isinstance(d.get(k), list) and all(isinstance(x, dict) for x in d[k]) for k in ("container", "timer")))


def dienst_name(d):
    return d["name"] if isinstance(d, dict) and isinstance(d.get("name"), str) else "?"


def server_lage(s):
    """Lagesatz Server: Abweichungen zuerst (Container nicht running, Lauf gescheitert, Timer fehlt,
    Neustart nötig), dann der Rest in Prosa. Liefert (Satz, Abweichungen)."""
    ab, gescheitert = [], False
    alle = s["dienste"]
    ab += [t("sl.ungemessen", dienst_name(d), t("sl.eintrag")) for d in alle if not dienst_ok(d)]
    s = {**s, "dienste": [d for d in alle if dienst_ok(d)]}
    for d in s["dienste"]:
        for c in d["container"]:
            if c["status"] == "ungemessen":
                ab.append(t("sl.ungemessen", d["name"], c["name"]))
            elif c["status"] != "running" or c.get("gesund") == "unhealthy":   # unhealthy ist ein Befund (10.10.2026, Audit S13)
                ab.append(t("sl.container", d["name"], c["name"], c["status"] if c["status"] != "running" else c["gesund"]))
        for tm_ in d["timer"]:
            if tm_.get("status") in ("fehlt", "ungemessen"):
                ab.append(t("sl.timer_fehlt" if tm_["status"] == "fehlt" else "sl.ungemessen", d["name"], tm_["unit"]))
                continue
            if not timer_aktiv(tm_):
                ab.append(t("sl.timer_aus", d["name"], tm_["unit"]))
            if tm_.get("exit") or tm_.get("ergebnis") not in ("success", None):
                ab.append(t("sl.gescheitert", d["name"], tm_["unit"], tm_.get("ergebnis"), tm_.get("exit")))
                gescheitert = True
    ab += firewall_befunde(s.get("firewall"))
    if s["zustand"].get("neustart_noetig"):
        ab.append(t("sl.neustart_noetig"))
    n = len(alle)
    laufen = sum(1 for d in s["dienste"] if all(c["status"] == "running" for c in d["container"]))
    rest = []
    if laufen == n or laufen:
        rest.append(t("sl.laufen", laufen == n, laufen, n, bool(ab)))
    offen = sum(1 for d in s["dienste"] for tm_ in d["timer"] if tm_.get("status") not in ("fehlt", "ungemessen", "activating", "active") and not tm_.get("letzter_utc"))
    if offen:   # nach einem Host-Neustart: systemd kennt keine Zeiten mehr, das ist kein Exit 0
        rest.append(t("sl.offen", offen))
    elif not gescheitert and any(tm_.get("status") not in ("fehlt", "ungemessen") for d in s["dienste"] for tm_ in d["timer"]):
        rest.append(t("sl.sauber"))   # nur über gemessene Läufe
    hand = von_hand(s["zustand"])
    if hand:   # der Automatismus zieht sie nicht, also muss die Zahl sichtbar sein
        rest.append(", ".join(t("sl.hand", q, n) for q, n in sorted(hand.items())) + t("sl.hand_ende"))
    if s["zustand"].get("neustart_noetig") is False:   # nur gemessen; ohne Mechanismus für den Marker steht nichts (Audit S11)
        rest.append(t("sl.kein_neustart"))
    # Kein Grossbuchstabe am Anfang erzwungen: "gitpuls" ist ein Name.
    return " ".join(a + "." for a in ab) + (" " if ab and rest else "") + (", ".join(rest) + "." if rest else ""), ab


def bild_kurz(bild):
    """Kurzname des Bildes; fehlt der Schlüssel oder ist er leer, steht «unbekannt» in Warnfarbe statt eines
    500 (KeyError 'bild' am 11.09.2026, als der Schreiber für einen fehlenden Container keinen Schlüssel lieferte)."""
    if not bild:
        return f'<span class="glut">{t("sv.unbekannt")}</span>'
    return html.escape(bild.split("@")[0].rsplit("/", 1)[-1])


def zeitspanne(sek):
    if sek >= 86400 * 2:
        return t("sv.tage", int(sek // 86400))
    if sek >= 3600 * 2:
        return t("sv.std", int(sek // 3600))
    return t("sv.min", max(1, int(sek // 60)))


# Die Blöcke, die im laufenden Seitenaufbau gebrochen sind, je Anfrage (10.10.2026, Audit S27); /gesund liest sie über seite(fehler=…).
# Vorher eine Liste je Prozess: ein zweiter gleichzeitiger Aufbau leerte sie, und /gesund meldete «ok» für eine Seite mit Fehlerzeile.
_blockfehler = ContextVar("gitpuls_blockfehler")


def block(name, fn, *args):
    """Ein Block für sich: eine Ausnahme ergibt an seiner Stelle eine Zeile in Warnfarbe, der Rest der Seite bleibt,
    Antwort 200; der vollständige Traceback geht ins Container-Log. Anlass: KeyError 'bild' am 11.09.2026 nahm
    die ganze Seite mit, obwohl nur ein Detail einer Dienstzeile betroffen war."""
    try:
        return fn(*args)
    except Exception as e:
        _blockfehler.get([]).append(name)
        print(f"FEHLER Block {name}: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return f'<p class="blockfehler glut" data-block="{html.escape(name)}">{t("blockfehler", html.escape(type(e).__name__))}</p>'


def server_stand_block(s, jetzt):
    erhoben = datetime.fromisoformat(s["erhoben_utc"].replace("Z", "+00:00"))
    alter_min = int((jetzt - erhoben).total_seconds() // 60)
    e = erhoben.astimezone(TZ)
    stand = t("sv.stand", fmt.zeitpunkt(e), alter_min)
    if alter_min * 60 > SERVER_ALT_S:
        stand = f'<span class="glut">{stand}{t("sv.alt", SERVER_ALT_S // 60)}</span>'
    return f'<span class="stand">{stand}</span>'


def boot_von(s):
    b = s["zustand"].get("boot_utc")
    return datetime.fromisoformat(b.replace("Z", "+00:00")).astimezone(TZ) if b else None


def server_lage_block(s, jetzt):
    """Lagesatz und die eine Zahl, die heute zählt: die erste Grösse über ihrer Schwelle, sonst die Laufzeit."""
    satz, _ = server_lage(s)
    m, z = s["maschine"], s["zustand"]
    boot = boot_von(s)
    hand = von_hand(z)
    # Eine nicht gemessene Grösse (null in server.json) steht nie über ihrer Schwelle.
    anteil = lambda belegt, gesamt: z[belegt] / m[gesamt] if z.get(belegt) is not None and m.get(gesamt) else 0
    ram_anteil, platte_anteil = anteil("ram_belegt_bytes", "ram_bytes"), anteil("platte_belegt_bytes", "platte_bytes")
    last = (z.get("load") or [None])[0]
    if ram_anteil > 0.8:
        zahl = (fmt.prozent(ram_anteil * 100), t("sv.ram_voll", gb(z["ram_belegt_bytes"]), gb(m["ram_bytes"])), True)
    elif platte_anteil > 0.8:
        zahl = (fmt.prozent(platte_anteil * 100), t("sv.platte_voll", gb(z["platte_belegt_bytes"]), gb(m["platte_bytes"])), True)
    elif last is not None and m.get("vcores") and last > m["vcores"]:
        zahl = (fmt.dezimal(last, 2), t("sv.last_hoch", m["vcores"]), True)
    elif hand:
        zahl = (str(sum(hand.values())), t("sv.hand", sum(hand.values()), ", ".join(sorted(hand))), False)
    elif boot:
        zahl = (zeitspanne((jetzt - boot).total_seconds()), t("sv.laeuft_seit_boot", fmt.zeitpunkt(boot)), False)
    else:
        zahl = (t("leer_strich"), t("sv.boot_ungemessen"), False)
    zahl_html = f'<div class="n"><b class="{"glut" if zahl[2] else "blau"}">{zahl[0]}</b><span>{zahl[1]}</span></div>'
    return f'<p class="f">{geschuetzt(satz)}</p>{zahl_html}'


def server_dienste_block(s, jetzt):
    """Vier Dienste als Zeilen (Zustandspunkt, Name als Link, kurze Aussage zum letzten Lauf) mit aufklappbarem Detail.
    Ein konfigurierter Container oder Timer, den der Serverstand nicht findet, steht mit dem gesuchten Namen und
    «nicht gefunden, tools/dienste.json prüfen»: die Tabelle ist dann veraltet, nicht der Dienst kaputt."""
    heute = jetzt.astimezone(TZ).date()
    boot = boot_von(s)
    neustart_txt = uhr(iso_utc(boot.astimezone(timezone.utc)), heute) if boot else t("dn.nicht_gemessen")
    PRUEFEN = t("dn.pruefen")
    ng = lambda x: f'<span class="muted" title="{html.escape(x.get("grund") or "")}">{t("dn.nicht_gemessen")}</span>'
    def zeile(d):
        # Nicht gemessen (Werkzeug fehlt, Zugriff verweigert, 10.10.2026) ist weder «fehlt» noch eine Abweichung des Dienstes: grauer Punkt.
        nicht = [x for x in d["container"] + d["timer"] if x.get("status") == "ungemessen"]
        cont_ab = [c for c in d["container"] if c["status"] not in ("running", "ungemessen") or c.get("gesund") == "unhealthy"]
        gescheitert = [t for t in d["timer"] if t.get("status") != "fehlt" and (t.get("exit") or t.get("ergebnis") not in ("success", None))]
        fehlend = [t for t in d["timer"] if t.get("status") == "fehlt"]
        gestoppt = [t for t in d["timer"] if t.get("status") != "fehlt" and not timer_aktiv(t)]
        laufend = [t for t in d["timer"] if t.get("status") in ("activating", "active") and t.get("gestartet_utc")]
        fertig = sorted((t for t in d["timer"] if t.get("letzter_utc") and t not in laufend), key=lambda t: t["letzter_utc"])
        # Zustandspunkt und die eine kurze Aussage zum letzten Lauf; die Langform steht im Detail
        if cont_ab:
            c0 = cont_ab[0]
            was = PRUEFEN if c0["status"] == "fehlt" else html.escape(c0["status"] if c0["status"] != "running" else c0["gesund"])
            punkt, aussage = "glut", f'<span class="glut">{t("dn.container", html.escape(c0["name"]), was)}</span>'
        elif gescheitert:
            tm_ = gescheitert[0]
            punkt, aussage = "glut", f'<span class="glut">{t("dn.gescheitert", html.escape(tm_["unit"]), uhr(tm_["letzter_utc"], heute) if tm_.get("letzter_utc") else "?", html.escape(tm_.get("ergebnis") or "?"), tm_.get("exit"))}</span>'
        elif fehlend:
            punkt, aussage = "glut", f'<span class="glut">{t("dn.timer", html.escape(fehlend[0]["unit"]), PRUEFEN)}</span>'
        elif gestoppt:
            punkt, aussage = "glut", f'<span class="glut">{t("dn.timer", html.escape(gestoppt[0]["unit"]), t("dn.nicht_aktiv"))}</span>'
        elif nicht:
            punkt, aussage = "grau", ng(nicht[0])
        elif laufend:
            punkt, aussage = "mint", t("dn.laeuft", html.escape(laufend[0]["unit"]), uhr(laufend[0]["gestartet_utc"], heute))
        elif fertig:
            tm_ = fertig[-1]
            punkt, aussage = "mint", t("dn.sauber", html.escape(tm_["unit"]), uhr(tm_["letzter_utc"], heute))
        elif not d["timer"] and d["container"]:
            # Ein Dienst ohne Timer (25.09.2026) ist gesund, wenn seine Container laufen: kein Lauf, auf den er warten müsste.
            punkt, aussage = "mint", t("dn.ohne_timer", uhr(d["container"][0]["seit_utc"], heute))
        else:
            punkt, aussage = "grau", t("dn.kein_lauf", neustart_txt)
        # Detail: alles, was von der ersten Ebene hierher gewandert ist. Zweck und Zeilen sind Angaben des Betreibers aus
        # tools/dienste.json, keine Texte der Oberfläche: sie bleiben in ihrer Sprache (translate="no").
        det = [f'<div class="dz"><span class="dk">{t("dn.zweck")}</span><span translate="no">{html.escape(d.get("zweck") or "")}</span></div>']
        for x in d.get("zeilen", []):
            det.append(f'<div class="dz"><span class="dk">{t("dn.dazu")}</span><span translate="no">{html.escape(x)}</span></div>')
        for c in d["container"]:
            if c["status"] == "running":
                txt = t("dn.c_laeuft", html.escape(c["name"]), uhr(c["seit_utc"], heute), bild_kurz(c.get("bild")))
                if c.get("gesund"):
                    txt += t("dn.gesund") if c["gesund"] == "healthy" else f', <span class="glut">{html.escape(c["gesund"])}</span>'
                if c.get("neustarts"):   # Dockers RestartCount: Neustarts durch die Restart-Policy, nicht der Host-Neustart
                    txt += t("dn.neustarts", c["neustarts"])
            elif c["status"] == "fehlt":
                txt = f'<span class="glut">{html.escape(c["name"])} {PRUEFEN}</span>'
            elif c["status"] == "ungemessen":
                txt = f'{html.escape(c["name"])} {ng(c)}'
            else:
                txt = f'<span class="glut">{html.escape(c["name"])} {html.escape(c["status"])}</span>{t("dn.bild", bild_kurz(c.get("bild")))}'
            det.append(f'<div class="dz"><span class="dk">{t("dn.container_k")}</span><span>{txt}</span></div>')
        for tm_ in d["timer"]:
            if tm_.get("status") == "fehlt":
                det.append(f'<div class="dz"><span class="dk">{t("dn.lauf_k")}</span><span class="glut">{t("dn.timer", html.escape(tm_["unit"]), PRUEFEN)}</span></div>')
                continue
            if tm_.get("status") == "ungemessen":
                det.append(f'<div class="dz"><span class="dk">{t("dn.lauf_k")}</span><span>{html.escape(tm_["unit"])} {ng(tm_)}</span></div>')
                continue
            ok = not tm_.get("exit") and tm_.get("ergebnis") in ("success", None)
            if tm_ in laufend:
                letzter = t("dn.l_laeuft", uhr(tm_["gestartet_utc"], heute))
            elif not tm_.get("letzter_utc"):
                # Nach einem Neustart kennt systemd keine Zeiten mehr (Result success, Exit 0 ohne Zeitstempel,
                # gemessen 08.09.2026 nach dem Neustart 05:04): das ist kein sauberer Lauf.
                letzter = t("dn.l_nicht", neustart_txt)
            elif ok:
                letzter = t("dn.l_sauber", uhr(tm_["letzter_utc"], heute))
            else:
                letzter = f'<span class="glut">{t("dn.l_gescheitert", uhr(tm_["letzter_utc"], heute), html.escape(tm_.get("ergebnis") or "?"), tm_.get("exit"))}</span>'
            naechster = uhr(tm_["naechster_utc"], heute) if tm_.get("naechster_utc") else (t("dn.nach_lauf") if tm_ in laufend else t("dn.kein_termin"))
            if tm_ in gestoppt:
                naechster = f'<span class="glut">{t("dn.timer", html.escape(tm_["unit"]), t("dn.nicht_aktiv"))}</span>'
            det.append(f'<div class="dz"><span class="dk">{t("dn.lauf_k")}</span><span>{t("dn.naechster", html.escape(tm_["unit"]), letzter, naechster)}</span></div>')
        dp = d.get("deploy")
        alter = deploy_alter_s(dp, jetzt)
        if not deploy_stand(dp):
            fehler_ = dp.get("fehler") if isinstance(dp, dict) else None
            dep = t("dn.nicht_gemessen") + (f' ({html.escape(str(fehler_))})' if fehler_ else "")
        elif alter is None and not dp.get("fehler"):   # ein ungültiger Zeitpunkt ist nicht gemessen, nie frisch (Befund 7)
            dep = f'{t("dn.nicht_gemessen")} ({t("dn.zeit_ungueltig")})'
        else:
            # Der Default-Branch kommt seit 10.10.2026 aus deploy.json (Audit S17); ein Eintrag von davor nennt ihn allgemein.
            zweig = html.escape(dp.get("branch") or t("default_branch"))
            if dp["hinter"]:
                dep = t("dn.hinter", dp["tag"], dp["hinter"], dp["origin_main"], dp["bildrelevant"], zweig)
            elif deploy_gleich(dp):
                dep = t("dn.gleich", dp["tag"], zweig)
            else:   # 0 hinter, aber ein anderer Commit (Befund 13): ein Stand vor main oder neben einem zurückgesetzten main
                dep = f'<span class="glut">{t("dn.abweichend", dp["tag"], dp["origin_main"], zweig)}</span>'
            if dp.get("fehler"):   # Messfehler seit 03.10.2026: der letzte gelungene Stand bleibt, als solcher benannt
                dep = f'<span class="glut">{t("dn.alter_stand", html.escape(str(dp["fehler"])), dep)}</span>'
            elif alter > DEPLOY_ALT_S:
                dep = f'<span class="glut">{t("dn.veraltet", zeitpunkt(dp["gemessen"]))}: {dep}</span>'
        det.append(f'<div class="dz"><span class="dk">{t("dn.stand_k")}</span><span>{dep}</span></div>')
        return (f'<details class="dienst"><summary><i class="punkt {punkt}" aria-hidden="true"></i><a href="{html.escape(d.get("link") or "")}" translate="no">{html.escape(d["name"])}</a>'
                      f'<span class="aussage">{aussage}</span></summary><div class="ddetail">{"".join(det)}</div></details>')

    def ungueltig(d, grund):
        """Ein Dienst, den die Seite nicht auswerten kann: grauer Punkt, «nicht gemessen» mit Grund, die übrigen Zeilen bleiben (Befund 8)."""
        return (f'<details class="dienst"><summary><i class="punkt grau" aria-hidden="true"></i><span translate="no">{html.escape(dienst_name(d))}</span>'
                f'<span class="aussage">{ng({"grund": grund})}</span></summary><div class="ddetail"><div class="dz"><span class="dk">{t("dn.stand_k")}</span>'
                f'<span>{t("dn.nicht_gemessen")} ({html.escape(grund)})</span></div></div></details>')

    zeilen = []
    for d in s["dienste"]:
        if not dienst_ok(d):
            zeilen.append(ungueltig(d, str(d.get("fehler")) if isinstance(d, dict) and d.get("fehler") else t("dn.eintrag_ungueltig", "server.json")))
            continue
        try:
            zeilen.append(zeile(d))
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            print(f"FEHLER Dienst {dienst_name(d)}: {type(e).__name__}: {e}", flush=True)
            zeilen.append(ungueltig(d, t("dn.eintrag_ungueltig", type(e).__name__)))
    if not zeilen and "dienste" in (s.get("ungemessen") or {}):   # keine Diensttabelle auf dem Host (10.10.2026)
        return f'<p class="sub glut">{t("sv.dienste_ungemessen", html.escape(s["ungemessen"]["dienste"]))}</p>'
    return "".join(zeilen)


def mz(k, v, klasse="", title=""):
    titel = f' title="{html.escape(title)}"' if title else ""
    return f'<div class="mz{klasse}"{titel}><span class="mk">{k}</span><span>{v}</span></div>'


def fuellbalken(anteil):
    """Dünner Balken direkt hinter dem Wert, der den Anteil zeigt: Spur in Rahmenfarbe, Füllung eine Stufe heller
    beziehungsweise dunkler, fester Massstab 0 bis 100. Einfarbig und ohne Farbwechsel nach Höhe — eine Füllung, die
    ab einer Schwelle umschlägt, wäre eine Bewertung, und Rot gehört auf dieser Seite der Warnfarbe (Entscheid
    03.09.2026: kein Rot, kein Grün). Die Spur ist für beide Zeilen gleich breit, sonst liessen sich die zwei
    Anteile nicht nebeneinander lesen. Der Balken ist Schmuck zur Zahl und deshalb aria-hidden."""
    return f'<span class="fbalken" aria-hidden="true"><i style="width:{min(100.0, max(0.0, anteil)):.1f}%"></i></span>'


def fuss_maschine(s):
    """Arbeitsspeicher und Platte stehen direkt unter der Lastzeile: Prozentwert, Bezugsgrösse und dahinter der
    Füllbalken. Der 24-Stunden-Verlauf davor ist am 20.09.2026 zurückgebaut — zwei Werte, die sich über einen Tag
    kaum bewegen, beantworten mit einem Verlauf die falsche Frage; gefragt ist, wie voll es JETZT ist."""
    m, z, boot = s["maschine"], s["zustand"], boot_von(s)
    # Die Last heisst Last (10.10.2026, Audit S26): das Load-Mittel zählt Prozesse, die rechnen oder auf Ein- und Ausgabe warten, und ist
    # kein Anteil an der Rechenleistung. Vom 25.09. bis 10.10.2026 stand es als Prozent der Kerne mit Füllbalken da und hiess «Auslastung».
    # Der Wert über 5 Minuten mit der Kernzahl als Bezug, die drei Rohwerte im title.
    def zeile(belegt, gesamt, schluessel):
        if z.get(belegt) is None or not m.get(gesamt):
            return nicht_gemessen(s, schluessel)
        anteil = z[belegt] * 100 / m[gesamt]
        return f'{t("mz.anteil", fmt.prozent(anteil), gb(z[belegt]), gb(m[gesamt]))}{fuellbalken(anteil)}'
    last = z.get("load")
    # Die Produktangabe ist eine Einstellung des Betreibers (GITPULS_PRODUKT, Vorgabe leer, Audit S15); leer entfällt die Zeile.
    return ((mz(t("mz.produkt"), f'<span translate="no">{html.escape(m["produkt"])}</span>') if m.get("produkt") else "") + mz(t("mz.host"), html.escape(m["hostname"]))
            + (mz(t("mz.last"), t("mz.last_wert", fmt.dezimal(last[1], 2), m["vcores"]), "", t("mz.last_title", *(fmt.dezimal(x, 2) for x in last)))
               if last and m.get("vcores") else mz(t("mz.last"), nicht_gemessen(s, "load")))
            + mz(t("mz.ram"), zeile("ram_belegt_bytes", "ram_bytes", "ram"), " anteil")
            + mz(t("mz.platte"), zeile("platte_belegt_bytes", "platte_bytes", "platte"), " anteil")
            + mz(t("mz.kerne"), str(m["vcores"]) if m.get("vcores") else nicht_gemessen(s, "vcores"))
            + mz(t("mz.gestartet"), t("uhrzeit", fmt.zeitpunkt(boot)) if boot else nicht_gemessen(s, "boot")))


def fuss_installiert(s):
    m, i = s["maschine"], s["installiert"]
    # Fehlt ein Werkzeug oder scheitert sein Aufruf, steht «nicht gemessen» mit Grund (Audit B2); containerd kommt aus docker version.
    wert = lambda d, k, ng: html.escape(str(d[k])) if d.get(k) else nicht_gemessen(s, ng)
    ports = i.get("serve_ports")
    return (mz("Docker", wert(i, "docker", "docker")) + mz("Compose", wert(i, "compose", "compose")) + mz("containerd", wert(i, "containerd", "docker"))
            + mz("Tailscale", wert(i, "tailscale", "tailscale"))
            + mz("serve", (", ".join(str(p) for p in ports) or t("leer_strich")) if ports is not None else nicht_gemessen(s, "serve"))
            + mz(t("mz.system"), wert(m, "os", "os")) + mz(t("mz.kernel"), html.escape(m["kernel"])))


def fuss_firewall(s):
    fw = s.get("firewall")
    if not fw:
        return mz(t("mz.reihe"), f'<span class="muted">{t("mz.nicht_gemessen")}</span>')
    if fw["art"] == "keine":
        return mz(t("mz.reihe"), f'<span class="glut">{t("mz.keine_reihe")}</span>')
    ufw = (html.escape(fw["ufw"]) if fw["ufw"] else nicht_gemessen(s, "firewall.ufw") if "firewall.ufw" in (s.get("ungemessen") or {})
           else t("mz.ufw_fehlt"))
    feuer = mz(t("mz.reihe"), t("mz.ufw", fw["art"], ufw))
    for name in fw.get("nicht_gemessen") or ():
        feuer += mz(name, nicht_gemessen(s, f"firewall.{name}", "glut"))
    for name, k in fw["familien"].items():
        feuer += mz(name, ", ".join(f'<span class="glut">{t("mz.kette_leer", kette)}</span>' if not k[kette] else f'{kette} {k[kette]}' for kette in KETTEN if kette in k))
    if fw["art"] == "nftables":
        feuer += mz("nftables", nicht_gemessen(s, "firewall.nftables"))
    feuer += mz(t("mz.persistent"), t("mz.persistent_ja") if fw["persistent"] else nicht_gemessen(s, "firewall.persistent") if fw["persistent"] is None
                else f'<span class="glut">{t("mz.persistent_nein")}</span>')
    if fw["seit_neustart"]:
        g = datetime.fromisoformat(fw["gespeichert_utc"].replace("Z", "+00:00")).astimezone(TZ)
        feuer += mz(t("mz.nach_neustart"), t("mz.wieder_da", fmt.zeitpunkt(g, mit_jahr=False)))
    elif fw["wie_gespeichert"]:
        feuer += mz(t("mz.nach_neustart"), t("mz.gespeichert"))
    elif fw["wie_gespeichert"] is None:
        feuer += mz(t("mz.nach_neustart"), nicht_gemessen(s, "firewall.gespeichert"))
    else:
        # Gekürzt von "laufender Stand weicht vom gespeicherten ab": mit 43 Zeichen war das der
        # längste Wert der Seite und der einzige, der in der Fusszeile noch umbrach.
        feuer += mz(t("mz.nach_neustart"), f'<span class="glut">{t("mz.weicht_ab")}</span>')
    if fw.get("nicht_verglichen"):   # nat und raw schreibt Docker je Container neu, v1 vergleicht sie nicht (11.10.2026)
        feuer += mz(html.escape(", ".join(fw["nicht_verglichen"])), f'<span class="muted">{t("mz.nicht_verglichen")}</span>', title=t("mz.nicht_verglichen_title"))
    return feuer


def fuss_updates(s):
    z = s["zustand"]
    if z.get("pakete_upgradebar") is None:
        upd = mz(t("mz.pakete"), nicht_gemessen(s, "pakete"))
    else:
        upd = mz(t("mz.pakete"), t("mz.upgradebar", z["pakete_upgradebar"]) if z["pakete_upgradebar"] else t("mz.keine_offen"))
    for q, n in sorted((z.get("pakete_quellen") or {}).items()):
        upd += mz(html.escape(q), t("mz.quelle", n, gedeckt(z, q)))
    neustart = z.get("neustart_noetig")
    return upd + (mz(t("mz.neustart"), f'<span class="glut">{t("mz.noetig")}</span>' if neustart else t("mz.nicht_noetig") if neustart is False
                     else nicht_gemessen(s, "neustart"))
                  + mz(t("mz.sicherheit"), t("mz.automatisch", z["unattended_upgrades"]) if z.get("unattended_upgrades") is not None
                       else nicht_gemessen(s, "automatik")))


def server_html(s, jetzt, fehler=None):
    """Die Serverzone: Alterszeile, dann zwei Kacheln nebeneinander (schmal: untereinander): links Lagesatz und
    die eine Zahl, die heute zählt, rechts die Dienste als Zeilen mit aufklappbarem Detail; darunter die Fusszeile
    (Maschine, Installiert, Firewall, Updates). Der Auslastungsverlauf steht seit dem 20.09.2026 als zwei Zeilen im
    Block Maschine, nicht mehr als eigene Kachel (fuss_maschine).
    Jeder Teil ist ein eigener Block (block()); wer einen neuen baut, hängt ihn in dieselbe Isolation. Warnfarbe
    (glut) nur für Abweichungen, Mint für «läuft, letzter Lauf sauber». Drei Zustände: Datei fehlt, älter als
    15 Minuten, frisch."""
    if not s and fehler:
        return (f'<section class="zone server"><div class="zonenkopf"><h2>{t("sv.titel")}</h2></div><p class="f glut">{t("sv.unlesbar")}</p>'
                f'<p class="sub">{html.escape(t("sv.unlesbar_sub", fehler))}</p></section>')
    if not s:
        return (f'<section class="zone server"><div class="zonenkopf"><h2>{t("sv.titel")}</h2></div><p class="f">{t("sv.leer")}</p>'
                f'<p class="sub">{t("sv.leer_sub")}</p></section>')
    return f"""<section class="zone server">
<div class="zonenkopf"><h2>{t("sv.titel")}</h2>{block("Serverstand", server_stand_block, s, jetzt)}</div>
<div class="zwei">
<div class="tile lage">{block("Server-Lage", server_lage_block, s, jetzt)}</div>
<div class="tile dienste">{block("Dienste", server_dienste_block, s, jetzt)}</div>
</div>
<div class="fuss"><div class="meta"><b>{t("sv.maschine")}</b>{block("Maschine", fuss_maschine, s)}</div>
<div class="meta"><b>{t("sv.installiert")}</b>{block("Installiert", fuss_installiert, s)}</div>
<div class="meta"><b>{t("sv.firewall")}</b>{block("Firewall", fuss_firewall, s)}</div>
<div class="meta"><b>{t("sv.updates")}</b>{block("Updates", fuss_updates, s)}</div></div>
</section>"""


def lage_block(c, heute, jetzt):
    """Satz über die volle Breite, darunter eine Reihe auf dem Raster der drei Kacheln (25.09.2026): Ring, Handlungsbedarf, und in der
    dritten Spalte Serie und Repos als zwei gleich grosse Zahlen untereinander (seit 26.09.2026, vorher nebeneinander mit einer
    Lücke zur zweiten Spalte, die zufällig wirkte), linksbündig auf der Kante der dritten Kachel. Die Wochenwerte je Repo kommen aus wochenreihen wie in der Projektzeile, Summe und Repozahl des Rings aus
    zeitfensterwerte wie in der Wochenkachel, beide mit Montag 00:00 bis jetzt."""
    karten = projekte(c, heute, jetzt)
    _, reihen = wochenreihen(c, heute, jetzt)
    for k in karten:
        k["woche"] = reihen.get(k["repo"], [0])[-1]
    montag = heute - timedelta(days=heute.weekday())
    tage_w, repos_w = zeitfensterwerte(c, tag0(montag), jetzt.astimezone(timezone.utc), heute.weekday() + 1)
    ser = serien(tageszaehlung(c, jetzt), heute)
    satz, zahlen, rang, handlungen = befund(ser, karten, sum(tage_w))
    # Lagesatz und Ring folgen dem Umschalter der Kachel 2 (30.09.2026): beide Fassungen stehen im HTML, die letzten 7 Tage vorn. Die
    # Werte je Repo aus tagesreihen, Summe und Repozahl aus zeitfensterwerte wie die 7-Tage-Kachel.
    start7, jetzt_u = heute - timedelta(days=6), jetzt.astimezone(timezone.utc)
    tage7, repos7 = zeitfensterwerte(c, tag0(start7), jetzt_u, 7)
    je7 = {r: sum(v) for r, v in tagesreihen(c, heute, jetzt, 7).items()}
    satz7, _, rang7, _ = befund(ser, [dict(k, woche=je7.get(k["repo"], 0)) for k in karten], sum(tage7), sieben=True)
    stapel = lambda a, b: (f'<div class="stapel"><div class="fenster" data-gruppe="k2" data-fenster="7">{a}</div>'
                           f'<div class="fenster" data-gruppe="k2" data-fenster="woche" hidden>{b}</div></div>')
    serie = "".join(f'<div class="n"><b>{fmt.zahl(z)}</b><span>{html.escape(l)}</span></div>' for z, l in zahlen)
    # Jede Spalte ausser der Serie ein eigener Block (Isolation wie seit db9ab6c). Ring, Legende und Prozente rechnen über alle Commits
    # der Woche (repos_w zählt deren Repos für «N weitere»); die Repozahl in Spalte 3 kommt aus derselben Menge wie die Projektliste
    # (projekte(), nach der Karenz), aktiv sind die Karten mit Commits in der Woche wie in der Gruppe «Diese Woche aktiv».
    return (stapel(f'<p class="f">{geschuetzt(satz7)}</p>', f'<p class="f">{geschuetzt(satz)}</p>')
            + f'<div class="nums">{stapel(block("Ring 7 Tage", ring_html, rang7, sum(tage7), repos7, start7, True), block("Wochenring", ring_html, rang, sum(tage_w), repos_w, montag))}'
            f'{block("Befunde", befunde_html, handlungen, karten, token_warnung(jetzt.astimezone(timezone.utc)))}<div class="npaar">{serie}{block("Repozahl", repos_html, karten)}</div></div>')


def repos_html(karten):
    """Kopfspalte 3 unter der Serie (25.09.2026, untereinander seit 26.09.2026): die Gesamtzahl der Repos der Projektliste, darunter wie viele davon diese Woche aktiv sind, beides aus
    derselben Kartenmenge wie die Liste und ihre Gruppen (nach der Karenz)."""
    aktiv = sum(1 for k in karten if k["woche"])
    return f'<div class="n repos-spalte"><b>{fmt.zahl(len(karten))}</b><span>{html.escape(t("kopf.repos", len(karten), aktiv))}</span></div>'


RING_STUFEN = ("s1", "s2", "s3")   # volle Akzentfarbe, zwei Drittel, ein Drittel Deckkraft (style.css); der Rest gedämpft


def ring_html(rang, summe, repos_n, montag, sieben=False):
    """Die Kalenderwoche nach Repo als Kreisring (Entscheid 25.09.2026, ersetzt die Rangliste mit Anteilbalken): die drei
    stärksten Repos und ein viertes Segment «N weitere» für den Rest der Wochensumme. Einfarbig, nur über die Helligkeit
    abgestuft, daneben die Legende mit Zahl und Anteil (Name einzeilig abgeschnitten, voll im title), darunter gedämpft «diese Woche
    nach Repo»; die Repozahl steht in Kopfspalte 3 neben der Serie (repos_html). Seit dem Feinschliff
    desselben Tages ohne Überschrift und mit leerer Ringmitte: die Spalte ist gebaut wie Serie und Zweige (.n), oben das
    Sichtbare, darunter die eine Zeile. Die Segmente laufen über pathLength 100, ihre Länge IST der Anteil. Ohne Commit in
    der Woche ein gedämpfter Satz statt eines leeren Rings. `montag` bleibt für die Beschriftung des Segments im aria-label."""
    if not summe:
        return f'<div class="n ring-spalte"><span>{t("lage.leer7" if sieben else "lage.woche_leer")}</span></div>'
    teile = [(n, w, a, RING_STUFEN[i]) for i, (n, w, a) in enumerate(rang)]
    rest = summe - sum(w for _, w, _ in rang)
    if rest:
        weitere = max(1, repos_n - len(rang))
        teile.append((t("ring.weitere", weitere), rest, rest * 100 / summe, "srest"))
    segmente, ab = [], 0.0
    for _, _, a, k in teile:
        segmente.append(f'<circle class="rseg {k}" cx="21" cy="21" r="15.9155" pathLength="100" '
                        f'stroke-dasharray="{a:.3f} {100 - a:.3f}" stroke-dashoffset="{-ab:.3f}" transform="rotate(-90 21 21)"/>')
        ab += a
    teile_aria = ", ".join(f'{n} {fmt.prozent(a)}' for n, _, a, _ in teile)
    # 112 px seit 26.09.2026 (vorher 84, ein Drittel grösser); style.css .ring trägt dasselbe Mass.
    bild = (f'<svg class="ring" viewBox="0 0 42 42" width="112" height="112" role="img" aria-label="{html.escape(t("ring.aria7" if sieben else "ring.aria", montag, teile_aria))}">'
            f'{"".join(segmente)}</svg>')
    legende = "".join(f'<li><i class="sw {k}" aria-hidden="true"></i><span class="rname" title="{html.escape(n)}">{html.escape(n)}</span>'
                      f'<span class="rzahl">{fmt.zahl(w)}</span><span class="ranteil">{fmt.prozent(a)}</span></li>' for n, w, a, k in teile)
    return (f'<div class="n ring-spalte"><div class="ringbox">{bild}<ul class="rlegende">{legende}</ul></div>'
            f'<span>{t("ring.unter7" if sieben else "ring.unter")}</span></div>')


# Reihenfolge in Kopfspalte und Block. «abruf» (26.09.2026) zuerst: ein nicht abrufbares Repo macht alle übrigen Angaben zu ihm unsicher.
# «messung» (03.10.2026, Codex-Audit) direkt danach: ein Stand, der nicht gemessen werden konnte, ist ein Befund und nie «Kein Handlungsbedarf».
BEFUND_ARTEN = (("abruf", "bf.abruf"), ("messung", "bf.messung"), ("warn", "bf.warn"), ("ci", "bf.ci"), ("server", "bf.server"), ("zweige", "bf.zweige"), ("dependabot", "bf.dependabot"))
# Hat ein Repo einen Befund dieser Art? Eine Stelle für Kopfzahl, Kopfzeilen und Block (02.10.2026).
HAT_ART = {"abruf": lambda k: k.get("abruf"), "messung": lambda k: k.get("messung"), "warn": lambda k: k.get("warn"), "ci": lambda k: k.get("ci"), "server": lambda k: k.get("server_befund"),
           "zweige": lambda k: k.get("befunde"), "dependabot": lambda k: k.get("dependabot_zweige")}
DEPENDABOT_PRAEFIX = "dependabot/"
HB_SPALTE = 2   # so viele Einträge stehen untereinander in einer Spalte, mehr fliessen über die volle Breite (09.10.2026)
HANDLUNG_FEST = ("warn", "ci", "server")   # Arten, die im Block immer stehen, ohne Befund in der Zeile «0 …» (04./09.10.2026)
# Zeichen für ?handlung=: je aufgeklapptem Warnungseintrag «warn:<owner>/<repo>», durch Komma getrennt. Seit 04.10.2026 stehen alle
# Einträge ohne Grenze da; die alten Zeichen «zweige-alle» und «zweige» werden übergangen.
WARN_ZEICHEN = re.compile(r"warn:[\w.-]+/[\w.-]+")


MESSGROESSE = {"ci": "mg.ci", "sicherheit": "mg.sicherheit", "server": "mg.server"}   # Texte der Messlücken (Art «messung»)


def kein_recht(fehler):
    """Ein Prüffehler aus fehlendem Leserecht: 403 (ohne «disabled», das ist eine Messung) oder der umgestellte Altzustand."""
    return bool(fehler) and (fehler.startswith("403") or fehler == "kein_recht")


def handlung_zeichen(wert):
    """Die gültigen Zeichen aus ?handlung=, alles andere fällt weg."""
    return frozenset(x for x in wert.split(",") if WARN_ZEICHEN.fullmatch(x))


def laufzeit(k):
    """Hat das Repo eine Warnung, die nicht ausdrücklich nur die Entwicklung betrifft? Ohne Geltungsbereich zählt sie als Laufzeit."""
    return any(w.get("scope") != "development" for w in k.get("warnliste") or ())


def zielzweig(art, karten):
    """Der Default-Branch, auf den Zweige warten oder hinter dem Dienste laufen (10.10.2026, Audit S17): die Namen der betroffenen Repos,
    ohne Befund die aller Repos, mehrere mit «oder». Vorher stand fest «main», auch bei master oder trunk."""
    namen = {b["branch"] for k in karten for b in k.get("befunde", ())} if art == "zweige" else {k["branch"] for k in karten if k.get("server_befund")}
    namen = namen or {k["branch"] for k in karten}
    return t("oder").join(sorted(namen)) if namen else t("default_branch")


def art_satz(art, schluessel, n, karten):
    """Ein Text einer Art mit Zahl; Zweige und Server nennen dazu ihren Default-Branch, Dienste aus einem Release-Bild die neuere Version."""
    neu = sorted({f'v{k["server"]["neuer"]}' for k in karten if k.get("server_befund") and k["server"].get("neuer")}) if art == "server" else []
    if neu:
        alt = [k for k in karten if k.get("server_befund") and not k["server"].get("neuer")]
        return t(BEIDES[schluessel], n, zielzweig(art, alt), t("oder").join(neu)) if alt else t(NEUER[schluessel], n, t("oder").join(neu))
    return t(schluessel, n, zielzweig(art, karten)) if art in ("zweige", "server") else t(schluessel, n)


def art_text(art, schluessel, n, karten, zahl=True):
    """Die Kurzangabe einer Art, gleich im Kopf und im zugeklappten Block: «5 Repos mit Sicherheitswarnungen, 1 zur Laufzeit». Ohne `zahl`
    fehlt die vordere Zahl (Unterzeile im Kopf, wenn es nur eine Art gibt: die Zahl steht dort gross darüber)."""
    n_lauf = sum(1 for k in karten if k.get("warn") and laufzeit(k)) if art == "warn" else 0
    return (f"{n} " if zahl else "") + art_satz(art, schluessel, n, karten) + (t("bf.warn_laufzeit", n_lauf) if n_lauf else "")


def befund_zahlen(karten):
    """Je Art die Zahl der Befunde: Repos mit Sicherheitswarnungen, Repos mit rotem Actions-Lauf, Zweige vor main (je Repo einer)."""
    return {a: sum(len(k.get("befunde", ())) if a == "zweige" else 1 for k in karten if HAT_ART[a](k)) for a, _ in BEFUND_ARTEN}


def token_lesen():
    """Der Ablauf des Tokens aus token.json: (UTC-Zeitpunkt oder None, Fehler). None ohne Datei oder bei "ablauf": null (Token ohne Ablauf).
    Eine unlesbare Datei, ein Objekt ohne "ablauf" oder ein Ablauf in unbekannter Form ist seit 11.10.2026 ein Fehler mit Grund (Codex-Audit,
    Klasse A): vorher hiess das «kein Ablauf», und die Warnung vor dem Ablauf verschwand still. GitHub schreibt «2026-12-01 00:00:00 UTC» oder
    mit Offset «… +0100»."""
    try:
        j = json.loads(TOKEN.read_text())
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, f"token.json unlesbar, {type(e).__name__}"
    if not isinstance(j, dict) or "ablauf" not in j:
        return None, "token.json ohne Feld ablauf"
    a = j["ablauf"]
    if a is None:
        return None, None
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (UTC|[+-]\d{4})", a.strip()) if isinstance(a, str) else None
    try:
        return datetime.strptime(f"{m[1]} {'+0000' if m[2] == 'UTC' else m[2]}", "%Y-%m-%d %H:%M:%S %z").astimezone(timezone.utc), None
    except (TypeError, ValueError):
        return None, f"token.json: Ablauf {str(a)[:40]!r} in unbekannter Form"


def token_ablauf():
    return token_lesen()[0]


def messluecken():
    """Ausgefallene Messungen ohne Repo (11.10.2026, Codex-Audit Befunde 6 und Klasse A): je (Schlüssel, Titel, Grund). Sie stehen als
    eigene Gruppen im Handlungsbedarf und zählen im Kopf mit, wie der nahe Ablauf des Tokens."""
    aus = []
    if (f := server_lesen()[1]):
        aus.append(("serverstand", t("bf.luecke_serverstand"), f"server.json: {f}"))
    if (f := token_lesen()[1]):
        aus.append(("tokendatei", t("bf.luecke_token"), f))
    return aus


def token_warnung(jetzt):
    """Der Ablauf, wenn er höchstens TOKEN_WARNUNG entfernt oder vorbei ist, sonst None. Vorher meldete der Dienst den Ablauf nicht und
    sammelte danach still nichts mehr; sichtbar war das nur an der Alterszeile (Harte Grenzen)."""
    a = token_ablauf()
    return a if a and a - jetzt <= TOKEN_WARNUNG else None


def token_text(ablauf, jetzt):
    tag = ablauf.astimezone(TZ).date()
    return t("hb.token_ab", tag) if ablauf <= jetzt else t("hb.token_bald", tag, (tag - jetzt.astimezone(TZ).date()).days)


def befunde_html(handlungen, karten=(), token=None):
    """Die dritte Spalte des Kopfs, «Handlungsbedarf», nur die Zusammenfassung: grosse Zahl, darunter je Art mit Befund
    eine Zeile mit Zahl, jede ein Sprunglink auf ihre Spalte im Block Handlungsbedarf (handlung_block). Keine Repo-Namen, Pakete
    oder Zweige: bei 7 Befunden war die Spalte dreimal so hoch wie Ring und Serie, und der Kopf kippte (vom 02. bis 04.10.2026 stand
    dort wieder je Repo eine Zeile, zurückgenommen). Bei 0 «Kein Handlungsbedarf». Konnte eine Art nicht gemessen werden
    (Token ohne Leserecht, Dependabot ausgeschaltet), steht das einmal gedämpft am Schluss. `handlungen` bleibt für die Aufrufer."""
    zahlen = befund_zahlen(karten)
    da = [(zahlen[a], a, s_) for a, s_ in BEFUND_ARTEN if zahlen[a]]
    luecken = messluecken()
    gesamt = sum(n for n, _, _ in da) + bool(token) + len(luecken)   # `token`: der nahe Ablauf des Tokens (token_warnung), ein Punkt vor allen Arten
    # Seit 04.10.2026 wieder wie vor dem 02.10.2026: je Art mit Befund eine Zeile mit Zahl, ein Sprunglink auf ihre Spalte im Block
    # («4 Repos mit Sicherheitswarnungen, 4 zur Laufzeit»), keine Repos. Die Höhe hängt damit nur an der Zahl der Arten, nie der Befunde.
    if not gesamt:
        unter, zeilen = f'<span>{html.escape(t("bf.keiner"))}</span>', ""
    else:
        unter = ""
        zeilen = ('<ul class="barten">' + (f'<li><a href="#handlung-token">1 {html.escape(t("bf.token"))}</a></li>' if token else "")
                  + "".join(f'<li><a href="#handlung-{k}">1 {html.escape(titel)}</a></li>' for k, titel, _ in luecken)
                  + "".join(f'<li><a href="#handlung-{a}">{html.escape(art_text(a, s_, n, karten))}</a></li>' for n, a, s_ in da) + "</ul>")
    hinweise = []
    # Ohne Leserecht (403, seit 03.10.2026 als Fehler der Abfrage geführt) zusätzlich der Grund in einer Zeile; die Repos selbst stehen
    # unter «nicht gemessen».
    if any(kein_recht(k.get("ci_fehler")) for k in karten):
        hinweise.append(t("bf.ci_recht"))
    sicher = [k.get("sicher_zustand") for k in karten if k.get("sicher_zustand")]
    if any(kein_recht(k.get("sicher_fehler")) for k in karten):
        hinweise.append(t("bf.warn_recht"))
    elif sicher and all(z == "aus" for z in sicher):
        hinweise.append(t("bf.warn_aus"))
    zeilen += "".join(f'<p class="bhinweis">{html.escape(h)}</p>' for h in hinweise)
    return f'<div class="n befunde"><b>{gesamt}</b><div class="bunter">{unter}{zeilen}</div></div>'


def handlung_block(c, heute, jetzt, offen=frozenset()):
    """Block «Handlungsbedarf» nach den Kacheln, seit 04.10.2026 immer (HANDLUNG_FEST ohne Befund als eine Zeile «0 …», seit 09.10.2026). Je Art mit
    Befund der Titel wie «1 Zweig wartet auf main» über die volle Breite, darunter alle Einträge ohne Klick, über drei Spalten fliessend
    (unter 820 px untereinander). Jeder Eintrag ist eine Zeile mit Ellipse, der volle Text steht im title. Titelzeile bzw. Stück der
    Nullzeile tragen die id «handlung-<art>», Sprungziel der Kopfspalte 2. `offen` sind die
    Zeichen aus ?handlung=: «warn:<repo>» öffnet einen Warnungseintrag. Die Einzelheiten je Sicherheitswarnung stehen im Aufklappdetail der
    Projektzeile (Stufe, Paket als Link auf die Warnung, Manifest, behobene Version)."""
    karten = projekte(c, heute, jetzt)
    # Laufzeit vor Entwicklung, dann Stufe, dann Alter (älteste offene Warnung zuerst), zuletzt der Name.
    warn = sorted((k for k in karten if k.get("warn")),
                  key=lambda k: (not laufzeit(k), STUFEN_RANG.get(k["warn"]["stufe"], 9), min((w["geoeffnet"] or "9") for w in k["warnliste"]) if k["warnliste"] else "9", k["name"]))

    def eintrag(k, art, zeilen, titel, link=None, zweig="", klasse=""):
        """Ein Listenpunkt (03.10.2026): blauer Punkt wie in der Befundliste des Kopfs (.barten), daneben die Repo-Zeile (fett, Link),
        darunter je Einzelheit eine Unterzeile mit Ellipse; Folgezeilen fluchten auf der Textkante nach dem Punkt. `zeilen` sind Paare
        (HTML, Zusatzklasse), « hbzwei» lässt zwei Zeilen zu. Der volle Text steht im title. Die id (hb_id) ist das Ziel der Befundangaben in Projektzeile und Kopf."""
        kl = f' class="{klasse}"' if klasse else ""
        return (f'<li id="{hb_id(art, k["repo"], zweig)}"{kl} title="{html.escape(titel)}"><a class="hbrepo" href="{html.escape(link or k["url"])}" target="_blank" rel="noopener">'
                f'{html.escape(k["name"])}</a>' + "".join(f'<span class="hbz{zusatz}">{z}</span>' for z, zusatz in zeilen) + "</li>")

    def rest_html(rest):
        return f' <span class="hbrest">{html.escape(rest)}</span>' if rest else ""

    def warnung(w):
        """Eine Warnung im aufgeklappten Eintrag, Inhalt und Texte wie vor 458fe35: Titel (englisch wie bei GitHub), Paket mit Marke
        «Entwicklung», dann Stufe, Geltungsbereich, Ökosystem, Manifest, Versionsbereich, behoben in, geöffnet am, GHSA und CVE, dazu die
        Links auf die Warnung und auf das Advisory in GitHubs Datenbank."""
        entw = f'<span class="hbentw">{html.escape(t("warn.entwicklung"))}</span>' if w["scope"] == "development" else ""
        felder = [("warn.f_stufe", html.escape(t("stufe", w["stufe"]))), ("warn.f_scope", html.escape(t("warn.scope", w["scope"]))),
                  ("warn.f_oeko", html.escape(w["oekosystem"] or "–")), ("warn.f_manifest", f'<span translate="no">{html.escape(w["manifest"] or "–")}</span>'),
                  ("warn.f_bereich", html.escape(w["bereich"] or "–")), ("warn.f_behoben", html.escape(w["behoben"] or t("warn.ohne_fix"))),
                  ("warn.f_geoeffnet", fmt.datum_jahr(datum(w["geoeffnet"])) if w["geoeffnet"] else "–"),
                  ("warn.f_ghsa", html.escape(w["ghsa"] or "–")), ("warn.f_cve", html.escape(w["cve"] or t("warn.keine")))]
        links = gh_link(w["url"], t("warn.link_github")) + (f' · {gh_link(f"https://github.com/advisories/{w["ghsa"]}", t("warn.link_advisory"))}' if w["ghsa"] else "")
        return (f'<div class="hbw"><p class="hbtitel"><span translate="no">{html.escape(w["paket"])}</span>{entw}</p>'
                f'<p class="hbsumm" translate="no">{html.escape(w["summary"] or w["paket"])}</p>'
                + "".join(mz(t(k_), v) for k_, v in felder) + f'<p class="hblinks">{links}</p></div>')

    def warn_eintrag(k):
        """Repo-Zeile, darunter die Pakete und gedämpft Stufe, Zahl und «Laufzeit» oder «nur Entwicklung» (03.10.2026 als Unterzeilen);
        aufklappbar (26.09.2026, zweiter Lauf) mit allen Einzelheiten je Warnung. Standardmässig zu, offen mit «warn:<repo>» in ?handlung=."""
        pakete = ", ".join(dict.fromkeys(w["paket"] for w in k["warnliste"]))   # je Paket einmal, nach Stufe sortiert
        bereich = t("hb.laufzeit") if laufzeit(k) else t("hb.nur_entwicklung")
        rest = t("hb.stufe", t("stufe", k["warn"]["stufe"]), k["warn"]["n"]) + (f", {bereich}" if k["warnliste"] else "")
        zeichen = f'warn:{k["repo"]}'
        return (f'<li class="hbauf" id="{hb_id("warn", k["repo"])}"><details class="hbeintrag" data-zeichen="{html.escape(zeichen)}"{" open" if zeichen in offen else ""}>'
                f'<summary title="{html.escape(f"{k["name"]}: {pakete}; {rest}")}"><span class="hbtext"><a class="hbrepo" href="{html.escape(k["dependabot"])}" target="_blank" rel="noopener">'
                f'{html.escape(k["name"])}</a>' + (f'<span class="hbz" translate="no">{html.escape(pakete)}</span>' if pakete else "")
                + f'<span class="hbz hbrest">{html.escape(rest)}</span></span>'
                f'<span class="chev" aria-hidden="true"></span></summary>'
                f'<div class="hbdetail">{"".join(warnung(w) for w in k["warnliste"])}'
                f'<p class="hblinks">{gh_link(k["dependabot"], t("hb.alle_github"))}</p></div></details></li>')

    def zweig_eintrag(k, b):
        stand = t("zweig.stand", b["ahead"], b["behind"], b["branch"])
        seit = seit_text(b["seit"]) if b["seit"] is not None else ""
        return eintrag(k, "zweige", [(f'<code translate="no">{html.escape(b["zweig"])}</code> {html.escape(stand)}{rest_html(seit)}', "")],
                       t("zweig.voll", b["zweig"], k["name"], stand) + (f", {seit}" if seit else ""), zweig=b["zweig"])

    def update_eintrag(k):
        """Je Update eine Unterzeile (03.10.2026): Paket und Zielversion als Link auf den PR in Textfarbe, gedämpft das Alter; Gruppenupdates
        stehen am Ende (projekte sortiert sie dorthin)."""
        titel = f'{k["name"]}: ' + ", ".join(f'{u["paket"]} {u["version"]}'.strip() for u in k["updates"])
        zeilen = [f'<a href="{html.escape(u["url"])}" target="_blank" rel="noopener">'
                  + (html.escape(u["paket"]) if u["gruppe"] else f'<span translate="no">{html.escape(u["paket"])}</span>')
                  + (f' {html.escape(u["version"])}' if u["version"] else "") + "</a>"
                  + rest_html(seit_text(u["seit"]) if u["seit"] is not None else "") for u in k["updates"]]
        zeilen = [(z, "") for z in zeilen]
        return eintrag(k, "dependabot", zeilen, titel, k["url"] + "/pulls?q=is:pr+is:open+author:app/dependabot")

    def server_eintrag(k):
        """Server hinter main (03.10.2026 als Unterzeilen): gedämpft «läuft <hash> · N hinter main», darunter je nicht ausgerollter Commit
        Hash gedämpft und Betreff, höchstens zwei Zeilen, voller Betreff im title (aus deploy.json, tools/deploy-stand.sh)."""
        d = k["server"]
        # Hash gedämpft in .hzeit (Monospace, Zeitspalte von «Zuletzt»): .hbliste code in --lbl wäre ein Format, das sonst nirgends vorkommt.
        zeilen = [(f'<span class="hbrest">{html.escape(t("hb.laeuft"))} <span class="hzeit" translate="no">{html.escape(d["tag"])}</span> · '
                   f'{html.escape(hinter_text("ps.hinter", d, k["branch"]))}</span>', "")]
        zeilen += [] if d.get("neuer") else [(f'<span class="hzeit" translate="no">{html.escape(z.get("sha", ""))}</span> '
                    f'<span translate="no" title="{html.escape(z.get("betreff", ""))}">{html.escape(z.get("betreff", ""))}</span>', " hbzwei") for z in d.get("commits") or ()]
        return eintrag(k, "server", zeilen, f'{k["name"]}: {hinter_text("kurz.server", d, k["branch"])}')

    def ci_eintrag(k):
        text = t("hb.ci_zeile", k["ci"]["workflow"])
        return eintrag(k, "ci", [(html.escape(text) + rest_html(k["ci"]["alter"]), "")], f'{k["name"]}: {text}, {k["ci"]["alter"]}', k["url"] + "/actions")

    def abruf_eintrag(k):
        """Ein Repo, das GitHub im letzten Lauf abwies: Code und Meldung (Daten von GitHub, nicht übersetzt), gedämpft seit wann."""
        a = k["abruf"]
        seit = seit_text(a["seit"]) if a["seit"] is not None else ""
        return eintrag(k, "abruf", [(f'<span translate="no">{html.escape(a["text"])}</span>{rest_html(seit)}', "")], f'{k["name"]}: {a["text"]}' + (f", {seit}" if seit else ""))

    def messung_eintrag(k):
        """Je nicht gemessenem Stand eine Zeile (03.10.2026): was, der Fehler (Daten von GitHub oder vom Host, nicht übersetzt), gedämpft
        das Alter der letzten gelungenen Messung; höchstens zwei Zeilen (.hbzwei), sonst kürzte die Ellipse gerade das Alter weg. Der
        Stand selbst bleibt in seiner Art stehen."""
        zeilen = [(f'{html.escape(t(MESSGROESSE[w]))}: <span translate="no">{html.escape(f)}</span>'
                   + rest_html(t("hb.letzter_erfolg", alter_text(g, (heute - datum(g)).days, jetzt)) if g else t("hb.nie_gemessen")), " hbzwei") for w, f, g in k["messung"]]
        return eintrag(k, "messung", zeilen, f'{k["name"]}: ' + "; ".join(f"{t(MESSGROESSE[w])}: {f}" for w, f, _ in k["messung"]))

    eintraege = {"abruf": [abruf_eintrag(k) for k in karten if k.get("abruf")], "messung": [messung_eintrag(k) for k in karten if k.get("messung")], "warn": [warn_eintrag(k) for k in warn],
                 "ci": [ci_eintrag(k) for k in karten if k.get("ci")], "server": [server_eintrag(k) for k in karten if k.get("server_befund")],
                 "zweige": [zweig_eintrag(k, b) for k in karten for b in k["befunde"]],
                 "dependabot": [update_eintrag(k) for k in karten if k.get("dependabot_zweige")]}

    # Seit 09.10.2026 dicht gepackt: Arten aus HANDLUNG_FEST ohne Befund stehen zusammen in einer gedämpften Zeile unter dem Titel
    # («0 Repos mit Sicherheitswarnungen · 0 Actions-Läufe fehlgeschlagen · 0 Dienste hinter main»), jedes Stück Sprungziel seiner Art.
    # Die Arten mit Befund teilen sich das Kachelraster (.hraster), je Art eine Gruppe aus Titel und Einträgen in einer Spalte, in der
    # Reihenfolge aus BEFUND_ARTEN (zweiter Umbau 09.10.2026: vorher stand jede Art über die volle Breite, mit einem Eintrag blieben
    # rechts zwei Drittel leer). Eine Art mit mehr als HB_SPALTE Einträgen nimmt die volle Breite, ihre Einträge fliessen über drei Spuren.
    null = [f'<span id="handlung-{a}">{html.escape(art_text(a, s_, 0, karten))}</span>' for a, s_ in BEFUND_ARTEN if a in HANDLUNG_FEST and not eintraege[a]]
    # Der nahe Ablauf des Tokens (10.10.2026) steht als erste Gruppe vor den Arten der Repos, in denselben Klassen.
    token = token_warnung(jetzt.astimezone(timezone.utc))
    arten = (f'<div><p class="hbart" id="handlung-token">1 {html.escape(t("bf.token"))}</p><ul class="hbliste"><li id="hb-token">'
             f'<span class="hbrepo">{html.escape(t("hb.token"))}</span><span class="hbz">{html.escape(token_text(token, jetzt.astimezone(timezone.utc)))}</span></li></ul></div>'
             if token else "")
    # Ausgefallene Messungen ohne Repo (server.json, token.json) danach, je eine Gruppe mit dem Grund
    arten += "".join(f'<div><p class="hbart" id="handlung-{k}">1 {html.escape(titel)}</p><ul class="hbliste"><li id="hb-{k}">'
                     f'<span class="hbrepo">{html.escape(titel)}</span><span class="hbz" translate="no">{html.escape(grund)}</span></li></ul></div>'
                     for k, titel, grund in messluecken())
    arten += "".join(f'<div><p class="hbart" id="handlung-{a}">{html.escape(art_text(a, s_, len(eintraege[a]), karten))}</p>'
                    f'<ul class="hbliste{" hraster" if len(eintraege[a]) > HB_SPALTE else ""}">{"".join(eintraege[a])}</ul></div>'
                    for a, s_ in BEFUND_ARTEN if eintraege[a])
    return (f'<section class="card handlung" id="handlung"><h3 class="titel">{html.escape(t("hb.titel"))}</h3>'
            + (f'<p class="hbrest">{" · ".join(null)}</p>' if null else "") + (f'<div class="hraster">{arten}</div>' if arten else "") + "</section>")


# Platz der Heute-Kachel, serverseitig, damit im Browser nichts nachträglich springt. Die Kachelhöhe bestimmen die zwei
# Kurvenkacheln. Neu gemessen in Chrome 154 am 26.09.2026 bei 1280 px, seit die Kacheln in zwei Gruppen stehen (Zahl, Titel und
# Vergleichszeile, darunter 16 px abgesetzt .kmeta mit zwei Zeilen Mindesthöhe; die Kopfzeile «Heute in N Repos» steht dort als
# Metazeile) und die Codezeile «+N / −M Zeilen» heisst: Kachel 348.95 px (vorher 340.97), von der Oberkante der Liste bis zur
# Innenkante unten 173.58 px (vorher 193.59, davor 210.97); eine Zeile 17.91 px, Zeilenabstand 3.19 px, beide unverändert.
# Die Restzeile «und N weitere Repos» hat dieselbe Höhe und denselben Abstand und zählt mit.
# Untereinander (unter 820 px) gilt die Rechnung nicht, dort stehen alle Repos.
# ponytail: die Werte folgen dem Stylesheet nicht von selbst; wer dort Schrift, Abstände oder die Kurvenhöhe ändert, misst neu.
HEUTE_PLATZ_PX, HEUTE_ZEILE_PX, HEUTE_ABSTAND_PX = 173.58, 17.91, 3.19
HEUTE_MIN, HEUTE_MAX = 2, 8
# Commitliste unter der Aufteilung (25.09.2026): 12 px Abstand, die gedämpfte Zeile «Zuletzt», dann die Einträge im selben Raster
# wie die Aufteilung; die letzte Zeile endet mindestens 4 px über der Innenkante. Gemessen in Chrome am 25.09.2026 bei 1280 px:
# Abstand Aufteilung zu «Zuletzt» 12.00 px, Zeile «Zuletzt» 17.91 px, ein Eintrag 17.91 px, Abstand .2rem = 3.19 px. Mit drei
# Repos passen bei 173.58 px Platz drei Einträge (26.09.2026; bei 193.59 px vier, bei 210.97 px fünf).
HEUTE_LISTE_ABSTAND_PX, HEUTE_LISTE_KOPF_PX, HEUTE_RAND_PX = 12, 17.91, 4
HEUTE_LISTE_SCHMAL = 5   # untereinander ist die Höhe frei, dort höchstens fünf Einträge


def heute_zeilen(n, platz_px=HEUTE_PLATZ_PX):
    """(sichtbare Einträge, Rest) für n Commits: so viele Zeilen, wie in den Platz passen, mindestens zwei, höchstens
    acht; bleiben Commits übrig, nimmt «und N weitere» eine dieser Zeilen, die Untergrenze zwei gilt trotzdem."""
    passt = max(HEUTE_MIN, min(HEUTE_MAX, int((platz_px + HEUTE_ABSTAND_PX) // (HEUTE_ZEILE_PX + HEUTE_ABSTAND_PX))))
    if n <= passt:
        return n, 0
    sichtbar = max(HEUTE_MIN, passt - 1)
    return sichtbar, n - sichtbar


def heute_commit_plaetze(zeilen_oben, platz_px=HEUTE_PLATZ_PX):
    """Wie viele ganze Commit-Einträge nebeneinander unter die Aufteilung passen: der gemessene Platz ab der Liste, abzüglich
    der `zeilen_oben` Zeilen der Aufteilung samt Restzeile, des Abstands, der Zeile «Zuletzt» und des Rands zur Innenkante.
    Null, wenn kein ganzer Eintrag passt; dann entfallen Zeile und Liste."""
    belegt = zeilen_oben * HEUTE_ZEILE_PX + max(zeilen_oben - 1, 0) * HEUTE_ABSTAND_PX
    frei = platz_px - belegt - HEUTE_LISTE_ABSTAND_PX - HEUTE_LISTE_KOPF_PX - HEUTE_ABSTAND_PX - HEUTE_RAND_PX
    return max(0, int((frei + HEUTE_ABSTAND_PX) // (HEUTE_ZEILE_PX + HEUTE_ABSTAND_PX)))


def heute_commits(commits, n_breit):
    """Die jüngsten Commits des angezeigten Tages unter der Aufteilung: Uhrzeit (Monospace, gedämpft), Repo (gedämpft), Betreff
    (Textfarbe, einzeilig mit Ellipse, voller Text im title), jüngste zuerst, ohne Restzeile. Nebeneinander `n_breit` Einträge,
    untereinander höchstens HEUTE_LISTE_SCHMAL; beides steht im HTML, das Stylesheet wählt nach Breite (nur-breit, nur-schmal).
    Passt nebeneinander kein Eintrag, trägt der ganze Block nur-schmal."""
    n = min(len(commits), max(n_breit, HEUTE_LISTE_SCHMAL))
    if not n:
        return ""
    def klasse(i):
        breit, schmal = i < n_breit, i < HEUTE_LISTE_SCHMAL
        return "" if breit and schmal else ' class="nur-breit"' if breit else ' class="nur-schmal"'
    zeilen = "".join(f'<li{klasse(i)}><span class="hzeit">{fmt.uhrzeit(datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(TZ))}</span>'
                     f'<span class="hcrepo">{html.escape(repo.split("/")[-1])}</span>'
                     f'<span class="hcbetreff" title="{html.escape(betreff or "")}" translate="no">{html.escape(betreff or "")}</span></li>'
                     for i, (t, repo, betreff) in enumerate(commits[:n]))
    return f'<div class="heute-zuletzt{" nur-schmal" if not n_breit else ""}"><p>{t("heute.zuletzt")}</p><ul class="hcommits">{zeilen}</ul></div>'


def anker(repo):
    """Die id der Projektzeile eines Repos, Ziel der Links aus der Heute-Kachel; eindeutig wie hb_id (vorher o/a-b und o-a/b gleich)."""
    return "p-" + id_teil(repo)


def heute_liste(aufteilung, zuletzt=None, commits=(), offen=False):
    """Die Aufteilung des angezeigten Tages je Repo (Entscheid 25.09.2026): Name, Zahl, Anteilbalken gegen das grösste Repo
    des Tages (fuellbalken, Füllung im Akzent: der laufende Wert), gedämpft «bis HH:MM» für den letzten Commit des Repos.
    Jede Zeile ist ein Link auf die Projektzeile desselben Repos. Darüber gedämpft die Kopfzeile mit der Repozahl, die sich
    auf den Tag der Liste bezieht: «Heute in 5 Repos» oder, ist heute leer, «Zuletzt gearbeitet, Do 24.09., in 5 Repos».
    Bis zum Nachtrag vom 25.09.2026 stand die Repozahl hinter dem Vergleichswert («gestern 0, in 5 Repos») und las sich
    als Angabe zu gestern. Nebeneinander so viele Zeilen, wie in die Kachel passen (heute_zeilen, Platz ab der Liste
    gemessen, die Kopfzeile liegt im Abstand darüber); untereinander alle Repos ohne Restzeile, dort bindet keine
    Nachbarkachel die Höhe. Beides steht im HTML, das Stylesheet wählt nach Breite. Seit 04.10.2026 ist die Restzeile «und N
    weitere Repos» ein Aufklapper wie die Gruppe «Ohne Commits diese Woche» (details.gruppe, .chev, offen mit ?heute=offen): aufgeklappt
    stehen die übrigen Repos in derselben Zeilenform, zugeklappt bleibt die Kachel unverändert. Die Zeilen bleiben dabei in der oberen
    Liste (nur-schmal), das Stylesheet zeigt sie bei offenem Aufklapper: eine eigene Liste darin wäre ein zweites Raster, gemessen am
    04.10.2026 in Chrome begannen Zahl und Balken dort 30 und 38 px weiter links, die Balken liessen sich nicht mehr vergleichen."""
    if not aufteilung:
        return f'<p class="heute-leer">{t("heute.leer")}</p>'
    sichtbar, rest = heute_zeilen(len(aufteilung))
    oben = aufteilung[0][1]
    zeilen = "".join(f'<li{" class=\"nur-schmal\"" if i >= sichtbar else ""}><a href="#{anker(repo)}" title="{html.escape(repo)}">'
                     f'<span class="hrepo">{html.escape(repo.split("/")[-1])}</span><span class="hzahl">{fmt.zahl(n)}</span>'
                     f'{fuellbalken(n * 100 / oben)}<span class="uhr">{t("heute.bis", fmt.uhrzeit(datetime.fromisoformat(z.replace("Z", "+00:00")).astimezone(TZ)))}</span></a></li>'
                     for i, (repo, n, z) in enumerate(aufteilung))
    # Unter der Aufteilung die jüngsten Commits desselben Tages, so viele, wie nebeneinander noch ganz in die Kachel passen.
    liste = block("Heute-Commits", heute_commits, commits, heute_commit_plaetze(sichtbar + (1 if rest else 0)))
    return (f'<ul class="heute">{zeilen}</ul>'
            + (f'<details class="gruppe heute-rest"{" open" if offen else ""}><summary>{t("heute.rest", rest)}<span class="chev" aria-hidden="true"></span></summary></details>'
               if rest else "") + liste)


def heute_kopf(aufteilung, zuletzt):
    """Die Metazeile der Heute-Kachel (seit 26.09.2026 in .kmeta statt über der Liste): «Heute in 5 Repos» oder, ist heute leer,
    «Zuletzt gearbeitet, Do 24.09., in 5 Repos»."""
    return t("heute.kopf_zuletzt", fmt.wochentag_datum(zuletzt), len(aufteilung)) if zuletzt else t("heute.kopf", len(aufteilung))


def tage_paare(an, tage, repos):
    """Metazeile der Wochen- und der 30-Tage-Kachel (26.09.2026) als zwei kurze Paare, «3/6 aktive Tage · 9 Repos»: Wert in Textfarbe
    (.kwert), Beschriftung gedämpft; der volle Satz «an 3 von 6 Tagen, in 9 Repos» steht als title daneben (meta_zeile)."""
    wert = lambda x: f'<span class="kwert">{x}</span>'
    return (f'{wert(f"{fmt.zahl(an)}/{fmt.zahl(tage)}")} {html.escape(t("kachel.aktive_tage"))} · '
            f'{wert(fmt.zahl(repos))} {html.escape(t("kachel.repos", repos))}')


def kachel_block(k, nav="", rahmen=True):
    """Metazeilen je Kachel (26.09.2026): Heute die Kopfzeile der Aufteilung («Heute in N Repos»), Woche Tage und Repos plus Codezeilen,
    30 Tage Tage und Repos. Seit 30.09.2026 je Fenster der Umschalter: 7 Tage (Balken mit gleitendem Durchschnitt), Woche (sieben feste
    Plätze mit der Vorwoche je Wochentag), 30 Tage und Monat (kumulierte Linien)."""
    if "aufteilung" in k:
        meta = meta_zeile(html.escape(heute_kopf(k["aufteilung"], k["zuletzt"])), " heute-kopf") if k["aufteilung"] else ""
        return kpi_html(k["n"], k["label"], k["zeile1_html"], meta, block("Heute-Aufteilung", heute_liste, k["aufteilung"], k["zuletzt"], k.get("commits", ()), k.get("offen", False)), "")
    meta = meta_zeile(tage_paare(*k["paare"]), " paare", k["zeile2"]) + (meta_zeile(k["zeile3"], " zeilen", k.get("zeile3_title", "")) if k.get("zeile3") else "")
    if "schnitt" in k:
        # Balken je Tag, heute heller (läuft noch), darüber der gleitende Durchschnitt als Vergleichsreihe auf den Balkenmitten: der
        # Zeiger liest an jedem Tag Tageswert und Durchschnitt ab.
        grafik = kurve(k["tage"], "", "", was=k["was"], vergleich=[((i + .5) / 7, v) for i, v in enumerate(k["schnitt"])],
                       stellen=k["stellen"], stellen_v=k["stellen"], laufend=True, balken=True, plaetze=7, xlabels=k["xlabels"], laeuft=True)
    elif "vorwoche" in k:
        grafik = kurve(k["tage"], "", "", was=k["was"], vergleich=[((i + .5) / 7, v) for i, v in enumerate(k["vorwoche"])],
                       stellen=k["stellen"], stellen_v=k["stellen_v"], laufend=True, balken=True, plaetze=7, xlabels=k["xlabels"], laeuft=True,
                       vgl_klasse="schnitt ref")
    else:
        # Eine Kumulierung bis jetzt ist kein angebrochener Wert: Linie, Fläche und Punkt enden am selben letzten Punkt (10.10.2026). Als
        # angebrochen gezeichnet endeten Linie und Fläche einen Tag früher, das letzte Stück war gepunktet, der Punkt stand daneben.
        laeuft = k.get("laeuft", False)
        grafik = kurve(k["voll"], *k["x"], vergleich=k["vergleich"], was=k["was"], stellen=k["stellen"], stellen_v=k["stellen_v"],
                       laufend=laeuft, laeuft=laeuft)
    return kpi_html(k["n"], k["label"], k["zeile1_html"], meta, grafik, k["legende"], nav, rahmen)


# Umschalter der Kacheln 2 und 3 (30.09.2026): Schlüssel und Beschriftung, das erste ist die Vorgabe.
UMSCHALTER = {"k2": (("7", "seg.7"), ("woche", "seg.woche")), "k3": (("30", "seg.30"), ("monat", "seg.monat")),
              # Grafik im Aufklappdetail der Projektzeilen (03.10.2026): eine Wahl für alle Zeilen. Die erste Stufe ist die Wochengrafik (Mo bis So
              # der laufenden Kalenderwoche) und heisst seit 03.10.2026 «Woche» wie in Kachel 2; der Wert "7" bleibt, damit gitpuls-pd gilt.
              "pd": (("7", "seg.woche"), ("30", "seg.30"), ("90", "seg.90"))}


def umschalter(gruppe, wahl, aria="seg.aria"):
    """Segmente im Stil der Reiter des Rückblicks, klein. Jedes Fenster trägt seinen eigenen Umschalter mit sich selbst gedrückt; das Skript
    zeigt alle Fenster der Gruppe um, auch Lagesatz und Ring im Kopf (Gruppe k2), und merkt die Wahl im localStorage."""
    knoepfe = "".join(f'<button type="button" data-fenster="{s}" aria-pressed="{str(s == wahl).lower()}">{html.escape(t(l))}</button>'
                      for s, l in UMSCHALTER[gruppe])
    return f'<nav class="seg klein umschalter" aria-label="{html.escape(t(aria))}" data-gruppe="{gruppe}">{knoepfe}</nav>'


def umschalt_kachel(gruppe, varianten):
    """Eine Kachel, darin die Fenster übereinander in einer Rasterzelle (.stapel), alle ausser der Vorgabe hidden."""
    fenster = "".join(f'<div class="fenster" data-gruppe="{gruppe}" data-fenster="{s}"{"" if i == 0 else " hidden"}>'
                      f'{block(f"Kachel {k['label']}", kachel_block, k, umschalter(gruppe, s), False)}</div>' for i, (s, k) in enumerate(varianten))
    return f'<div class="card tile kpi umschalt"><div class="stapel">{fenster}</div></div>'


def kacheln_block(c, heute, jetzt, heute_offen=False):
    h, k7, k30 = kacheldaten(c, heute, jetzt)
    h["offen"] = heute_offen
    extra = kachelvarianten(c, heute, jetzt)
    return (block(f"Kachel {h['label']}", kachel_block, h)
            + block("Kachel 2", umschalt_kachel, "k2", [("7", k7)] + extra["k2"])
            + block("Kachel 3", umschalt_kachel, "k3", [("30", k30)] + extra["k3"]))


def alter_text(letzter_utc, tage_her, jetzt):
    """Alter des letzten Commits: unter einer Stunde in Minuten, unter einem Tag in Stunden, danach in
    Kalendertagen wie SCHWELLEN, damit «vor 8 Tagen» und «ruht» nie auseinanderlaufen."""
    if not letzter_utc:
        return t("alter.kein")
    sek = (jetzt - datetime.fromisoformat(letzter_utc.replace("Z", "+00:00"))).total_seconds()
    if sek < 3600:
        return t("alter.min", max(1, int(sek // 60)))
    if sek < 86400:
        return t("alter.std", int(sek // 3600))
    return t("alter.gestern") if tage_her == 1 else t("alter.tage", tage_her)


# Seit 27.09.2026 30 Tagesbalken statt 12 Wochen: 30 × 3 + 29 × 1 = 119 px ab 1 px Rand in 120 px, jeder Balken 3 px breit.
MIKRO_TAGE, MIKRO_BALKEN, MIKRO_ABSTAND, MIKRO_B, MIKRO_H = 30, 3, 1, 120, 20
PD_TAGE = 90   # längstes Fenster des Umschalters im Aufklappdetail; die Mikrokurve nimmt davon die letzten MIKRO_TAGE


def tagesreihen(c, heute, jetzt, n=MIKRO_TAGE):
    """Je Repo die Commits je Kalendertag über die letzten n Tage bis heute, dasselbe Fenster wie der Rückblick im Reiter 30 Tage,
    nach Ortsdatum wie wochenreihen; Obergrenze jetzt, ein Zukunftscommit zählt nicht. Eine Abfrage."""
    start = heute - timedelta(days=n - 1)
    aus = {}
    for repo, tag in c.execute(f"SELECT repo, date(zeitstempel_utc, 'localtime') FROM commits WHERE zeitstempel_utc >= ? AND zeitstempel_utc < ? "
                               f"AND {IN_ADRESSEN}", (utc(start), obergrenze(jetzt), *ADRESSEN)):
        aus.setdefault(repo, [0] * n)[(date.fromisoformat(tag) - start).days] += 1
    return aus


def mikrokurve(reihe):
    """Die letzten 30 Kalendertage als 30 senkrechte Balken in 120x20 px (seit 27.09.2026, vorher zwölf Kalenderwochen in 96x20), für alle
    Zeilen dasselbe Fenster (tagesreihen), dieselbe Breite und derselbe Abstand; der Massstab je Zeile ist ihr eigenes Maximum. Ein
    Tag ohne Commits ist ein Grundstrich von 1 px, keine Lücke. Keine Achse, keine Legende, kein Zeiger, keine Schrift.
    Seit 13.09.2026; die Linie davor zeichnete ab der ersten Woche mit Commits, ein Repo mit einer Woche Daten stand
    als 12-px-Stummel da und die Zeilen waren untereinander nicht vergleichbar."""
    oben = max(reihe) or 1
    balken = []
    for i, v in enumerate(reihe):
        # ponytail: ein Wert über null ist mindestens 2 px hoch, sonst läse er sich neben 174 als leer
        h = max(2, round(MIKRO_H * v / oben)) if v else 1
        # Der laufende Tag (letzter Wert) im Akzent, auch als Grundstrich bei null (Entscheid 25.09.2026 für die Woche).
        jetzt = ' class="jetzt"' if i == len(reihe) - 1 else ""
        balken.append(f'<rect{jetzt} x="{1 + i * (MIKRO_BALKEN + MIKRO_ABSTAND)}" y="{MIKRO_H - h}" width="{MIKRO_BALKEN}" height="{h}"/>')
    return f'<svg class="mikro" viewBox="0 0 {MIKRO_B} {MIKRO_H}" width="{MIKRO_B}" height="{MIKRO_H}" aria-hidden="true">{"".join(balken)}</svg>'


def projekt_stand(k, woche, woche_vor, zeilen=None, zeilen_vor=None):
    """Der frühere Inhalt von «Stand und letzte Commits»: ein Raster aus Beschriftung und Wert (seit 26.09.2026, vorher Chips) und die
    letzten fünf Betreffzeilen. Seit 25.09.2026 nennt das Raster die Codezeilen der laufenden Woche aus derselben Abfrage wie der Wochenwert (wochenreihen), gedämpft «Zeilen werden
    nachgeladen», solange für die Woche Werte fehlen. Die Vorwoche steht im title, aus derselben Menge wie der Vergleichswert der
    Zeile (vorwoche_je_repo)."""
    plus, minus, offen = zeilen or (0, 0, 0)
    plus_v, minus_v, offen_v = zeilen_vor or (0, 0, 0)
    # Raster aus Beschriftung und Wert (26.09.2026) statt der Metazeile mit Aufzählungspunkten. Weggelassen, was doppelt stand: die
    # Datumsspanne der laufenden Woche (es ist immer die laufende) und «letzter Commit» (steht als erste Zeile der Liste darunter).
    zeilen_wert = (html.escape(t("ps.nachladen")) if offen or offen_v else
                   f'<span title="{html.escape(t("kachel.zeilen_vor", zeilen_text(plus_v, minus_v)))}">{zeilen_werte(plus, minus, als_html=True)}</span>')
    # Vorwoche kurz wie in der Projektzeile («45 (Vorwoche 1)»), die volle Beschriftung im title (26.09.2026).
    woche_wert = (f'<span title="{html.escape(t("ps.vorwoche"))} {fmt.zahl(woche_vor)}">{fmt.zahl(woche)} '
                  f'({html.escape(t("delta.vorwoche", fmt.zahl(woche_vor)))})</span>')
    felder = [(t("ps.woche"), woche_wert, ""), (t("ps.zeilen"), zeilen_wert, ""), (t("ps.sprache"), html.escape(k["sprache"]), "")]
    # Jeder Befund ein eigenes Paar (02.10.2026), mit denselben Einzelheiten wie sein Eintrag im Handlungsbedarf.
    if k["server"]:
        felder.append((t("ps.server"), server_wert(k), ""))
    if k.get("updates"):
        felder.append((t("ps.dependabot"), dependabot_wert(k), ""))
    if k.get("befunde"):
        felder.append((t("ps.zweige"), ", ".join(f'<span translate="no">{html.escape(b["zweig"])}</span> {html.escape(t("zweig.stand", b["ahead"], b["behind"], b["branch"]))}'
                                                 + (f' <span class="hbrest">{html.escape(seit_text(b["seit"]))}</span>' if b["seit"] is not None else "") for b in k["befunde"]), ""))
    if k.get("ci"):
        felder.append((t("ps.actions"), f'{html.escape(t("hb.ci_zeile", k["ci"]["workflow"]))} <span class="hbrest">{html.escape(k["ci"]["alter"])}</span>', ""))
    if k.get("abruf"):
        felder.append((t("ps.abruf"), f'<span translate="no">{html.escape(k["abruf"]["text"])}</span>', ""))
    # Der Zweig steht zuletzt und nimmt den Rest der Zeile: ein langer Name kürzt mit Ellipse (voller Name im title), statt das Raster um
    # eine Zeile zu verlängern (gemessen 26.09.2026: «release/2026-herbst-umbau-der-kartenansicht» brauchte 335 px).
    felder.append((t("ps.zweig"), f'<span translate="no" title="{html.escape(k["branch"])}">{html.escape(k["branch"])}</span>', " zweig"))
    # Seit 26.09.2026 fliessen die Paare nebeneinander und brechen nur um, wenn der Platz fehlt; ein Paar (.ppaar) wird nie getrennt.
    raster = '<dl class="pmeta">' + "".join(f'<div class="ppaar{k_}"><dt>{html.escape(b)}</dt><dd>{w}</dd></div>' for b, w, k_ in felder) + "</dl>"
    # Datum kurz («26.09.»), die Commits sind die letzten fünf und liegen fast immer im laufenden Jahr (26.09.2026).
    liste = "".join(f'<li><span class="datum">{fmt.datum(datum(z["zeitstempel_utc"]))}</span><span translate="no">{html.escape(z["betreff"] or "")}</span></li>'
                    for z in k["letzte"]) or f"<li class='muted'>{t('ps.keine')}</li>"
    # Offene Sicherheitswarnungen (25.09.2026): je Warnung Stufe, Paket als Link auf die Warnung, Manifest-Pfad und behobene Version.
    warn = ""
    if k.get("warnliste"):
        warn = (f'<p class="pwarn">{gh_link(k["dependabot"], t("warn.detail_titel"), t("warn.liste_title"))}</p><ul class="commits">'
                + "".join(f'<li><span class="datum">{html.escape(t("stufe", w["stufe"]))}</span><span>{gh_link(w["url"], w["paket"], paket_title(w))}'
                          f' · <span translate="no">{html.escape(w["manifest"] or "")}</span> · {html.escape(t("warn.behoben", w["behoben"]))}</span></li>'
                          for w in k["warnliste"]) + "</ul>")
    # Ein Element, damit Kurve und Stand im Detail zwei Spalten eines Rasters sind.
    return f'<div class="pstand">{raster}<ul class="commits">{liste}</ul>{warn}</div>'


def detail_kurven(name, woche_tage, tage, heute):
    """Umschalter Woche, 30 und 90 Tage über der Grafik im Aufklappdetail (03.10.2026), im Stil der Umschalter der Kacheln 2 und 3. Die drei
    Grafiken liegen übereinander in einer Rasterzelle (.stapel), die Höhe springt beim Wechsel nicht. Der Umschalter steht ausserhalb
    der Fenster, ein Klick zeigt die Wahl in allen Projektzeilen und merkt sie im localStorage (gitpuls-pd). Jede Grafik ein eigener Block."""
    fenster = [("7", block(f"Wochenkurve {name}", karte_kurve, list(woche_tage), heute)),
               ("30", block(f"Tageskurve 30 {name}", tage_kurve, tage[-30:], heute)),
               ("90", block(f"Tageskurve 90 {name}", tage_kurve, tage[-90:], heute))]
    return (f'<div class="pkurven">{umschalter("pd", "7", "seg.aria_pd")}<div class="stapel">'
            + "".join(f'<div class="fenster" data-gruppe="pd" data-fenster="{s}"{"" if i == 0 else " hidden"}>{g}</div>' for i, (s, g) in enumerate(fenster))
            + "</div></div>")


def projekt_zeile(k, reihe, woche_vor, schluessel, heute, jetzt, zeilen=None, zeilen_vor=None, tage=None, woche_tage=(0,) * 7):
    """Eine Zeile je Repo: Name, Wochenwert mit Delta, Mikrokurve, Alter, Betreff des letzten Commits, Befund nur wo
    einer vorliegt, Chevron.
    Aufgeklappt die Tage der laufenden Woche mit Achsen und Zeiger, darunter Stand und letzte Commits; ruhende Repos
    bekommen dasselbe Detail. Mikrokurve, Wochenkurve und Stand sind je ein eigener Block."""
    aktiv = k["tage_her"] is not None and k["tage_her"] <= SCHWELLEN["aktiv"]
    tage = tage or [0] * PD_TAGE
    # Angebrochene Woche gegen die Vorwoche zum selben Zeitpunkt (Wochentag und Uhrzeit), derselbe Massstab wie die Wochenkachel.
    d_html, d_vor = delta_html(reihe[-1], woche_vor, t("ps.vorwoche"))
    alter = alter_text(k["letzter_utc"], k["tage_her"], jetzt)
    if not aktiv and k["tage_her"] is not None:
        alter += t("alter.ruht") if k["tage_her"] <= SCHWELLEN["ruht"] else t("alter.still")
    befund = f'<span class="pbefund">{" · ".join(k["kurz"])}</span>' if k["kurz"] else ""
    # Betreff des letzten Commits, dieselbe Quelle wie das Detail (k["letzte"]). Ohne Commit im Fenster der Kurve
    # bleibt die Zelle leer: ein Betreff von vor Monaten läse sich wie laufende Arbeit.
    betreff = (k["letzte"][0]["betreff"] or "") if k["letzte"] and any(reihe) else ""
    repo, name = html.escape(k["repo"]), k["name"]
    return (f'<details class="prow{"" if aktiv else " gedaempft"}" data-repo="{repo}" id="{anker(k['repo'])}"><summary>'
            f'<a class="pname" href="https://github.com/{repo}" title="{html.escape(name)}">{html.escape(name)}</a>'
            f'<span class="pwert" title="{html.escape(t("woche_ab", wochenmontag(schluessel[-1])))}, {html.escape(d_vor)}"><span class="pzahl">{fmt.zahl(reihe[-1])}</span>{d_html}</span>'
            + block(f"Mikrokurve {name}", mikrokurve, tage[-MIKRO_TAGE:])
            + f'<span class="palter">{alter}</span><span class="pbetreff" title="{html.escape(betreff)}" translate="no">{html.escape(betreff)}</span>'
            f'{befund}<span class="chev" aria-hidden="true"></span></summary>'
            f'<div class="pdetail">{detail_kurven(name, woche_tage, tage, heute)}'
            f'{block(f"Projektstand {name}", projekt_stand, k, reihe[-1], woche_vor, zeilen, zeilen_vor)}</div></details>')


def vorwoche_je_repo(c, start, ende):
    """Je Repo (Commits, plus, minus, offen) mit Zeitstempel in [start, ende), aus einer Abfrage: Vergleichswert und Codezeilen der
    Vorwoche in Projektzeile und Detail rechnen gegen dieselbe Menge (Harte Grenze), mit den Rändern der Wochenkachel
    (vorwoche_fenster, zeitfensterwerte)."""
    aus = {}
    for repo, plus, minus, _, _, geladen in c.execute(f"SELECT repo, {ZEILEN_SQL} FROM commits WHERE zeitstempel_utc >= ? AND zeitstempel_utc < ? "
                                                      f"AND {IN_ADRESSEN}", (iso_utc(start), obergrenze(ende), *ADRESSEN)):
        n, p_, m, o = aus.get(repo, (0, 0, 0, 0))
        aus[repo] = (n + 1, p_ + plus, m + minus, o + (geladen is None))
    return aus


def projekte_block(c, heute, jetzt, ruhend=False):
    """Alle Repos, eine Zeile je Repo, in zwei Gruppen nach dem Wochenwert und darin nach letztem Commit; ruhende und
    stillliegende gedämpft. 0 Commits in der laufenden Woche zählt als ohne, auch mit Befund. Jede Zeile ist ein eigener Block."""
    schluessel, reihen, zeilen_w, tage_w = wochenreihen(c, heute, jetzt, zeilen=True)
    karten = projekte(c, heute, jetzt)
    vorwoche = vorwoche_je_repo(c, *vorwoche_fenster(heute, jetzt))
    tage = tagesreihen(c, heute, jetzt, n=PD_TAGE)
    leer = (0, 0, 0, 0)
    reihe12 = {k["repo"]: reihen.get(k["repo"], [0] * len(schluessel))[-12:] for k in karten}
    # Zwei Gruppen (25.09.2026) statt einer Liste mit Zählzeile: «Diese Woche aktiv» (Wochenwert grösser null) und «Ohne Commits
    # diese Woche», je mit Anzahl; darin die Reihenfolge von projekte(), nach letztem Commit. Die Zählzeile «N Repos: M mit
    # Commits …» entfällt, die zwei Zwischentitel tragen dieselbe Aussage. Eine leere Gruppe entfällt samt Titel und Linie.
    zeile = lambda k: block(f"Projektzeile {k['name']}", projekt_zeile, k, reihe12[k["repo"]], vorwoche.get(k["repo"], leer)[0], schluessel, heute, jetzt,
                            zeilen_w.get(k["repo"]), vorwoche.get(k["repo"], leer)[1:], tage.get(k["repo"]), tage_w.get(k["repo"], [0] * 7))
    gruppen = [(aktiv, [k for k in karten if (reihe12[k["repo"]][-1] > 0) == aktiv]) for aktiv in (True, False)]
    n_aktiv, n_ohne = (len(ks) for _, ks in gruppen)
    assert n_aktiv + n_ohne == len(karten), (n_aktiv, n_ohne, len(karten))
    # Zahlen im Block (26.09.2026) nach der Regel der Kachel-Metazeilen (tage_paare): Wert in Textfarbe (.kwert), Beschriftung gedämpft.
    # Die Gesamtzahl steht nicht mehr allein rechts im Titel, sondern links darunter als «14 Repos · 9 diese Woche aktiv», beides aus
    # denselben Gruppen gezählt. Seit 27.09.2026 trägt die aktive Gruppe keinen Titel mehr, an seiner Stelle steht eine Kopfzeile, die jede
    # Spalte beschriftet, auf demselben Raster wie die Zeilen (.pgruppe.spalten): «Commits» rechtsbündig auf der Zahlenkante, die übrigen
    # bündig mit ihrer Spalte. Die Gruppe «Ohne Commits diese Woche» behält Titel und Zahl und zeigt aufgeklappt dieselbe Kopfzeile.
    wert = lambda x: f'<span class="kwert">{fmt.zahl(x)}</span>'
    spalten = lambda getrennt: (f'<p class="pgruppe spalten{getrennt}"><span class="pkname">{html.escape(t("projekte.k_repo"))}</span>'
                                f'<span class="pwert pkw"><span class="pspalte">{html.escape(t("projekte.spalte"))}</span></span>'
                                f'<span class="pkmikro">{html.escape(t("projekte.k_tage"))}</span><span class="pkalter">{html.escape(t("projekte.k_zuletzt"))}</span>'
                                f'<span class="pkbetreff">{html.escape(t("projekte.k_betreff"))}</span></p>')
    meta = (f'<p class="delta bezug pkopf">{wert(len(karten))} {html.escape(t("kachel.repos", len(karten)))} · '
            f'{wert(n_aktiv)} {html.escape(t("projekte.meta_aktiv"))}</p>')
    # Die zweite Gruppe ist seit 25.09.2026 ein Aufklapper, standardmässig zu; offen steht sie mit ?ruhend=offen, das der
    # Server schon beim Rendern setzt (kein Springen nach dem Laden) und das Skript beim Umschalten nachführt.
    teile, erste = [], True
    for aktiv_, ks in gruppen:
        if not ks:
            continue
        getrennt = "" if erste else " getrennt"
        if aktiv_:
            kopf = spalten(getrennt)
        else:
            kopf = (f'<details class="gruppe ruhend"{" open" if ruhend else ""}><summary class="pgruppe{getrennt}">'
                    f'{t("projekte.ohne")}<span class="panz kwert">{fmt.zahl(len(ks))}</span><span class="chev" aria-hidden="true"></span></summary>'
                    + spalten(""))
        teile.append(kopf + f'<div class="pliste">{"".join(zeile(k) for k in ks)}</div>' + ("" if aktiv_ else "</details>"))
        erste = False
    return f"""<h3 class="titel">{t("projekte.titel")}</h3>
  {(meta + "".join(teile)) if karten else f'<p class="sub">{t("projekte.leer")}</p>'}"""


RUECKBLICK = ("7", "30", "60", "90", "beginn")   # Beschriftung aus der Texttabelle, rb.30 und so weiter
# Mehr Tage als das, und der Rückblick zeichnet Wochen statt Tage. Gemessen am 13.09.2026 in Chrome 153 auf den Live-Daten:
# die Zeichenfläche ist 703.6 px breit bei 1280 und 268.6 px bei 430, 30 Tage liegen 24.3 und 9.3 px auseinander. 90 Tage
# lägen 7.9 und 3.0 px auseinander, seit Beginn (146 Tage) 4.9 und 1.9 px, bei einer Linie von 2 px: schmal verschmelzen
# Anstieg und Abstieg eines einzelnen Tages. Unter den 9.3 px, die heute schmal gelten, wird keine Tagesreihe behauptet.
# ponytail: die Grenze folgt der Breite nicht von selbst; wer Rückblick-Raster oder Achsenspalte ändert, misst neu.
TAGESPUNKTE_MAX = 30


def rueckblick_fenster(tage, heute, erster, schluessel, name):
    """Ein Fenster des Rückblicks: (Kopfzeile, Grafik mit Legende). Das Fenster beginnt nie vor dem ersten Tag mit
    Daten. Bis TAGESPUNKTE_MAX Tage ein Punkt je Tag, darüber einer je Kalenderwoche ab Montag. Angebrochen sind die laufende
    Woche immer und die erste, wenn der Fensterrand sie schneidet; sie zählen nur ihre Tage im Fenster, die Zeigerzeile nennt
    genau diese Tage, ihr Balken ist ein Umriss (seit 27.09.2026, vorher ein gestricheltes Segment), und sie fallen aus Spitze und Durchschnitt (ohne_angebrochene). Seit
    13.09.2026; vorher rechnete der Durchschnitt Summe mal sieben durch die Tage, und eine Teilwoche konnte die Spitze sein.
    Unter zwei vollen Wochen entfielen Spitze und Linie; mit mehr als 30 Tagen liegen gerechnet mindestens drei volle Wochen
    im Fenster, der Fall bleibt hier theoretisch und ist an der Detailkurve belegt. Über TAGESPUNKTE_MAX Wochen einer je Monat.
    `woche` ist die Einheit für die Texte: False Tag, True Woche, "monat" Monat."""
    von = erster if schluessel == "beginn" else max(heute - timedelta(days=int(schluessel) - 1), erster)
    je_tag = [tage.get(von + timedelta(days=i), 0) for i in range((heute - von).days + 1)]
    summe = sum(je_tag)
    if len(je_tag) <= TAGESPUNKTE_MAX:
        werte, stellen, woche = je_tag, [fmt.datum_jahr(von + timedelta(days=i)) for i in range(len(je_tag))], False
        spitze = lambda i: t("rb.am", von + timedelta(days=i))
        mittel, angebrochen = schnitt(je_tag), set()
        i_max = max(range(len(werte)), key=lambda i: werte[i])
    else:
        montag0 = von - timedelta(days=von.weekday())
        werte = [0] * ((heute - montag0).days // 7 + 1)
        for i, v in enumerate(je_tag):
            werte[(von + timedelta(days=i) - montag0).days // 7] += v
        montage = [montag0 + timedelta(weeks=w) for w in range(len(werte))]
        stellen = [fmt.spanne(max(m, von), min(m + timedelta(days=6), heute)) for m in montage]
        spitze, woche = (lambda i: t("rb.ab", montage[i])), True
        angebrochen = {i for i, m in enumerate(montage) if m < von} | {len(montage) - 1}
        if len(werte) > TAGESPUNKTE_MAX:
            # Mehr als TAGESPUNKTE_MAX Wochen: einer je Kalendermonat (10.10.2026, Vorgabe vom 25.09.2026 für lange Historie), aus demselben Grund
            # und mit derselben Grenze wie Tage zu Wochen: über 30 Punkten lägen sie bei 430 px keine 9.3 px auseinander (drei Jahre: 157 Wochen
            # zu 1.7 px). Angebrochen sind der laufende Monat und der erste, wenn das Fenster nicht am Ersten beginnt.
            monate = [date(von.year + (von.month - 1 + k) // 12, (von.month - 1 + k) % 12 + 1, 1) for k in range((heute.year - von.year) * 12 + heute.month - von.month + 1)]
            index = {(m.year, m.month): k for k, m in enumerate(monate)}
            werte = [0] * len(monate)
            for i, v in enumerate(je_tag):
                d = von + timedelta(days=i)
                werte[index[(d.year, d.month)]] += v
            ende = lambda m: min(date(m.year + m.month // 12, m.month % 12 + 1, 1) - timedelta(days=1), heute)
            stellen = [fmt.spanne_jahr(max(m, von), ende(m)) for m in monate]
            spitze, woche = (lambda i: t("rb.im", monate[i])), "monat"
            angebrochen = ({0} if von.day != 1 else set()) | {len(monate) - 1}
        i_max, mittel = ohne_angebrochene(werte, angebrochen)
    grafik = kurve(werte, fmt.datum_jahr(von), fmt.datum_jahr(heute), klasse="gross", bezug=(mittel, "Durchschnitt") if mittel is not None else None,
                   max_marke=f"{fmt.zahl(werte[i_max])} {spitze(i_max)}" if tage and i_max is not None else None, was=t("rb.was", woche),
                   stellen=stellen, angebrochen=angebrochen, laufend=not woche, balken=True, laeuft=True)
    # Einzeilig bei 1280 px (25.09.2026): Reihe und Mittel in der Zeile, Zone und Wochenbeginn im title; «ab dem ersten Tag mit
    # Daten» entfällt, der Zeitraum steht schon in der Kopfzeile des Rückblicks.
    zusatz = t("rb.zusatz", woche, zonenname())
    legende = (t("rb.legende", sw(balken=True), woche)
               + (t("rb.mittel", sw(True), mittel, woche) if mittel is not None and zeigt_bezug(werte, mittel) else ""))
    zeitraum = t("rb.zeitraum", t("rb.tage", len(je_tag)) if schluessel != "beginn" else name, von, heute, fmt.zahl(summe))
    return (zeitraum if tage else t("keine_daten")), f'{grafik}\n    <p class="sub rlegende1" title="{html.escape(zusatz)}">{legende}</p>'


def wochenrang(c, heute, jetzt):
    """Rang der laufenden Woche unter allen Kalenderwochen seit der ersten mit eigenem Commit, je zum selben Zeitpunkt: ab Montag 00:00
    bis zum selben Wochentag im selben Abstand ab dessen Tagesbeginn, wie vorwoche_fenster. (Rang, Wochen) oder None ohne Commit.
    Seit 10.10.2026 (Audit S19): vorher zählte der Nenner nur Wochen mit Commits («Rang 2 von 1 Wochen» in einer Woche ohne Commit) und
    verglichen wurde bis zum Ende des gleichen Wochentags, nicht zum selben Zeitpunkt, wie der Text sagt."""
    zeiten = [datetime.fromisoformat(z.replace("Z", "+00:00")) for (z,) in
              c.execute(f"SELECT zeitstempel_utc FROM commits WHERE zeitstempel_utc < ? AND {IN_ADRESSEN}", (obergrenze(jetzt), *ADRESSEN))]
    if not zeiten:
        return None
    erster = min(zeiten).astimezone(TZ).date()
    m0 = erster - timedelta(days=erster.weekday())
    werte = [0] * ((heute - timedelta(days=heute.weekday()) - m0).days // 7 + 1)
    abstand = jetzt.astimezone(timezone.utc) - tag0(heute)
    for z in zeiten:
        w = (z.astimezone(TZ).date() - m0).days // 7
        if z < tag0(m0 + timedelta(weeks=w, days=heute.weekday())) + abstand:
            werte[w] += 1
    return 1 + sum(1 for v in werte if v > werte[-1]), len(werte)


def rueckblick_block(c, heute, jetzt, wahl):
    """Fenster als Reiter: alle im HTML übereinander in einer Rasterzelle, zwei davon
    hidden, auch ihre Kopfzeilen; der Wechsel ist clientseitig und schreibt ?rueckblick= per replaceState. Serie und
    Rekorde über alle Daten bis jetzt, unabhängig vom Fenster."""
    tage = tageszaehlung(c, jetzt)
    ser, rek, rang = serien(tage, heute), rekorde(tage), wochenrang(c, heute, jetzt)
    erster = min(tage) if tage else heute
    fenster = [(s, *rueckblick_fenster(tage, heute, erster, s, t(f"rb.{s}"))) for s in RUECKBLICK]
    verbergen = lambda s: "" if s == wahl else " hidden"
    kopf = "".join(f'<span class="fenster" data-fenster="{s}"{verbergen(s)}>{z}</span>' for s, z, _ in fenster)
    grafiken = "".join(f'<div class="fenster" data-fenster="{s}"{verbergen(s)}>{g}</div>' for s, _, g in fenster)
    reiter = "".join(f'<button type="button" data-fenster="{s}" aria-pressed="{str(s == wahl).lower()}">{t(f"rb.{s}")}</button>' for s in RUECKBLICK)
    rek_html = (f'<li><span class="datum">{t("rek.tag")}</span>{t("rek.mit", fmt.datum_jahr(rek["tag"][0]), fmt.zahl(rek["tag"][1]))}</li>'
                f'<li><span class="datum">{t("rek.woche")}</span>{t("rek.mit", wochenspanne(rek["woche"][0]), fmt.zahl(rek["woche"][1]))}</li>'
                f'<li><span class="datum">{t("rek.laufend")}</span>{t("rek.rang", *rang)}</li>') if rek and rang else f'<li>{t("keine_daten")}</li>'
    serie_html = f'<li><span class="datum">{t("rek.serie")}</span>{t("rek.serie_wert", ser["laengste"], ser["laengste_von"], ser["laengste_bis"])}</li>'
    # Commits ohne Elternzahl (Repo gelöscht oder nicht mehr gelistet, Audit 25.09.2026) zählen als Einzelcommit; ihre Zahl einmal im title.
    ohne = c.execute(f"SELECT COUNT(*) FROM commits WHERE {IN_ADRESSEN} AND eltern IS NULL", ADRESSEN).fetchone()[0]
    ohne_title = f' title="{html.escape(t("rb.ohne_eltern", ohne))}"' if ohne else ""
    return f"""<h3 class="titel">{t("rb.titel")} <span class="lbl"{ohne_title}><span class="stapel">{kopf}</span></span></h3>
  <nav class="seg small reiter" aria-label="{t("rb.aria")}" data-param="rueckblick" data-vorgabe="30" data-speicher="gitpuls-rueckblick">{reiter}</nav>
  <div class="rraster"><div class="stapel">{grafiken}</div>
    <div><h3 class="titel">{t("rek.titel")} <span class="lbl">{t("rek.seit", erster)}</span></h3>
      <ul class="liste klein">{serie_html}{rek_html}</ul></div></div>"""


# Gemerkte Wahl vor dem ersten Bild (30.09.2026): das Kopfskript liest die drei Schlüssel aus dem localStorage (try/catch), prüft sie gegen
# die bekannten Fenster und setzt sie als data-k2, data-k3, data-rb auf <html>; ein ?rueckblick= in der Adresse geht vor. Die Regeln daneben
# zeigen damit sofort das gewählte Fenster und den gedrückten Reiter, aus denselben Listen wie die Knöpfe. Das Skript am Seitenende setzt
# danach hidden und aria-pressed passend und führt das Attribut bei jedem Wechsel nach, sonst hielte die Regel die alte Wahl fest.
WAHL = {"k2": [s for s, _ in UMSCHALTER["k2"]], "k3": [s for s, _ in UMSCHALTER["k3"]], "pd": [s for s, _ in UMSCHALTER["pd"]], "rb": list(RUECKBLICK)}
_WAHL_ZEIGEN = ", ".join([f'html[data-{g}="{f}"] .fenster[data-gruppe="{g}"][data-fenster="{f}"]' for g in ("k2", "k3", "pd") for f in WAHL[g]]
                         + [f'html[data-rb="{f}"] .rueckblick .fenster[data-fenster="{f}"]' for f in WAHL["rb"]])
_WAHL_REITER = ", ".join([f'html[data-rb="{f}"] .rueckblick .reiter button[data-fenster="{f}"]' for f in WAHL["rb"]]
                         + [f'html[data-pd="{f}"] nav[data-gruppe="pd"] button[data-fenster="{f}"]' for f in WAHL["pd"]])
WAHL_KOPF = ("<script>try{var w=document.documentElement,g=" + json.dumps(WAHL, separators=(",", ":")) + ";for(var k in g){"
             "if(k==='rb'&&new URLSearchParams(location.search).has('rueckblick'))continue;"
             "var v=localStorage.getItem(k==='rb'?'gitpuls-rueckblick':'gitpuls-'+k);if(g[k].indexOf(v)>=0)w.setAttribute('data-'+k,v)}}catch(e){}</script>"
             '<style>html[data-k2] .fenster[data-gruppe="k2"], html[data-k3] .fenster[data-gruppe="k3"], html[data-pd] .fenster[data-gruppe="pd"], '
             'html[data-rb] .rueckblick .fenster { visibility: hidden; } '
             f"{_WAHL_ZEIGEN} {{ visibility: visible; }} "
             "html[data-rb] .rueckblick .reiter button[aria-pressed], html[data-pd] nav[data-gruppe=\"pd\"] button[aria-pressed] { background: none; color: var(--sec); } "
             f"{_WAHL_REITER} {{ background: var(--eis); color: var(--bg); }}</style>")


def stand_block(c):
    """Der Knopf holt nur GitHub; der Serverstand trägt seine eigene Alterszeile in server_html."""
    lauf = c.execute("SELECT gestartet_utc, beendet_utc, neu, teilweise FROM laeufe ORDER BY beendet_utc DESC LIMIT 1").fetchone()
    if not lauf:
        return f"<span>{t('stand.keiner')}</span>"
    beendet = datetime.fromisoformat(lauf["beendet_utc"].replace("Z", "+00:00"))
    b = beendet.astimezone(TZ)
    zeit = fmt.zeitpunkt(b)
    # Teilerfolg (26.09.2026): wies GitHub im Lauf ein Repo ab, sagt die Alterszeile das ausdrücklich, statt einen vollen Stand zu behaupten.
    # Seit 10.10.2026 getrennt: nicht abrufbare Repos (repos.fehler) und gescheiterte Prüfabfragen (CI, Sicherheit), je aus dem Stand dieses
    # Laufs (gesehen = Start); vorher hiess jeder Prüffehler «Repo nicht abrufbar».
    teil = ""
    if lauf["teilweise"]:
        start = lauf["gestartet_utc"]
        abruf = c.execute("SELECT COUNT(*) FROM repos WHERE gesehen = ? AND fehler IS NOT NULL", (start,)).fetchone()[0]
        pruef = c.execute("SELECT COALESCE(SUM((ci_fehler IS NOT NULL) + (sicher_fehler IS NOT NULL)), 0) FROM pruefungen WHERE gesehen = ?", (start,)).fetchone()[0]
        teil = t("stand.teil", abruf, pruef) if abruf or pruef else t("stand.teil", lauf["teilweise"], 0)
    return f'<span>{html.escape(t("stand.github", zeit, int((db.jetzt(timezone.utc) - beendet).total_seconds() // 60)) + teil)}</span>'


# Fusszeile (26.09.2026, für die freie Veröffentlichung): Copyright «© <Jahr> gitpuls.com» aus den Texttabellen, seit 14.10.2026 mit
# gitpuls.com als Link auf die Produktseite wie die übrigen Aussenlinks der Seite (neuer Tab, rel="noopener"), und die laufende Version. Die Zeile zu Zeitzone und Wochenbeginn bleibt weg (Entscheid 11.09.2026). Seit v1.0.2 trägt ein
# Release-Bild seine Version selbst (GITPULS_VERSION aus dem Build-Argument von release.yml), GITPULS_TAG wählt nur noch das Bild: mit der
# .env aus .env.example reichte compose.yaml «unbekannt» herein. Ein Bau ohne Argument zeigt wie bisher GITPULS_TAG aus der .env, den
# Commit; ohne beides (Mac, Tests) entfällt die Angabe.
VERSION = (os.environ.get("GITPULS_VERSION") or os.environ.get("GITPULS_TAG") or "").strip()


def zeitpunkt(iso_s):
    return fmt.zeitpunkt(datetime.fromisoformat(iso_s.replace("Z", "+00:00")).astimezone(TZ))


def fusszeile(c, server, jetzt):
    """Copyright mit Jahr und Inhaber, Stand der GitHub-Daten (letzter Sammellauf wie stand_block), Serverstand nur mit Serverzone
    (server.json lesbar), dann die Version. Ein eigener Block in der Isolation."""
    lauf = c.execute("SELECT beendet_utc FROM laeufe ORDER BY beendet_utc DESC LIMIT 1").fetchone()
    teile = [t("fuss.copyright", fmt.jahr(jetzt)), t("fuss.github", zeitpunkt(lauf["beendet_utc"])) if lauf else t("stand.keiner")]
    if server:
        teile.append(t("fuss.server", zeitpunkt(server["erhoben_utc"])))
    if VERSION:
        teile.append(t("fuss.version", VERSION))
    html_ = "".join(f"<span>{html.escape(x)}</span>" for x in teile)
    return html_.replace("gitpuls.com", '<a href="https://gitpuls.com" target="_blank" rel="noopener">gitpuls.com</a>', 1)


def startpruefung(zone=None, heute=None):
    """Nachweis vor dem ersten Aufruf, dass Python und SQLite über den TATSÄCHLICH AUSGEWERTETEN Zeitraum
    dieselbe Zone rechnen: vom ersten Commit in der Datenbank bis heute, ohne Commits die letzten zwölf Monate.
    Liefert None oder den Text der Abweichung. Läuft etwas auseinander, wären Tageswerte und Serien still falsch,
    deshalb bricht der Start damit ab. Als eigene Funktion, damit sie prüfbar ist: im __main__-Block stünde sie
    ausserhalb jeder Reichweite der Tests (Gegenprobe 20.09.2026)."""
    zone = zone or TZ
    with db.verbinden() as c:
        erster = c.execute("SELECT MIN(zeitstempel_utc) FROM commits").fetchone()[0]
        heute_ = heute or db.jetzt(zone).date()
        von = datum(erster) if erster else heute_ - timedelta(days=365)
        return zonendeckung(c, zone, min(von, heute_), heute_)


def skript():
    """Das Seitenskript mit den Texten der aktiven Sprache als Objekt T."""
    texte = {k: t(f"js.{k}") for k in ("laeuft", "sperre", "fehler", "startet", "kein_lauf", "gescheitert")} | {"locale": fmt.bcp47()}
    # Die Uhrzeit im Laufstatus in der Zone des Servers, nicht des Browsers (10.10.2026, Audit S24); ohne IANA-Namen kennt Intl die Zone nicht.
    zone = getattr(TZ, "key", "") or ""
    if zone and not zone.startswith("/") and zonenpfad(zone):
        texte["zone"] = zone
    return SKRIPT.replace("/*TEXTE*/", json.dumps(texte, ensure_ascii=False))


def seite(jetzt=None, rueckblick="30", ruhend=False, sprache=None, lang_param=False, handlung=frozenset(), accept="", heute_offen=False, fehler=None):
    """Jeder Block wird für sich gerendert (block()); eine Ausnahme in einem Block lässt den Rest der Seite stehen. `sprache` übersteuert
    GITPULS_SPRACHE für diesen Aufruf; `lang_param` heisst, sie kam als ?lang= und der Link auf die Startseite behält sie. `accept` ist
    der Accept-Language-Kopf des Aufrufs, aus dem mit GITPULS_FORMAT die Format-Locale folgt (app/format.py). `fehler` nimmt die Namen
    der Blöcke auf, die in diesem Aufbau gebrochen sind."""
    sp = sprache or STANDARD
    marke = _blockfehler.set([] if fehler is None else fehler)
    try:
        with gilt(sp), fmt.gilt(fmt.waehlen(sp, accept)):
            return _seite(jetzt, rueckblick, ruhend, lang_param, handlung, heute_offen)
    finally:
        _blockfehler.reset(marke)


def _seite(jetzt, rueckblick, ruhend, lang_param, handlung_offen=frozenset(), heute_offen=False):
    jetzt = jetzt or db.jetzt(TZ)   # injizierbar für Tests an Wechseltagen
    heute = jetzt.date()
    s_daten, s_fehler = server_lesen()
    server = block("Server", server_html, s_daten, db.jetzt(timezone.utc), s_fehler)
    with db.verbinden() as c:
        stand_html = block("Sammelstand", stand_block, c)
        lage = block("GitHub-Lage", lage_block, c, heute, jetzt)
        kacheln = block("Kennzahlen", kacheln_block, c, heute, jetzt, heute_offen)
        handlung = block("Handlungsbedarf", handlung_block, c, heute, jetzt, handlung_offen)
        projekte_html = block("Projekte", projekte_block, c, heute, jetzt, ruhend)
        rueckblick = block("Rückblick", rueckblick_block, c, heute, jetzt, rueckblick)
        fuss = block("Fusszeile", fusszeile, c, s_daten, jetzt)
    sprache = aktiv()
    return f"""<!doctype html><html lang="{sprache}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>gitpuls</title><meta name="color-scheme" content="light dark">
<link rel="icon" href="/favicon.ico" sizes="32x32"><link rel="icon" href="{STATIK["icon.svg"]}" type="image/svg+xml"><link rel="apple-touch-icon" href="{STATIK["apple-touch-icon.png"]}">
{MODUS_KOPF}{WAHL_KOPF}<link rel="stylesheet" href="{STATIK["tokens.css"]}"><link rel="stylesheet" href="{STATIK["style.css"]}"></head>
<body>
<header><h1><a href="{f"/?lang={sprache}" if lang_param else "/"}">gitpuls</a></h1><div class="schalter"><nav class="seg small modus" aria-label="{t("modus.aria")}">{"".join(f'<button type="button" data-wahl="{w}">{t(k)}</button>' for w, k in MODI)}</nav></div></header>
<main>
{server}
<section class="zone github">
<div class="zonenkopf"><h2>GitHub</h2>
<div class="aktual"><span class="stamp lbl"><span id="laufinfo"></span>{stand_html}</span><button class="btn lauf" id="lauf" type="button" title="{t("lauf.title")}">{KREISPFEIL}<span class="wort">{t("lauf.knopf")}</span></button></div></div>
<div class="lage">{lage}</div>
<div class="tiles3">
{kacheln}
</div>
{handlung}
<section class="card projekte">
  {projekte_html}
</section>
<section class="card rueckblick">
  {rueckblick}
</section>
</section>
<footer class="fusszeile">{fuss}</footer>
</main>{skript()}</body></html>"""


# Erlaubte Hosts gegen DNS-Rebinding (10.10.2026): eine fremde Seite, deren Name kurz auf 127.0.0.1 zeigt, liest sonst die Seite im Namen
# des Browsers. Jede Anfrage, deren Host-Kopf nicht hier steht, bekommt 421. GITPULS_HOSTS nennt die Namen durch Komma (ohne Port passt jeder
# Port, mit Port nur dieser); Loopback gilt immer, sonst bräche der Healthcheck des Containers. tailscale serve reicht den Host unverändert
# weiter (gemessen 10.10.2026 auf dem VPS: Host v…ts.net:8444), die eigene Instanz trägt ihren Tailnet-Namen in der .env.
HOSTS = {h.strip().lower() for h in os.environ.get("GITPULS_HOSTS", "").split(",") if h.strip()} | {"localhost", "127.0.0.1", "[::1]"}


def host_erlaubt(host):
    host = (host or "").strip().lower()
    return host in HOSTS or re.sub(r":\d+$", "", host) in HOSTS


def eigener_ursprung(kopf):
    """Kommt ein POST von dieser Seite? Sec-Fetch-Site muss same-origin sein; ohne den Kopf (ältere Browser) muss Origin auf den Host zeigen,
    ohne beide gilt die Anfrage als fremd (10.10.2026, Audit N5). Vorher startete jeder POST /lauf einen Sammellauf, auch der einer fremden
    Seite im selben Browser, und verbrauchte Kontingent des Tokens."""
    seite_ = kopf.get("Sec-Fetch-Site")
    if seite_:
        return seite_ == "same-origin"
    herkunft = kopf.get("Origin")
    return bool(herkunft) and urlparse(herkunft).netloc in {kopf.get("Host"), kopf.get("X-Forwarded-Host")} - {None}


def sprache_waehlen(param):
    """Sprache eines Aufrufs: ?lang vor GITPULS_SPRACHE. Ein fremder Wert wird übergangen, nie übernommen. Seit 02.10.2026 ohne
    Umschalter und ohne Cookie lang."""
    return param if param in TABELLEN else STANDARD


class Handler(BaseHTTPRequestHandler):
    def fremder_host(self):
        if host_erlaubt(self.headers.get("Host")):
            return False
        self.antwort(421, b"Host nicht erlaubt (GITPULS_HOSTS)", "text/plain")
        return True

    def do_GET(self):
        if self.fremder_host():
            return
        u = urlparse(self.path)
        try:
            self.bedienen(u)
        except Exception as e:  # ausserhalb der Blöcke: 500 statt abgebrochener Verbindung (502 hinter tailscale serve)
            print(f"FEHLER {u.path}: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
            self.antwort(500, f"{type(e).__name__}: {e}".encode(), "text/plain")

    def bedienen(self, u):
        q = parse_qs(u.query)
        lang = q.get("lang", [""])[0]
        gewaehlt = sprache_waehlen(lang)
        if u.path == "/lauf/status":
            s = lauf_status()
            if s["phase"]:
                with gilt(gewaehlt):
                    s["phase"] = t("phase", s["phase"])
            return self.antwort(200, json.dumps(s, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        if u.path == "/gesund":
            # Baut die Seite selbst auf (rund 22 ms, der Healthcheck fragt alle 30 Sekunden) und misst damit,
            # was sie wirklich zeigt. Ein Zähler vom letzten fremden Aufruf bliebe unter einer Schemadrift
            # ohne Zuschauer leer. 503 statt 500: der Prozess lebt und liefert, die Seite bleibt 200 mit ihren
            # Fehlerzeilen, vollständig ist sie nicht. Der Container gilt danach als ungesund, ein Neustart
            # folgt daraus nicht (restart: unless-stopped greift beim Exit, Compose startet nichts wegen
            # Gesundheit; kein autoheal auf dem Host). Die Dienstzeile zeigt den Zustand ohnehin an.
            fehler = []
            seite(fehler=fehler)
            if fehler:
                return self.antwort(503, ("blockfehler: " + ", ".join(fehler)).encode(), "text/plain")
            return self.antwort(200, b"ok", "text/plain")
        if u.path in PFAD_ZU_DATEI:
            return self.antwort(200, (WEB / PFAD_ZU_DATEI[u.path]).read_bytes(), MEDIENTYP[Path(PFAD_ZU_DATEI[u.path]).suffix], cache=True)
        if u.path == "/favicon.ico":
            return self.antwort(200, (WEB / "favicon.ico").read_bytes(), "image/x-icon")
        if u.path != "/":
            return self.antwort(404, b"nicht gefunden", "text/plain")
        rb = q.get("rueckblick", ["30"])[0]
        # ?lang=en oder ?lang=de gilt für diesen Aufruf und lässt die übrigen Parameter unberührt; ein anderer Wert wird übergangen, dann
        # gilt GITPULS_SPRACHE (sprache_waehlen).
        self.antwort(200, seite(rueckblick=rb if rb in RUECKBLICK else "30", ruhend=q.get("ruhend", [""])[0] == "offen", heute_offen=q.get("heute", [""])[0] == "offen",
                                handlung=handlung_zeichen(q.get("handlung", [""])[0]),
                                sprache=gewaehlt, lang_param=lang in TABELLEN, accept=self.headers.get("Accept-Language", "")).encode(),
                    "text/html; charset=utf-8")

    def do_POST(self):
        if self.fremder_host():
            return
        if urlparse(self.path).path != "/lauf":
            return self.antwort(404, b"nicht gefunden", "text/plain")
        if not eigener_ursprung(self.headers):
            return self.antwort(403, json.dumps({"fehler": "fremder Ursprung"}).encode(), "application/json; charset=utf-8")
        code, daten = lauf_starten()
        self.antwort(code, json.dumps(daten, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_HEAD(self):
        self.kopf_nur = True
        self.do_GET()

    def antwort(self, code, body, typ, cache=False):
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=31536000, immutable" if cache else "no-store")
        self.end_headers()
        if not getattr(self, "kopf_nur", False):
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}")


def port():
    return db.zahl("GITPULS_PORT", 8789, minimum=1, ganz=True, maximum=65535)


if __name__ == "__main__":
    if not ADRESSEN:
        sys.exit("gitpuls: GITPULS_ADRESSEN ist nicht gesetzt. Trage die Commit-Adressen ein, die als eigene "
                 "zählen sollen, durch Komma getrennt (git config user.email). Ohne sie bliebe die Seite leer, "
                 "ohne zu sagen warum.")
    port = port()
    # Vor dem ersten Sammellauf gibt es keine Datei; eine Nur-Lese-Verbindung scheiterte
    # dann mit "unable to open database file" (gemessen 2026-09-02 auf dem VPS, 502).
    db.verbinden(schreibend=True).close()
    abweichung = startpruefung()
    if abweichung:
        sys.exit("gitpuls: " + abweichung)
    if TAKT_MIN > 0:
        threading.Thread(target=takt, daemon=True).start()
    print(f"gitpuls auf {BIND}:{port}, DB {db.DB}" + (f", Takt {TAKT_MIN:g} Min." if TAKT_MIN > 0 else ""))
    ThreadingHTTPServer((BIND, port), Handler).serve_forever()
