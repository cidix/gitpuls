"""Gegenprobe: Woche, Vorwoche zum selben Zeitpunkt und Monat der Kacheln (app/web.py::kachelvarianten) gegen eigenes SQL über die
Rohzeilen. python3 -m tests.gegenprobe. Bis 10.10.2026 prüfte sie app/kennzahlen.py::kopf(), das mit /api/kopf entfallen ist; die
Grenzfälle sind dieselben, «jetzt» steht auf Mittwoch 23:59:59.5, damit die Vorwoche wie bisher den ganzen Mittwoch zählt.

Bis 13.09.2026 druckte dieses Modul die drei Werte nur aus und enthielt keine Zusicherung; «gegenprobe grün» hiess
Exit 0 und belegte nichts. Bei einer Vorwoche ohne Commits brach es mit Division durch null ab.
Die Erwartung zählt Rohzeilen in eigenem SQL über UTC-Grenzen aus Zürcher Mitternacht, nie über date(…,'localtime')
und nie über tageszaehlung: sonst stammte sie aus derselben Quelle wie der geprüfte Wert."""
import os
import sqlite3
import tempfile
import time
from datetime import date, datetime, time as uhrzeit, timedelta, timezone
from zoneinfo import ZoneInfo

os.environ["TZ"] = "Europe/Zurich"   # tageszaehlung rechnet mit SQLite 'localtime', das TZ des Prozesses liest
time.tzset()
os.environ["GITPULS_DB"] = os.path.join(tempfile.mkdtemp(), "gegenprobe.db")   # vor dem Import, db.DB liest die Variable beim Laden
os.environ.setdefault("GITPULS_ADRESSEN", "alt@fernwerk-studio.example,mail@fernwerk-studio.example,dev@fernwerk-studio.example")   # kein Autor-Default mehr im Code (Codex-Befund 20.09.2026)
os.environ.setdefault("GITPULS_SPRACHE", "de")
os.environ["GITPULS_FORMAT"] = "de-CH"   # feste Schreibweise für die Vergleichszeile   # die bisherigen Prüfungen laufen auf Deutsch, die Vorgabe ist seit 25.09.2026 en
from app import db, web  # noqa: E402

TZ = ZoneInfo("Europe/Zurich")
EIGENE = "dev@fernwerk-studio.example"
heute = date(2026, 9, 16)   # Mittwoch
zh = lambda d, h=12, m=0, s=0: datetime.combine(d, uhrzeit(h, m, s), TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
mitternacht = lambda d: datetime.combine(d, uhrzeit(0), TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def befuellen(zeilen):
    """Neue Datenbank mit genau diesen Rohzeilen (Zeitstempel UTC, Adresse)."""
    db.DB = type(db.DB)(tempfile.mkdtemp()) / "gegenprobe.db"
    c = db.verbinden(schreibend=True)
    for i, (t, adresse) in enumerate(zeilen):
        c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES (?, 'fernwerk-studio/probe', ?, ?)", (f"g{i}", t, adresse))
    c.commit(); c.close()


def roh(von, bis):
    """Eigene Commits mit Zürcher Datum in [von, bis], als halboffenes UTC-Intervall über die Rohzeilen."""
    c = sqlite3.connect(f"file:{db.DB}?mode=ro", uri=True)
    n = c.execute(f"SELECT COUNT(*) FROM commits WHERE zeitstempel_utc >= ? AND zeitstempel_utc < ? AND autor_adresse IN ({','.join('?' * len(web.ADRESSEN))})",
                  (mitternacht(von), mitternacht(bis + timedelta(days=1)), *web.ADRESSEN)).fetchone()[0]
    c.close()
    return n


JETZT = datetime.combine(heute, uhrzeit(23, 59, 59, 500000), TZ)


def gemessen():
    """(Woche, Vorwoche zum selben Zeitpunkt, Monat, Vergleichszeile der Woche) aus denselben Funktionen wie die Seite."""
    with db.verbinden() as c:
        v = web.kachelvarianten(c, heute, JETZT)
    woche, monat = v["k2"][0][1], v["k3"][0][1]
    return woche["n"], woche["vor"], monat["n"], woche["zeile1"]


montag, wt = heute - timedelta(days=heute.weekday()), heute.weekday()

# 1: Commits je auf beiden Seiten jeder Grenze in Zürcher Zeit. Montag 00:30 zählt zur Woche, Sonntag 23:59 davor nicht; der
# Mittwoch der Vorwoche bis 23:59:59 zählt, Donnerstag 00:00 nicht mehr; der 01.09. 00:00 zählt zum Monat, der 31.08. 23:59:59
# nicht; eine fremde Adresse und ein Commit von morgen zählen nirgends.
befuellen([(zh(heute, 9), EIGENE), (zh(heute, 11), EIGENE), (zh(date(2026, 9, 14), 0, 30), EIGENE), (zh(date(2026, 9, 13), 23, 59), EIGENE),
           (zh(date(2026, 9, 9), 23, 59, 59), EIGENE), (zh(date(2026, 9, 10), 0), EIGENE), (zh(date(2026, 9, 7), 8), EIGENE),
           (zh(date(2026, 9, 1), 0), EIGENE), (zh(date(2026, 8, 31), 23, 59, 59), EIGENE),
           (zh(heute, 10), "fremd@example.org"), (zh(heute + timedelta(days=1), 10), EIGENE)])
woche, vor, monat = roh(montag, heute), roh(montag - timedelta(weeks=1), heute - timedelta(weeks=1)), roh(heute.replace(day=1), heute)
assert (woche, vor, monat) == (3, 2, 8), ("Testdaten tragen die Grenzfälle nicht mehr", woche, vor, monat)
k = gemessen()
assert k[:3] == (woche, vor, monat), (k, woche, vor, monat)
assert k[3] == f"Vorwoche zum selben Zeitpunkt: {vor}, +{round((woche - vor) * 100 / vor)}%", k[3]

# 2: Vorwoche ohne Commits. Die frühere Ausgabe teilte hier durch null; der Vergleich liefert jetzt einen sprechenden Wert.
befuellen([(zh(heute, 9), EIGENE), (zh(date(2026, 9, 14), 10), EIGENE), (zh(date(2026, 9, 3), 10), EIGENE)])
woche, vor = roh(montag, heute), roh(montag - timedelta(weeks=1), heute - timedelta(weeks=1))
assert (woche, vor) == (2, 0), (woche, vor)
k = gemessen()
assert k[:2] == (woche, vor), k
assert k[3] == "Vorwoche zum selben Zeitpunkt: 0", ("ohne Vorwoche kein Prozentwert", k[3])

print("ok: Woche, Vorwoche zum selben Zeitpunkt und Monat gegen eigenes SQL über die Rohzeilen, Zürcher Grenzen, fremde Adresse, Zukunftscommit, Vorwoche ohne Commits")
