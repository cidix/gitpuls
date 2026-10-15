"""Schreibweisen nach Format-Locale (26.09.2026): app/format.py gegen von Hand geschriebene Erwartungen aus den CLDR-Regeln, je für
de-CH, de-DE, en-US und en-GB. Keine Erwartung wird aus dem Formatmodul oder aus Babel erzeugt: eine Erwartung aus derselben Quelle wie
der Fehler belegt nichts (Harte Grenze der Projektnotiz). Dazu der Vorrang GITPULS_FORMAT, Accept-Language, Vorgabe; eine fremde
Browsersprache fällt auf die Vorgabe; und die Falle mit html.escape in den data-Attributen des Ablese-Zeigers.
python3 -m tests.test_format  (auf dem Mac mit .venv/bin/python, Babel steht in requirements.txt)"""
import html
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

os.environ["GITPULS_DB"] = os.path.join(tempfile.mkdtemp(), "format.db")
os.environ.setdefault("GITPULS_ADRESSEN", "dev@fernwerk-studio.example")
os.environ.setdefault("GITPULS_SPRACHE", "de")
os.environ.pop("GITPULS_FORMAT", None)
os.environ["TZ"] = "Europe/Zurich"   # die Zone setzt der Test selbst (10.10.2026, Audit S28): grün unter jeder TZ der Shell
from app import format as fmt  # noqa: E402
from app import web  # noqa: E402

WURZEL = Path(__file__).resolve().parent.parent
NB, SCHMAL, MINUS = " ", " ", "−"
d, bis, anders, zeit = date(2026, 9, 25), (date(2026, 9, 15), date(2026, 9, 21)), (date(2026, 9, 28), date(2026, 10, 4)), datetime(2026, 9, 25, 17, 3)

# Je Locale die Erwartungen nach CLDR, von Hand: Gruppierung (de-CH U+2019, de-DE Punkt, en Komma), Dezimalzeichen, Prozent (de-DE mit
# geschütztem Abstand, de-CH und en ohne), Minus als U+2212, Datum kurz (de numerisch dd.MM., en Monatskürzel), mit Jahr, mit Wochentag,
# Spanne gleicher und verschiedener Monat (U+202F um den Strich), Uhrzeit (en-US 12 Stunden mit U+202F vor PM) und Datum mit Zeit.
ERWARTET = {
    "de_CH": ["1’234’567", "26.1", "1’234.5", "34%", MINUS + "23%", "+12%", MINUS + "476", "25.09.", "25.09.2026", f"Fr.,{NB}25.9.",
              "15.–21.09.", f"28.09.{SCHMAL}–{SCHMAL}04.10.", "17:03", f"25.09.2026,{NB}17:03"],
    "de_DE": ["1.234.567", "26,1", "1.234,5", f"34{NB}%", f"{MINUS}23{NB}%", f"+12{NB}%", MINUS + "476", "25.09.", "25.09.2026", f"Fr.,{NB}25.9.",
              "15.–21.09.", f"28.09.{SCHMAL}–{SCHMAL}04.10.", "17:03", f"25.09.2026,{NB}17:03"],
    "en_US": ["1,234,567", "26.1", "1,234.5", "34%", MINUS + "23%", "+12%", MINUS + "476", f"Sep{NB}25", f"Sep{NB}25,{NB}2026", f"Fri,{NB}Sep{NB}25",
              f"Sep{NB}15{SCHMAL}–{SCHMAL}21", f"Sep{NB}28{SCHMAL}–{SCHMAL}Oct{NB}4", f"5:03{SCHMAL}PM", f"Sep{NB}25,{NB}2026,{NB}5:03{SCHMAL}PM"],
    "en_GB": ["1,234,567", "26.1", "1,234.5", "34%", MINUS + "23%", "+12%", MINUS + "476", f"25{NB}Sept", f"25{NB}Sept{NB}2026", f"Fri{NB}25{NB}Sept",
              f"15–21{NB}Sept", f"28{NB}Sept{SCHMAL}–{SCHMAL}4{NB}Oct", "17:03", f"25{NB}Sept{NB}2026,{NB}17:03"],
}
for tag, soll in ERWARTET.items():
    with fmt.gilt(fmt.locale_von(tag)):
        ist = [fmt.zahl(1234567), fmt.dezimal(26.1), fmt.dezimal(1234.5), fmt.prozent(34), fmt.prozent(-23), fmt.prozent(12, vorzeichen=True),
               fmt.zahl(-476), fmt.datum(d), fmt.datum_jahr(d), fmt.wochentag_datum(d), fmt.spanne(*bis), fmt.spanne(*anders), fmt.uhrzeit(zeit),
               fmt.zeitpunkt(zeit)]
        assert ist == soll, (tag, [(i, a, b) for i, (a, b) in enumerate(zip(ist, soll)) if a != b])
        # Nie ein Bindestrich als Minus, nie ein normales Leerzeichen in einem Wert (e).
        assert all("-" not in x and " " not in x for x in ist), (tag, ist)
        assert fmt.zeilen(45997, 11300)[1].startswith(MINUS) and fmt.zeilen(0, 0) == ("+0", MINUS + "0") and fmt.prozent(0, vorzeichen=True).startswith("0")
        assert fmt.spanne(d, d) == fmt.datum(d), "gleicher Tag: das einfache Datum statt der Rückfallform"

