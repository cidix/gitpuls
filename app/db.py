"""Verbindung und Schema. GITPULS_DB zeigt auf die Datei, Vorgabe data/gitpuls.db."""
import fcntl
import math
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.environ.get("GITPULS_DB", "data/gitpuls.db"))
SCHEMA = Path(__file__).with_name("schema.sql")


def jetzt(tz=timezone.utc):
    """Die einzige Quelle für «jetzt» in der App (09.10.2026). GITPULS_JETZT hält die Uhr fest, ISO 8601 mit Offset, etwa
    2026-10-15T22:40:00+02:00, für den Demodatensatz (demo/); fehlt die Variable, gilt die Systemuhr wie bisher."""
    fest = os.environ.get("GITPULS_JETZT")
    return datetime.fromisoformat(fest).astimezone(tz) if fest else datetime.now(tz)

# Spalten, die nach dem ersten Schema dazukamen; ALTER TABLE ist idempotent über die Spaltenliste.
NACHTRAEGE = {
    # eltern: Zahl der Eltern je Commit (25.09.2026); ein Merge hat mehr als einen und zählt nirgends. NULL heisst noch nicht
    # geholt (Altbestand vor dem ersten --alles-Lauf) und zählt wie bisher.
    # dateien_geladen: 1, sobald die Zeilen je Datei in commit_dateien stehen (25.09.2026); NULL heisst noch nicht geladen, 0 nicht
    # verfügbar (404 oder 422 auf dem Commit-Detail, Audit 25.09.2026), mit dateien_versuche und dem letzten Versuch in dateien_versucht.
    "commits": [("betreff", "TEXT"), ("eltern", "INTEGER"), ("dateien_geladen", "INTEGER"), ("dateien_versuche", "INTEGER"),
                ("dateien_versucht", "TEXT")],
    # fehler: Code und Meldung, mit der GitHub das Repo im letzten Lauf abwies (403, 404, 451 …), fehler_utc der erste Lauf dieser
    # Reihe; NULL, sobald ein Lauf das Repo wieder lesen konnte (Audit 25.09.2026).
    # html_url: Adresse des Repos auf GitHub aus user/repos (25.09.2026), Grundlage der Links auf die Dependabot-Liste.
    # abgerufen_utc und abruf_branch (03.10.2026, Codex-Audit): Startzeit des letzten Laufs, der die Commits dieses Repos ohne Fehler
    # geholt hat, und der Default-Branch, für den das gilt. Vom Sammler gesetzt, nie aus Autordaten; Grundlage für since je Repo. Ein
    # Abrufzustand, kein vorberechnetes Aggregat. NULL heisst nie vollständig gelesen: der nächste Lauf holt das Repo ganz.
    # import_seite und import_start (11.10.2026, Codex-Audit Befund 3): ein Erstimport über mehrere Läufe, die nächste Seite der Commitliste und
    # der Beginn des ersten Teils; NULL, sobald das Repo ganz gelesen ist. Ein Abrufzustand wie abgerufen_utc. import_kopf und import_id: der beim
    # Start festgehaltene Kopf, gegen den der Import blättert, und die GitHub-ID, für die er gilt (Bestätigungslauf 11.10.2026).
    "repos": [("id", "INTEGER"), ("fehler", "TEXT"), ("fehler_utc", "TEXT"), ("html_url", "TEXT"), ("abgerufen_utc", "TEXT"), ("abruf_branch", "TEXT"),
              ("import_seite", "INTEGER"), ("import_start", "TEXT"), ("import_kopf", "TEXT"), ("import_id", "INTEGER")],
    # kopf_sha (03.10.2026): der Kopf-Commit, für den letzter_utc gilt. letzter_utc wird beim ersten Sehen eines Kopfs als
    # min(Autordatum, jetzt) gesetzt und bleibt, solange der Kopf derselbe ist: ein Zukunftsdatum hält die Zweigfrist nicht auf.
    "zweige": [("kopf_sha", "TEXT")],
    # Messvertrag (03.10.2026, Codex-Audit): ci und sicherheit tragen das Ergebnis der letzten gelungenen Messung, *_gemessen deren
    # Zeitpunkt, *_fehler den Fehler der letzten Abfrage (NULL, wenn sie gelang). Ein Fehler ersetzt keinen bekannten Wert.
    # Die Messzeitpunkte vor den Fehlern: die Umstellung beim Anlegen von *_fehler schreibt schon in *_gemessen.
    "pruefungen": [("ci_gemessen", "TEXT"), ("sicher_gemessen", "TEXT"), ("ci_fehler", "TEXT"), ("sicher_fehler", "TEXT")],
    # Einzelheiten je Dependabot-Warnung (25.09.2026) für das Aufklappdetail im Block Handlungsbedarf: Titel (security_advisory.summary),
    # Geltungsbereich (dependency.scope: development oder runtime), geöffnet am (created_at), betroffener Versionsbereich, GHSA und CVE.
    # teilweise: Zahl der Repos, die GitHub im Lauf abwies (26.09.2026); 0 voller Erfolg, mehr ein Teilerfolg. NULL im Altbestand.
    "laeufe": [("teilweise", "INTEGER")],
    "warnungen": [("summary", "TEXT"), ("scope", "TEXT"), ("geoeffnet", "TEXT"), ("bereich", "TEXT"), ("ghsa", "TEXT"), ("cve", "TEXT")],
}