# (c) Vorrang: GITPULS_FORMAT, sofern seine Sprache die Oberflächensprache ist; sonst der erste passende Eintrag aus Accept-Language
# (nach q), sonst de-DE oder en-US. Eine fremde Browsersprache fällt auf die Vorgabe.
L = lambda loc: str(loc)
ch = fmt.locale_von("de-CH")
assert L(fmt.waehlen("de", "", betreiber=ch)) == "de_CH" and L(fmt.waehlen("de", "de-AT,de;q=0.9", betreiber=ch)) == "de_CH", "Betreiber übersteuert"
assert L(fmt.waehlen("en", "en-GB,en;q=0.8", betreiber=ch)) == "en_GB", "der Betreiber gilt nur für seine Sprache"
assert L(fmt.waehlen("de", "de-AT,de-CH;q=0.9,en;q=0.8", betreiber=False)) == "de_AT"
assert L(fmt.waehlen("de", "en-US,de-DE;q=0.7,de-CH;q=0.9", betreiber=False)) == "de_CH", "nach q geordnet, nicht nach Stellung"
assert L(fmt.waehlen("en", "de-CH,en-GB;q=0.5", betreiber=False)) == "en_GB", "der erste Eintrag der aktiven Sprache"
assert L(fmt.waehlen("de", "fr-FR,it;q=0.8", betreiber=False)) == "de_DE" and L(fmt.waehlen("en", "fr-FR", betreiber=False)) == "en_US", "fremd: Vorgabe"
assert L(fmt.waehlen("de", "de-XX-kaputt,,;q=x", betreiber=False)) == "de_DE", "unlesbar: Vorgabe"
# q-Werte nach RFC 9110 (26.09.2026, Codex-Review): q=0 schliesst aus, ein ungültiger oder nicht endlicher Wert verwirft den Eintrag, gleiches q
# behält die Reihenfolge im Kopf.
assert L(fmt.waehlen("en", "en-GB;q=0", betreiber=False)) == "en_US", "q=0 heisst nicht gewünscht"
assert L(fmt.waehlen("en", "en-GB;q=garbage", betreiber=False)) == "en_US", "ungültiges q verwirft den Eintrag"
assert L(fmt.waehlen("en", "en-GB;q=nan,en-US;q=1", betreiber=False)) == "en_US", "nan ist kein q"
assert L(fmt.waehlen("en", "en-GB;q=0.8,en-US;q=0.8", betreiber=False)) == "en_GB" and L(fmt.waehlen("en", "en-US;q=0.8,en-GB;q=0.8", betreiber=False)) == "en_US", "gleiches q: Reihenfolge"
assert [L(fmt.waehlen("en", h, betreiber=False)) for h in ("en-GB;q=1.5", "en-GB;q=-1", "en-GB;q=0.0001", "en-GB;q=inf", "en-GB;q=1.000", "en-GB;q=0.001")] == \
    ["en_US", "en_US", "en_US", "en_US", "en_GB", "en_GB"]
assert fmt.accept_language("de-CH;q=0.5, en-GB;q=0, fr;q=x, de;q=0.9") == ["de", "de-CH"]
assert fmt.einstellung("de-CH") and fmt.einstellung("") is None
for falsch in ("fr-FR", "xx-YY", "de-CH;"):
    try:
        fmt.einstellung(falsch)
        raise AssertionError(falsch)
    except SystemExit as e:
        assert "GITPULS_FORMAT" in str(e), e
# GITPULS_FORMAT wirkt im laufenden Prozess, ein ungültiger Wert bricht den Start ab.
env = {k: v for k, v in os.environ.items() if k != "GITPULS_FORMAT"}
lauf = lambda wert: subprocess.run([sys.executable, "-c", "from app import format as f; print(f.BETREIBER)"], cwd=WURZEL, env=env | {"GITPULS_FORMAT": wert},
                                   capture_output=True, text=True)
assert lauf("de-CH").stdout.strip() == "de_CH" and lauf("fr").returncode == 1 and "GITPULS_FORMAT='fr'" in lauf("fr").stderr

# Die Seite: Accept-Language wählt die Schreibweise, die Oberflächensprache bleibt; das Skript bekommt die Locale für Intl.
from app import db  # noqa: E402
c = db.verbinden(schreibend=True)
c.execute("INSERT INTO laeufe (gestartet_utc, beendet_utc, neu) VALUES ('2026-09-25T15:00:00Z', '2026-09-25T15:03:00Z', 1)")
c.execute("INSERT INTO repos (repo, default_branch, sprache, pushed_at, gesehen, id) VALUES ('fernwerk-studio/a', 'main', 'Python', '2026-09-25T15:00:00Z', '2026-09-25T15:00:00Z', 1)")
c.executemany("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff, eltern) VALUES (?, 'fernwerk-studio/a', ?, 'dev@fernwerk-studio.example', 'x', 1)",
              [(f"s{i}", f"2026-09-{1 + i % 25:02d}T12:{i % 60:02d}:00Z") for i in range(1234)])
c.commit(); c.close()
jetzt = datetime(2026, 9, 25, 17, 30, tzinfo=web.TZ)
for sprache, accept, stichwerte in (("de", "de-CH", ["1’234", "GitHub-Daten vom 25.09.2026, 17:03 Uhr"]),
                                    ("de", "de-DE,de;q=0.9", ["1.234", "GitHub-Daten vom 25.09.2026, 17:03 Uhr"]),
                                    ("en", "en-US", ["1,234", "GitHub data from Sep 25, 2026, 5:03 PM"]),
                                    ("en", "en-GB,en;q=0.5", ["1,234", "GitHub data from 25 Sept 2026, 17:03"])):
    s = web.seite(jetzt=jetzt, sprache=sprache, accept=accept)
    assert s.count("blockfehler") == 0 and all(w in s for w in stichwerte), (sprache, accept, [w for w in stichwerte if w not in s])
    assert f'"locale": "{fmt.waehlen(sprache, accept).language}-{fmt.waehlen(sprache, accept).territory}"' in s, (sprache, accept)

# (g) Die Falle mit html.escape in data-stellen: ein Apostroph als Gruppierung würde zu «&#x27;», dessen Semikolon das Trennzeichen der
# Stellen ist. Der Browser löst das Attribut vor dem Trennen auf; so muss jede Stelle ihre sechs Felder behalten, in jeder Locale.
def felder(k):
    roh = k[k.index('data-stellen="') + 14:]
    roh = roh[:roh.index('"')]
    return [e.split("|") for e in html.unescape(roh).split(";")], roh
for tag in ERWARTET:
    with fmt.gilt(fmt.locale_von(tag)):
        stellen, _ = felder(web.kurve([1234, 5678901], "a", "b", stellen=["x", "y"]))
    assert [len(e) for e in stellen] == [6, 6] and stellen[0][2] == ERWARTET[tag][0][:5], (tag, stellen)   # 1234 in der Gruppierung der Locale
echt = fmt.zahl
fmt.zahl = lambda n, vorzeichen=False: f"{n:,}".replace(",", "'")   # eine Locale mit ASCII-Apostroph, wie das alte Schweizer Format
try:
    stellen, roh = felder(web.kurve([1234, 5678901], "a", "b", stellen=["x", "y"]))
finally:
    fmt.zahl = echt
assert "&#x27;" in roh and [len(e) for e in stellen] == [6, 6] and stellen[0][2] == "1'234", (roh, stellen)
assert len(roh.split(";")) > 2, "vor dem Auflösen getrennt zerfiele die Stelle: genau die Falle"
# (3a) Keine Schreibweise mehr ausserhalb von app/format.py: in den Texttabellen gar keine Formatangabe, in web.py nur ISO-Zeitstempel für
# Datenbank und Protokoll, Koordinaten im SVG und die zwei Startmeldungen der Zonenprüfung an den Betreiber (Log, keine Oberfläche).
import re  # noqa: E402
ANZEIGE = re.compile(r"%H:%M|%d\.%m|\{[^{}]*:[+,]?\.\d+f\}|\{[^{}]*:[+,]d?\}|\.replace\(\"-\", |toLocale|hourCycle|\"\\u2212|\"−")
ERLAUBT = re.compile(r'strftime\("%Y-%m-%dT%H:%M:%SZ"\)|%Y-%m-%dT%H:%M:%SZ|%Y-%m-%d %H:%M:%S|Zeitzone \{getattr|\{[xy]\(|\{H\}|style="|stroke-|points=|x1=|:\.1f\}%|timeStyle|\{-ab:|\{a:\.3f\}|\{100 - a:')
for datei in ("app/texte/de.py", "app/texte/en.py", "app/web.py"):
    funde = [(i + 1, z.strip()[:100]) for i, z in enumerate((WURZEL / datei).read_text().split("\n")) if ANZEIGE.search(z) and not ERLAUBT.search(z)]
    assert not funde, (datei, funde)
print("ok: CLDR de-CH, de-DE, en-US, en-GB von Hand; Minus U+2212 und geschützte Leerzeichen; Vorrang GITPULS_FORMAT, Accept-Language, Vorgabe; "
      "fremde Browsersprache; Seite je Locale; data-stellen mit &#x27;")