# Spalten, die entfallen sind und bei der nächsten schreibenden Verbindung verschwinden (25.09.2026): die vier aus dem am 25.09.2026
# zurückgenommenen Teil C (alle Zweige, Squash, Rebase) und die zwei Summenspalten der Codezeilen, die seit der Tabelle commit_dateien
# aus den Zeilen je Datei gerechnet werden. DROP COLUMN braucht SQLite 3.35, im Bild ist 3.46.1.
ENTFALLEN = {"commits": ("herkunft", "zweig", "squash_pr", "dublette_von", "zeilen_plus", "zeilen_minus")}

# Codezeilen zählen geschriebenen Code, keine Datenmasse (Regel vom 25.09.2026, gemessen an den 25 grössten Dateien der Woche: die
# fünf Regionen-JSON von velotrip trugen über 80'000 von 109'552 Zeilen). Die Regel gilt für jeden Betreiber und wird bei jeder
# Abfrage aus commit_dateien gerechnet, damit sie sich ohne neuen Abruf ändern lässt.
LOCKDATEIEN = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "Cargo.lock", "poetry.lock", "composer.lock", "Gemfile.lock", "go.sum"}
DATEN_ENDUNGEN = (".json", ".geojson", ".csv", ".tsv", ".xml", ".svg", ".map", ".snap")
JSON_KONFIGURATION = re.compile(r"(package|tsconfig[\w.-]*|jsconfig[\w.-]*)\.json")
VERZEICHNISSE = {"dist", "build", "vendor", "node_modules", "coverage", ".next", "__snapshots__"}
EINZEL_MAX = 3000   # mehr geänderte Zeilen in einer Datei und einem Commit sind Datenmasse, keine Handarbeit


def zaehlt(pfad, plus, minus):
    """1, wenn die Zeilen dieser Datei in diesem Commit als geschriebener Code zählen, sonst 0. Auch als SQL-Funktion zaehlt()."""
    teile = (pfad or "").split("/")
    name = teile[-1]
    if name in LOCKDATEIEN or any(t in VERZEICHNISSE for t in teile[:-1]) or ".min." in name.lower():
        return 0
    if name.lower().endswith(DATEN_ENDUNGEN) and not JSON_KONFIGURATION.fullmatch(name):
        return 0
    return int((plus or 0) + (minus or 0) <= EINZEL_MAX)


# Eine Frist je Lauf (26.09.2026, Codex-Review): 600 s ab Laufbeginn für Sperrwartezeit UND Sammelarbeit, für Timer- und Knopflauf gleich.
# Vorher wartete flock ohne Grenze; der Timer hatte TimeoutStartSec=600, der Knopflauf gar nichts, und blieb dann für immer «läuft».
def zahl(name, vorgabe, minimum=0, ueber=False, ganz=False, maximum=None):
    """Eine Zahl aus der Umgebung, leer heisst Vorgabe. Ungültig (kein Zahltext, nan, inf, unter dem Minimum) ist ein sichtbarer Fehler der
    Einrichtung beim Start, Exit 1 mit Name und Wert (Codex-Audit 11.10.2026, Befund 12): vorher schalteten nan und -1 den Takt still ab, und
    inf brach ihn erst beim Schlafen mit OverflowError."""
    roh = os.environ.get(name, "").strip()
    if not roh:
        return vorgabe
    try:
        w = int(roh) if ganz else float(roh)
    except ValueError:
        w = None
    if w is None or not math.isfinite(w) or w < minimum or (ueber and w == minimum) or (maximum is not None and w > maximum):
        grenze = f"über {minimum}" if ueber else f"ab {minimum}" + (f" bis {maximum}" if maximum is not None else "")
        raise SystemExit(f"gitpuls: {name}={roh!r} ist ungültig, erwartet {'eine ganze Zahl' if ganz else 'eine endliche Zahl'} {grenze}")
    return w


# GITPULS_LAUFFRIST_S verkürzt die Frist für Tests und die Nachstellung; live bleibt 600.
LAUFFRIST_S = zahl("GITPULS_LAUFFRIST_S", 600.0, ueber=True)


class FristAbgelaufen(Exception):
    """Die Frist des Laufs ist abgelaufen, beim Warten auf die Sperre oder mitten im Werk. Der Text ist die Meldung für lauf.json."""


def frist(start=None):
    """Das monotone Ende eines Laufs, der jetzt (oder bei `start`) beginnt."""
    return (time.monotonic() if start is None else start) + LAUFFRIST_S


def frist_pruefen(ende, wo):
    if time.monotonic() >= ende:
        raise FristAbgelaufen(f"Frist von {LAUFFRIST_S:.0f} s abgelaufen, {wo}")


def _halter(sperre):
    """(PID, Sekunden) des Schreibers, der die Sperre hält, aus der Sperrdatei; (None, None), wenn nicht lesbar."""
    try:
        pid, seit = sperre.read_text().split()
        return int(pid), max(0, int(time.time() - float(seit)))
    except (OSError, ValueError):
        return None, None


@contextmanager
def schreibsperre(ende=None):
    """Ein Schreiber zur Zeit (25.09.2026): jeder Sammellauf nimmt diese Dateisperre im Volume, bevor er schreibt. Ein
    zweiter wartet, bis der erste fertig ist, statt nach 5 s an «database is locked» zu scheitern. Gemessen am 25.09.2026 um 08:00:
    Lauf per Knopf um 08:00:01 und Timer-Lauf um 08:00:11 schrieben gleichzeitig, der Timer-Lauf endete mit Exit 1. flock gilt über
    Containergrenzen, weil beide Container dieselbe Datei auf demselben Kernel öffnen; die Sperre fällt mit dem Prozess, auch bei
    einem Abbruch (SIGKILL eingeschlossen, der Kernel schliesst die Datei). Seit dem 26.09.2026 wartet der zweite höchstens bis `ende`
    (monoton, frist()) und endet dann mit FristAbgelaufen «Ein anderer Schreiber hält die Sperre seit N s»; der Halter schreibt dafür
    PID und Beginn in die Sperrdatei."""
    ende = frist() if ende is None else ende
    DB.parent.mkdir(parents=True, exist_ok=True)
    sperre = DB.parent / ".schreiber.sperre"
    with open(sperre, "a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Ein anderer Schreiber läuft, warte auf ihn", flush=True)
            gewartet = time.monotonic()
            while True:
                if time.monotonic() >= ende:
                    pid, seit = _halter(sperre)
                    raise FristAbgelaufen(f"Ein anderer Schreiber{f' (PID {pid})' if pid else ''} hält die Sperre seit {seit if seit is not None else '?'} s; "
                                          f"nach {time.monotonic() - gewartet:.0f} s Warten ist die Frist des Laufs abgelaufen, der Lauf endet ohne zu schreiben")
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(min(0.5, max(0.0, ende - time.monotonic())))
        f.seek(0)
        f.truncate()
        f.write(f"{os.getpid()} {time.time():.0f}")
        f.flush()
        yield


def verbinden(schreibend=False):
    if schreibend:
        DB.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB)
        c.executescript(SCHEMA.read_text())
        for tabelle, spalten in NACHTRAEGE.items():
            vorhanden = {r[1] for r in c.execute(f"PRAGMA table_info({tabelle})")}
            for name, typ in spalten:
                if name not in vorhanden:
                    c.execute(f"ALTER TABLE {tabelle} ADD COLUMN {name} {typ}")
                    if name == "dateien_versuche":
                        # Einmal, mit der Spalte: bis zum Audit vom 25.09.2026 speicherte ein 404 oder 422 eine leere Dateiliste als
                        # geladen. Commits ohne jede Dateizeile werden neu geprüft und dabei in «geladen, leer» und «nicht verfügbar» getrennt.
                        c.execute("UPDATE commits SET dateien_geladen = NULL WHERE dateien_geladen = 1 "
                                  "AND NOT EXISTS (SELECT 1 FROM commit_dateien d WHERE d.sha = commits.sha)")
                    elif name in ("ci_fehler", "sicher_fehler"):
                        # Einmal, mit der Spalte (03.10.2026): bis dahin stand ein Fehler als Zustand in ci oder sicherheit. Er wird
                        # zum Fehler, der Wert zu «unbekannt»; ein gemessener Zustand bekommt seinen Messzeitpunkt aus gesehen.
                        wert, fehler_, gemessen = ("ci", "ci_fehler", "ci_gemessen") if name == "ci_fehler" else ("sicherheit", "sicher_fehler", "sicher_gemessen")
                        c.execute(f"UPDATE pruefungen SET {fehler_} = {wert}, {wert} = 'unbekannt' WHERE {wert} IN ('kein_recht', 'unbekannt')")
                        c.execute(f"UPDATE pruefungen SET {gemessen} = gesehen WHERE {fehler_} IS NULL")
        for tabelle, spalten in ENTFALLEN.items():
            vorhanden = {r[1] for r in c.execute(f"PRAGMA table_info({tabelle})")}
            for name in spalten:
                if name in vorhanden:
                    c.execute(f"ALTER TABLE {tabelle} DROP COLUMN {name}")
        c.commit()
    else:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.create_function("zaehlt", 3, zaehlt, deterministic=True)
    c.row_factory = sqlite3.Row
    return c
