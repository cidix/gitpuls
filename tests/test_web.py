"""Kleinste Prüfung: die Seite rendert in den drei Serverzuständen (Datei fehlt, älter als 15 Minuten,
frisch) und der Lagesatz nennt Abweichungen zuerst. python3 -m tests.test_web"""
import json
import os
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

os.environ["GITPULS_DB"] = os.path.join(tempfile.mkdtemp(), "probe.db")   # vor dem Import, db.DB liest die Variable beim Laden
os.environ.setdefault("GITPULS_ADRESSEN", "alt@fernwerk-studio.example,mail@fernwerk-studio.example,dev@fernwerk-studio.example")   # kein Autor-Default mehr im Code (Codex-Befund 20.09.2026)
os.environ.setdefault("GITPULS_SPRACHE", "de")   # die bisherigen Prüfungen laufen auf Deutsch, die Vorgabe ist seit 25.09.2026 en
os.environ["GITPULS_FORMAT"] = "de-CH"   # Schreibweisen wie die eigene Instanz (26.09.2026); die übrigen Locales prüft tests/test_format.py
os.environ["TZ"] = "Europe/Zurich"   # die Zone setzt der Test selbst (10.10.2026, Audit S28): grün unter jeder TZ der Shell
from app import db, sprache, web  # noqa: E402
from tests.sammlerprobe import lauf  # noqa: E402

z = lambda h: (jetzt - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")   # Laufzeitpunkt vor h Stunden, fest ab jetzt; die Läufe im Test liegen zeitlich aufsteigend

db.verbinden(schreibend=True).close()   # leeres Schema, damit seite() ohne Sammellauf rendert
beispiel = json.loads(Path(__file__).with_name("server.beispiel.json").read_text())   # erste echte server.json vom 08.09.2026
jetzt = datetime.now(timezone.utc)
# Eine Zeitquelle für den ganzen Test: jede Seite rendert gegen dasselbe jetzt, gegen das auch die Testdaten liegen.
jetzt_zh = jetzt.astimezone(web.TZ)
seite = lambda *a, **k: web.seite(*a, **({"jetzt": jetzt_zh} | k))

# 1: Datei fehlt
s = seite()
assert "Noch kein Serverstand" in s and "<html" in s, "Seite ohne server.json"

# 2: frisch, mit den Abweichungen des Beispiels (Neustart nötig, Timer gitpuls-server fehlt)
beispiel["erhoben_utc"] = web.iso_utc(jetzt)
for d_ in beispiel["dienste"]:   # ein Deploy-Stand gilt seit 10.10.2026 nach zwei Takten als veraltet (Audit S14); das Beispiel ist frisch
    if d_.get("deploy"):
        d_["deploy"]["gemessen"] = web.iso_utc(jetzt)
web.SERVER.write_text(json.dumps(beispiel))
s = seite()
for wort in ("Kundenseite", "Preisbeobachter", "gitpuls", "Trainingslog", "Beispiel-VPS 2 vCPU",
             ">Arbeitsspeicher</span><span>32%, 1.3 von 4.1 GB<span class=\"fbalken\"",
             ">Platte</span><span>4%, 5.7 von 134 GB<span class=\"fbalken\"",
             ">Docker</span><span>29.7.2", ">Pakete</span><span>keine offen", "Berichtsgenerator-Generator", "auf f20a625, gleich Default-Branch",
             "1.102.3", "443, 8443, 8444, 8446", "6.12.101+deb13-amd64", "ufw nicht installiert", "trixie", "unattended-upgrades aktiv"):
    assert wort in s, wort
satz, ab = web.server_lage(beispiel)
assert satz.startswith("gitpuls: Timer gitpuls-server fehlt. Neustart nötig. Sonst laufen alle vier Dienste"), satz
assert "älter als" not in s and '>Neustart</span><span><span class="glut">nötig' in s

# 2b: ohne Abweichungen der Normalsatz, ohne Warnfarbe; mit gestopptem Container und gescheitertem Lauf zuerst die Abweichung
beispiel["zustand"]["neustart_noetig"] = False
beispiel["dienste"][2]["timer"] = [t for t in beispiel["dienste"][2]["timer"] if t["status"] != "fehlt"]
satz, ab = web.server_lage(beispiel)
assert satz == "Alle vier Dienste laufen, letzter Lauf überall sauber beendet, kein Neustart nötig." and not ab, satz
web.SERVER.write_text(json.dumps(beispiel))
assert "glut" not in web.server_html(beispiel, jetzt)
beispiel["dienste"][3]["container"][0]["status"] = "exited"
beispiel["dienste"][1]["timer"][0].update(ergebnis="exit-code", exit=1)
satz, ab = web.server_lage(beispiel)
assert satz.startswith("Preisbeobachter: Lauf beobachter-lauf gescheitert, exit-code, Exit 1. Trainingslog: Container trainingslog-trainingslog-1 exited. Sonst laufen drei von vier Diensten"), satz
h = web.server_html(beispiel, jetzt)
assert 'class="glut">Container trainingslog-trainingslog-1 exited' in h and "gescheitert, exit-code, Exit 1" in h and "gescheitert, exit-code 1</span></span></summary>" in h

# 2c: nach einem Host-Neustart (08.09.2026 05:04) kennt systemd keine Zeiten mehr: kein "Exit 0", kein "läuft seit";
# eine gerade laufende Unit ohne nächsten Termin zeigt "nach dem Lauf" statt einer leeren Zelle
beispiel["dienste"][3]["container"][0]["status"] = "running"
beispiel["dienste"][1]["timer"][0].update(ergebnis="success", exit=0)
beispiel["dienste"][2]["timer"][0].update(status="inactive", gestartet_utc=None, letzter_utc=None, ergebnis="success", exit=0)
beispiel["dienste"][1]["timer"][0].update(status="activating", gestartet_utc=web.iso_utc(jetzt), letzter_utc=None, naechster_utc=None)
beispiel["dienste"][0]["container"][0]["neustarts"] = 2
h = web.server_html(beispiel, jetzt)
sammler = h[h.index("gitpuls-sammler"):h.index("gitpuls-deploy", h.index("gitpuls-sammler"))]
assert "seit Neustart" in sammler and "nicht gelaufen" in sammler and "sauber beendet" not in sammler and "Exit" not in sammler and "läuft seit" not in sammler, sammler
preise = h[h.index("beobachter-lauf: läuft seit"):h.index("nach dem Lauf")]
assert "Exit" not in preise, preise
assert "2× von Docker neu gestartet" in h and "Neustarts" not in h
assert "glut" not in h and "noch nie" not in h
satz, ab = web.server_lage(beispiel)
assert "ein Timer seit dem Neustart noch nicht gelaufen" in satz and "überall sauber" not in satz, satz

# 3: älter als 15 Minuten
beispiel["erhoben_utc"] = web.iso_utc(jetzt - timedelta(minutes=20))
web.SERVER.write_text(json.dumps(beispiel))
s = seite()
assert "vor 20 Min., älter als 15 Minuten" in s and 'class="glut">Stand' in s

# kaputtes JSON: seit 11.10.2026 (Codex-Audit, Befund 6) eine ausgefallene Messung mit Grund, nicht mehr «wie fehlend»
web.SERVER.write_text("{")
s = seite()
assert "Noch kein Serverstand" not in s and "Serverstand nicht lesbar" in s and "JSONDecodeError" in s
web.SERVER.unlink()   # weiter wie bisher ohne Serverstand
# Repos ohne Eintrag im letzten Sammellauf (auf GitHub archiviert, der Sammler holt sie nicht mehr) fallen weg;
# Rückblick und Kacheln zeichnen erst ab dem ersten Tag mit Daten. Die Repos kommen über den Sammlerpfad mit
# nachgebautem GitHub (tests/sammlerprobe.py): ein Lauf vor 90 Stunden listet alt, neu, ruhig, einer vor 60
# Stunden nur noch neu und ruhig.
c = db.verbinden(schreibend=True)
# Testdaten liegen auf 12:00 Uhr eines Zürcher Kalendertages, n Tage vor dem heutigen, nie auf dem UTC-Datum: bis
# 13.09.2026 lag gestern auf «(jetzt - 1 Tag) 12:00Z», zwischen 00:00 und 02:00 Uhr Sommerzeit zwei Zürcher Tage zurück.
zh_mittag = lambda tage: datetime.combine(jetzt_zh.date() - timedelta(days=tage), time(12), web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
gestern = zh_mittag(1)
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES ('s1', 'fernwerk-studio/neu', ?, 'dev@fernwerk-studio.example')", (gestern,))
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES ('s2', 'fernwerk-studio/neu', ?, 'dev@fernwerk-studio.example')", (jetzt.strftime("%Y-%m-%dT%H:%M:%SZ"),))
# Aktiv, aber ohne Commit in der laufenden Woche: genau sieben Zürcher Kalendertage vor heute, 12:00 Uhr. Das liegt an
# jedem Wochentag gleich im Fenster: noch aktiv (SCHWELLEN aktiv 7), in der ISO-Vorwoche, ausserhalb der letzten sieben
# Tage (heute-6 bis heute), im Fenster der sieben Tage davor. Bis 13.09.2026 lag der Commit auf «dem vorigen Sonntag»:
# sonntags sieben Tage zurück, montags gestern; Lagesatz und 7-Tage-Kachel waren sonntags rot.
vor_7_tagen = zh_mittag(7)
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES ('s3', 'fernwerk-studio/ruhig', ?, 'dev@fernwerk-studio.example')", (vor_7_tagen,))
c.commit(); c.close()
assert lauf(z(90), ["fernwerk-studio/alt", "fernwerk-studio/neu", "fernwerk-studio/ruhig"]) and lauf(z(60), ["fernwerk-studio/neu", "fernwerk-studio/ruhig"])
s = seite()
assert "fernwerk-studio/neu" in s and "fernwerk-studio/alt" not in s, "archiviertes Repo fällt weg"
assert "ruhig" in s
assert "Summe 3" in s and 'Serie und Rekorde <span class="lbl">(seit ' in s, "Rückblick ab Datenbeginn"
# Der Lagesatz und sein Anteil («Diese Woche am meisten neu, 100% der Commits.», befund()[0] mit 67%) werden im
# Abschnitt «Kopf der GitHub-Zone» aus den Rohdaten geprüft, dort stehen die Hilfen für gelistete Repos.
# Zeilen nach letztem Commit, neuester zuerst, unabhängig vom Befund; ein aktives Repo mit Befund und 0 Commits in
# der laufenden Woche steht ungedämpft in der Liste, zählt im Kopf aber als ohne; kein blauer Rahmen mehr.
import re
def projekt_meta(s):
    """Metazeile unter dem Titel «Projekte» (26.09.2026): (gesamt, aktiv) aus «14 Repos · 9 diese Woche aktiv» oder «14 repos · 9 active
    this week», Wert in .kwert; der Singular ist Teil der Erwartung (Wortlaut fest, nicht aus den Tabellen)."""
    m = re.search(r'<h3 class="titel">(?:Projekte|Projects)</h3>\n  <p class="delta bezug pkopf"><span class="kwert">(\d+)</span> (Repos?|repos?) · '
                  r'<span class="kwert">(\d+)</span> (diese Woche aktiv|active this week)</p>', s)
    assert m, "Metazeile fehlt"
    n = int(m.group(1))
    assert m.group(2).lower() == ("repo" if n == 1 else "repos"), m.group(0)
    return n, int(m.group(3))
KOPF_DE = '<span class="pkname">Repo</span><span class="pwert pkw"><span class="pspalte">Commits</span></span><span class="pkmikro">30 Tage</span><span class="pkalter">Zuletzt</span><span class="pkbetreff">Betreff</span></p>'


def gruppen_von(s, mit_zustand=False):
    """Die Gruppen der Projektliste aus dem HTML: (Titel, Anzahl, mit Linie, Repos in Reihenfolge[, offen]). Offen ist eine
    Gruppe, die kein Aufklapper ist oder deren details das Attribut open trägt. Anzahl ist die angezeigte Zahl: bei «Ohne Commits
    diese Woche» im Gruppentitel, bei der aktiven Gruppe in der Metazeile. Die aktive Gruppe hat seit 27.09.2026 keinen Titel mehr, nur die
    Kopfzeile der Spalten; sie heisst hier «aktiv». Die Kopfzeile im Aufklapper (nach </summary>) beginnt keine eigene Gruppe."""
    teil = s[s.index('<section class="card projekte">'):s.index('<section class="card rueckblick">')]
    stuecke = re.split(r'(?=<details class="gruppe ruhend")|(?<!</summary>)(?=<p class="pgruppe)', teil)[1:]
    aus = []
    for st in stuecke:
        m = re.match(r'<details class="gruppe ruhend"( open)?><summary class="pgruppe( getrennt)?">([^<]*)<span class="panz kwert">(\d+)</span>'
                     r'|<p class="pgruppe spalten( getrennt)?">' + re.escape(KOPF_DE), st)
        if m.group(3) is None:
            eintrag = ("aktiv", projekt_meta(teil)[1], bool(m.group(5)), re.findall(r'data-repo="([^"]+)"', st))
        else:
            eintrag = (m.group(3), int(m.group(4)), bool(m.group(2)), re.findall(r'data-repo="([^"]+)"', st))
        aus.append(eintrag + ((not st.startswith("<details") or bool(m.group(1))),) if mit_zustand else eintrag)
    return aus
c = db.verbinden(schreibend=True)
c.execute("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc) VALUES ('fernwerk-studio/ruhig', 'wip', 1, 0, ?)", (vor_7_tagen,))
c.commit(); c.close()
s = seite()
assert s.count('<details class="prow"') == 2 and "gedaempft" not in s and s.index('data-repo="fernwerk-studio/neu"') < s.index('data-repo="fernwerk-studio/ruhig"'), "neu (heute) vor ruhig (vor 7 Tagen)"
assert [(t, n, g) for t, n, g, _ in gruppen_von(s)] == [("aktiv", 1, False), ("Ohne Commits diese Woche", 1, True)] and "Repos:" not in s, gruppen_von(s)
assert "hat-befund" not in s and "Zweig wip +1" in s and "Zweig wip in ruhig: 1 Commit vor main" in s
c = db.verbinden(schreibend=True); c.execute("DELETE FROM zweige"); c.commit(); c.close()

# Projektliste (seit 13.09.2026): alle Repos in einer Liste, eine Zeile je Repo, nach letztem Commit, ruhende und
# stillliegende gedämpft am Ende derselben Liste; «ruht» und «still» nur dort, «aktiv» nirgends; der Zweigbefund als
# Kurztext nur in seiner Zeile und im Kopf der Zone; die Mikrokurve ohne Zeiger; das Aufklappdetail trägt auch beim
# ruhenden Repo Wochenkurve, Chips und Betreffzeilen. Jede Erwartung stammt aus den Rohdaten (Laufzeilen, Repos,
# Commits) und eigenem SQL, nie aus projekte() oder karenzgrenze().
def gelistet_roh():
    """Repos nach der dokumentierten Regel: gesehen nicht älter als der letzte Lauf minus 24 Stunden."""
    with db.verbinden() as c2:
        letzter = datetime.fromisoformat(c2.execute("SELECT MAX(gestartet_utc) FROM laeufe").fetchone()[0].replace("Z", "+00:00"))
        return [r for (r,) in c2.execute("SELECT repo FROM repos WHERE gesehen >= ?", ((letzter - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),))]
def letzte_roh():
    with db.verbinden() as c2:
        return dict(c2.execute("SELECT repo, MAX(zeitstempel_utc) FROM commits WHERE autor_adresse = 'dev@fernwerk-studio.example' GROUP BY repo"))
heute_roh = jetzt_zh.date()
tage_roh = lambda iso: (heute_roh - datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(web.TZ).date()).days
zeile_von = lambda s, repo: s[s.index(f'data-repo="{repo}"'):s.index("</details>", s.index(f'data-repo="{repo}"'))]
summary_von = lambda s, repo: zeile_von(s, repo)[:zeile_von(s, repo).index("</summary>")]

# Kopf der GitHub-Zone (13.09.2026, seit 25.09.2026 auf der Kalenderwoche): zuoberst der Lagesatz als eigene Zeile, darunter
# links die Serienzahl und daneben die Rangliste der Woche, darunter der Befundblock mit Titel und einer Zeile je Zweigname
# und Stand. Die Erwartung kommt aus Laufzeilen, Repos, Commits und Zweigen: Serie über Zürcher Kalendertage, Wochenwerte
# der gelisteten Repos ab Montag 00:00 Zürcher Zeit bis jetzt, die Summe über ALLE eigenen Commits desselben Fensters (die
# Zahl der Wochenkachel), offene Zweige. Die Zahlwörter stehen hier, nicht aus web.WORTE.
ZAHLWORT = {1: "Ein", 2: "Zwei", 3: "Drei", 4: "Vier"}
# Lagesätze auf der Seite (25.09.2026): zwischen Zahl und folgendem Wort ein geschütztes Leerzeichen. Eigene Liste, nicht aus den Tabellen.
NB_WOERTER = ("ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn", "elf", "zwölf")
# Erwartung der geschützten Leerzeichen, eigene Umsetzung der zwei Regeln: Zahl oder Zahlwort an das folgende Wort (25.09.2026), und seit
# dem 26.09.2026 das erste Wort jedes Satzes an das folgende; zuerst die Zahlen, ein schon gebundenes Satzanfangswort bleibt, wie es ist.
nb_zahl = lambda satz: re.sub(r"(?<![\w'’.,-])(\d(?:[\d'’.,]*\d)?%?|(?i:" + "|".join(sorted(NB_WOERTER, key=len, reverse=True)) + r")) (?=\S)", "\\1\u00a0", satz)
# Datumsspanne in de-CH nach CLDR (intervalFormats für MMdd), von Hand: gleicher Monat «14.–16.09.», sonst «19.06. – 21.07.» mit schmalen
# geschützten Leerzeichen, gleicher Tag das einfache Datum.
def ch_spanne(a, b):
    if a == b:
        return f"{a:%d.%m.}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a:%d}.\u2013{b:%d.%m.}"
    return f"{a:%d.%m.}\u202f\u2013\u202f{b:%d.%m.}"


def nb(satz):
    aus, anfang = [], True
    for teil in re.split(r"( )", nb_zahl(satz)):
        if teil == " " and anfang and aus and aus[-1] and not aus[-1].endswith((".", "!", "?")):
            aus.append("\u00a0"); anfang = False; continue
        if teil == " " and aus and aus[-1].endswith((".", "!", "?")):
            anfang = True
        elif teil and teil != " " and "\u00a0" in teil and anfang:
            anfang = False
        aus.append(teil)
    # Seit 26.09.2026 (zweiter Lauf) jeder Satz als eigenes .satz, getrennt am normalen Leerzeichen nach «.», «!» oder «?».
    return " ".join(f'<span class="satz">{s_}</span>' for s_ in re.split(r"(?<=[.!?]) (?=\S)", "".join(aus)))
def kopf_roh():
    gel = gelistet_roh()
    montag_r = heute_roh - timedelta(days=heute_roh.weekday())
    ab_r = datetime.combine(montag_r, time(0), web.TZ).astimezone(timezone.utc)
    with db.verbinden() as c2:
        roh_t = [(r, datetime.fromisoformat(t.replace("Z", "+00:00")), t)
                 for r, t in c2.execute("SELECT repo, zeitstempel_utc FROM commits WHERE autor_adresse = 'dev@fernwerk-studio.example'")]
        # Zweigfrist (26.09.2026) aus den Rohdaten nachgerechnet, mit eigenem Literal statt web.ZWEIG_FRIST: Befund erst, wenn der letzte
        # Commit des Zweigs älter als 3 Tage ist, sekundengenau; ohne Zeitstempel bleibt er Befund.
        offen = [tuple(z) for z in c2.execute("SELECT repo, name, ahead, behind, letzter_utc FROM zweige WHERE ahead > 0 ORDER BY repo") if z[0] in gel
                 and (not z[4] or datetime.now(timezone.utc) - datetime.fromisoformat(z[4].replace("Z", "+00:00")) > timedelta(days=3))]
    roh_c = [(r, d.astimezone(web.TZ).date()) for r, d, _ in roh_t]
    tage = {d for _, d in roh_c if d <= heute_roh}
    ende = heute_roh if heute_roh in tage else heute_roh - timedelta(days=1)
    serie = 0
    while ende - timedelta(days=serie) in tage:
        serie += 1
    if serie >= 2:
        satz = [f"Seit {serie} Tagen ohne Unterbruch."]
        zahlen = [(str(serie), f"Tage ohne Unterbruch, seit {ende - timedelta(days=serie - 1):%d.%m.}")]
    elif serie == 1 and ende == heute_roh:   # nur mit einem Commit von heute (Audit S20); eine Serie bis gestern ist eine Pause
        satz, zahlen = ["Heute wieder aktiv."], [("1", "Tag, heute wieder aktiv")]
    else:
        pause = min(((heute_roh - d).days for r, d in roh_c if r in gel), default=0)
        satz = [f"Seit {pause} Tag{'en' if pause != 1 else ''} kein Commit." if pause else "Noch kein eigener Commit."]
        zahlen = [(str(pause), "Tag ohne Commit" if pause == 1 else "Tage ohne Commit")]
    in_woche = [(r, t) for r, d, t in roh_t if ab_r <= d <= jetzt]
    summe = len(in_woche)
    woche = {r: sum(1 for r2, _ in in_woche if r2 == r) for r in gel}
    letzt = {r: max((t for r2, t in in_woche if r2 == r), default="") for r in gel}
    rang = sorted((r for r in gel if woche[r]), key=lambda r: (woche[r], letzt[r]), reverse=True)[:3]
    rangliste = [(r.split("/")[1], str(woche[r]), f"{woche[r] * 100 / summe:.0f}%") for r in rang]
    # Ring: die drei stärksten Repos und der Rest der Wochensumme als «N weitere», N aus allen Repos der Woche (wie die Wochenkachel).
    repos_w = len({r for r, _ in in_woche})
    rest = summe - sum(woche[r] for r in rang)
    if rest:
        n_w = repos_w - len(rang)
        rangliste.append((f"{n_w} {'weiteres' if n_w == 1 else 'weitere'}", str(rest), f"{rest * 100 / summe:.0f}%"))
    ring_roh = (repos_w, [woche[r] * 100 / summe for r in rang] + ([rest * 100 / summe] if rest else []))
    if rang:
        assert len([r for r in gel if woche[r] == woche[rang[0]]]) == 1, ("Testdaten ohne Gleichstand an der Spitze", woche)
        satz.insert(0, f"Diese Woche am meisten {rang[0].split('/')[1]}, {woche[rang[0]] * 100 / summe:.0f}% der Commits.")
    else:
        satz.insert(0, "Diese Woche noch kein Commit.")   # Reihenfolge wie die Spalten: Woche, Serie, Zweige (25.09.2026)
    if offen:
        satz.append(f"{ZAHLWORT[len(offen)]} Zweig wartet auf main." if len(offen) == 1 else f"{ZAHLWORT[len(offen)]} Zweige warten auf main.")
    # Je Zweigname und Stand eine Zeile: (title, Zweigname, [(Link, Repo-Name)], Befund, Alter).
    gruppen = {}
    for r, n, a, b, l in offen:
        tage_ = (heute_roh - datetime.fromisoformat(l.replace('Z', '+00:00')).astimezone(web.TZ).date()).days
        gruppen.setdefault((n, a, b, tage_), []).append(r)
    zeilen = []
    for (n, a, b, tage_), repos in gruppen.items():
        was = f"{a} Commit{'s' if a != 1 else ''} vor main" + (f", {b} dahinter" if b else "")
        alter = "seit heute" if tage_ == 0 else f"seit {tage_} Tag{'en' if tage_ != 1 else ''}"
        namen = sorted(r.split("/")[1] for r in repos)
        zeilen.append((f"Zweig {n} in {', '.join(namen)}: {was}, {alter}", n, sorted((f"https://github.com/{r}", r.split('/')[1]) for r in repos), was, alter))
    return " ".join(satz), zahlen, rangliste, montag_r, len(offen), zeilen, ring_roh
def repos_gelistet():
    """Gesamtzahl der Projektliste aus eigenem SQL (25.09.2026): Repos, deren gesehen nicht älter ist als der letzte Lauf minus 24 h."""
    with db.verbinden() as c_:
        letzter = c_.execute("SELECT MAX(gestartet_utc) FROM laeufe").fetchone()[0]
        grenze = (datetime.fromisoformat(letzter.replace("Z", "+00:00")) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ") if letzter else ""
        return c_.execute("SELECT COUNT(*) FROM repos WHERE gesehen >= ?", (grenze,)).fetchone()[0]


def pruefe_gesamt(s):
    """Metazeile «N Repos · M diese Woche aktiv» und die Zahl der Gruppe «Ohne Commits diese Woche» (seit 26.09.2026; vorher N im Titel
    und je Gruppe eine Zahl): N aus eigenem SQL, M plus die Gruppenzahl ergibt N, die Liste hat N Zeilen, die aktive Gruppe
    M, und im Titel steht keine Zahl mehr."""
    g = repos_gelistet()
    gesamt, aktiv = projekt_meta(s)
    gruppen = gruppen_von(s)
    zeilen_aktiv = sum(len(r) for t_, _, _, r in gruppen if t_ == "aktiv")
    ohne = sum(n for t_, n, _, _ in gruppen if t_ == "Ohne Commits diese Woche")
    assert '<h3 class="titel">Projekte</h3>' in s and gesamt == g == aktiv + ohne == s.count('<details class="prow') and aktiv == zeilen_aktiv, (gesamt, aktiv, ohne, g, zeilen_aktiv)
    return g


def schliesst(h, j):
    """Index nach dem </div>, das das <div> an Stelle j schliesst."""
    tiefe = 0
    for m in re.finditer(r"<div\b|</div>", h[j:]):
        tiefe += -1 if m.group() == "</div>" else 1
        if tiefe == 0:
            return j + m.end()


def fassung(h, f):
    """Die Fassung f der Gruppe k2 (Umschalter der Kachel 2, 30.09.2026): jeder Stapel wird durch den Inhalt seines Fensters f ersetzt."""
    aus, i = "", 0
    while (j := h.find('<div class="stapel"><div class="fenster" data-gruppe="k2"', i)) >= 0:
        ende = schliesst(h, j)
        m = re.compile(f'<div class="fenster" data-gruppe="k2" data-fenster="{f}"( hidden)?>').search(h, j, ende)
        aus += h[i:j] + h[m.end():schliesst(h, m.start()) - len("</div>")]
        i = ende
    return aus + h[i:]


def pruefe_kopf(s, befunde):
    roh_lage = s[s.index('<div class="lage">'):s.index('<div class="tiles3">')]
    # Die Fassung 7 Tage ist die Vorgabe und steht zuerst; geprüft wird hier die Fassung Woche wie bisher, die 7 Tage an ihren Texten.
    assert roh_lage.index('data-fenster="7">') < roh_lage.index('data-fenster="woche" hidden>')
    sieben = fassung(roh_lage, "7")
    assert re.search(r'<p class="f"><span class="satz">In\u00a0den letzten 7\u00a0Tagen (am meisten|kein Commit)', sieben), sieben[:200]
    assert "in den letzten 7 Tagen nach Repo</span>" in sieben and "kein Commit.</span></div>" not in sieben, sieben[:600]
    lage = fassung(roh_lage, "woche")
    satz, zahlen, rangliste, montag_r, n_zweige, zeilen, (repos_w, anteile) = kopf_roh()
    assert len(zeilen) == befunde, (befunde, zeilen)
    assert re.findall(r'<div class="n"><b>([^<]*)</b><span>([^<]*)</span></div>', lage) == zahlen, (lage, zahlen)
    # Reihenfolge (25.09.2026): der Satz als erstes Kind des Kopfs, dann die eine Reihe mit drei Spalten auf dem Raster der Kacheln:
    # Ring, Handlungsbedarf, und in der dritten Serie und Repos nebeneinander (.npaar).
    assert lage.startswith(f'<div class="lage"><p class="f">{nb(web.html.escape(satz))}</p><div class="nums"><div class="n ring-spalte">') and lage.count('<p class="f">') == 1, (satz, lage[:300])
    assert lage.count('<div class="nums">') == 1 and "wartetn" not in s and "Punkte mit Handlungsbedarf" not in lage and "letzte 7 Tage" not in lage
    i_ring, i_bf, i_paar, i_serie, i_repos = (lage.index(x) for x in ('<div class="n ring-spalte">', '<div class="n befunde">', '<div class="npaar"><div class="n"><b>',
                                                                     '<div class="n"><b>', '<div class="n repos-spalte">'))
    assert i_ring < i_bf < i_paar < i_serie < i_repos, "Ring, Handlungsbedarf, Serie, Repos"
    assert lage.count('class="n befunde"') == 1 and lage.count('class="n repos-spalte"') == 1 and lage.count('class="npaar"') == 1
    # Spalte 3 rechts: die Gesamtzahl aus derselben Menge wie «Projekte N», darunter die in dieser Woche aktiven (eigenes SQL, Kalenderwoche).
    g = pruefe_gesamt(s)
    lage = lage.rstrip()
    assert lage.endswith(f'<div class="n repos-spalte"><b>{g}</b><span>{"Repo" if g == 1 else "Repos"}, {repos_w} diese Woche aktiv</span></div></div></div></div>'), (g, repos_w, lage[-200:])
    ring = lage[i_ring:i_bf]
    assert "rtitel" not in ring and "ringmitte" not in ring, ("keine Überschrift, leere Ringmitte", ring[:300])
    gef = re.findall(r'<li><i class="sw (s1|s2|s3|srest)" aria-hidden="true"></i><span class="rname" title="([^"]*)">([^<]*)</span><span class="rzahl">([^<]*)</span><span class="ranteil">([^<]*)</span></li>', ring)
    assert all(ti == n for _, ti, n, _, _ in gef), ("voller Name im title", gef)
    gef = [(k, n, z, a) for k, _, n, z, a in gef]
    assert [(n, z, a) for _, n, z, a in gef] == rangliste, (gef, rangliste)
    assert [k for k, *_ in gef] == ["s1", "s2", "s3"][:len(gef) - (1 if gef and gef[-1][0] == "srest" else 0)] + (["srest"] if gef and gef[-1][0] == "srest" else []), gef
    # Segmentlängen gleich den Anteilen aus den Rohdaten, aneinander anschliessend, Summe 100.
    seg = [(float(l), float(o)) for l, o in re.findall(r'stroke-dasharray="([\d.]+) [\d.]+" stroke-dashoffset="(-?[\d.]+)"', ring)]
    # Ohne Commit (26.10.2026) ein einziges geschlossenes Segment über 100.
    erwartet = anteile or [100.0]
    assert len(seg) == len(erwartet) and all(abs(l - a) < .001 for (l, _), a in zip(seg, erwartet)), (seg, erwartet)
    assert all(abs(-o - sum(erwartet[:i])) < .002 for i, (_, o) in enumerate(seg)) and abs(sum(l for l, _ in seg) - 100) < .01, seg
    # Unterzeile «diese Woche nach Repo», auch ohne Commit; dann in der Legende nur der gedämpfte Fleck und die 0.
    assert ring.endswith('</ul></div><span>diese Woche nach Repo</span></div>'), ring[-200:]
    if not anteile:
        assert '<circle class="rseg srest"' in ring and 'nach Repo, 0"' in ring and ring.count("<li>") == 1, ring
        assert '<ul class="rlegende"><li><i class="sw srest" aria-hidden="true"></i><span class="rzahl">0</span></li></ul>' in ring, ring
    assert "fbalken" not in lage, "die Anteilbalken der Rangliste sind weg"
    block = lage[i_bf:i_serie]
    # Seit 25.09.2026 nur die Zusammenfassung: grosse Zahl in Akzentfarbe (.n b), Unterzeile, Singular bei 1; bei einer Art ist die
    # Unterzeile der Sprunglink auf ihren Abschnitt im Block Handlungsbedarf. Keine Zweige, Repos oder Links auf GitHub im Kopf.
    wort = "Zweig wartet" if n_zweige == 1 else "Zweige warten"
    assert "<code>" not in block and "github.com" not in block and "bgruppe" not in block, block
    hb = s[s.index('<section class="card handlung"'):s.index('<section class="card projekte">')]
    # Seit 02.10.2026 steht der Block direkt unter den drei Kacheln, vor Projekte (ersetzt den Entscheid vom 25.09.2026).
    assert s.index('<div class="tiles3">') < s.index('<section class="card handlung"') < s.index('<section class="card projekte">'), "Handlungsbedarf vor Projekte"
    if not zeilen:
        # Bei 0 die grosse 0 mit «Kein Handlungsbedarf»; der Block steht seit 04.10.2026 trotzdem, seit 09.10.2026 nur Titel und Zeile mit 0.
        assert block.startswith('<div class="n befunde"><b>0</b><div class="bunter"><span>Kein Handlungsbedarf</span></div></div>') and hb.strip() == ('<section class="card handlung" id="handlung">'
            '<h3 class="titel">Handlungsbedarf</h3>'
            '<p class="hbrest"><span id="handlung-warn">0 Repos mit Sicherheitswarnungen</span> · <span id="handlung-ci">0 Actions-Läufe fehlgeschlagen</span> · <span id="handlung-server">0 Dienste hinter main</span></p></section>'), (block[:200], hb)
        return
    # Seit 04.10.2026 unter Zahl und Beschriftung je Art eine Zeile mit Zahl als Sprunglink auf ihre Spalte, keine Repos.
    assert block.startswith(f'<div class="n befunde"><b>{n_zweige}</b><div class="bunter"><ul class="barten">'
                            f'<li><a href="#handlung-zweige">{n_zweige} {wort} auf main</a></li></ul>'), block[:300]
    assert "anzeigen" not in block, block
    # Block Handlungsbedarf (26.09.2026): je Art der Titel «N Zweige warten auf main», die Einträge offen, je Repo und Zweig
    # eine Zeile mit dem vollen Text im title, Repo als Link auf GitHub, Zweig in <code> mit Stand, das Alter gedämpft am Ende.
    # Seit 09.10.2026 die festen Arten mit 0 als eine Zeile unter dem Titel, dann die Zweige auf dem Raster: bis HB_SPALTE Einträge
    # in einer Spalte, mehr über die volle Breite mit eigenem Raster.
    assert hb.startswith('<section class="card handlung" id="handlung"><h3 class="titel">Handlungsbedarf</h3>'
                         '<p class="hbrest"><span id="handlung-warn">0 Repos mit Sicherheitswarnungen</span> · <span id="handlung-ci">0 Actions-Läufe fehlgeschlagen</span> · <span id="handlung-server">0 Dienste hinter main</span></p><div class="hraster"><div><p class="hbart" id="handlung-zweige">'
                         f'{n_zweige} {wort} auf main</p><ul class="hbliste{" hraster" if n_zweige > web.HB_SPALTE else ""}"><li id="hb-zweige-'), hb[:900]
    assert "<details" not in hb and "anzeigen" not in hb, "alle Einträge offen, ohne «alle N anzeigen»"
    gefunden = re.findall(r'<li id="hb-[^"]*" title="([^"]*)"><a class="hbrepo" href="([^"]*)" target="_blank" rel="noopener">([^<]*)</a><span class="hbz"><code translate="no">([^<]*)</code> ([^<]*)'
                          r' <span class="hbrest">([^<]*)</span></span></li>', hb)
    assert hb.count("<li ") == len(gefunden) == n_zweige, (len(gefunden), n_zweige, hb)
    ist = sorted(tuple(web.html.unescape(x) for x in (u, n, ti, z, w, al)) for ti, u, n, z, w, al in gefunden)
    assert ist == sorted((u, n, f"Zweig {z} in {n}: {w}, {al}", z, w, al) for _, z, links, w, al in zeilen for u, n in links), (ist, zeilen)
    assert "glut" not in block + hb and "muted" not in block + hb, block
s = seite()
pruefe_kopf(s, 0)
satz_roh = kopf_roh()[0]
assert "Diese Woche am meisten neu, 100% der Commits." in satz_roh and f"<p class=\"f\">{nb(satz_roh)}</p>" in s, satz_roh
c = db.verbinden(schreibend=True); c.execute("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc) VALUES ('fernwerk-studio/ruhig', 'wip', 1, 0, ?)", (vor_7_tagen,)); c.commit(); c.close()
s = seite()
pruefe_kopf(s, 1)
assert nb("Ein Zweig wartet auf main.") in s and '<b>1</b><div class="bunter"><ul class="barten"><li><a href="#handlung-zweige">1 Zweig wartet auf main</a></li></ul>' in s and "Zweige" not in s.split('<div class="tiles3">')[0], "Singular bei einem Zweig"
lang_zweig = "feature/ein-sehr-langer-zweigname-" + "der-im-kopf-nur-mit-ellipse-platz-findet-" * 4
# Vier Zürcher Tage alt (zh_mittag(4)): seit der Zweigfrist vom 26.09.2026 wird ein Zweig erst nach 3 Tagen zum Befund, vorher lag er auf gestern.
c = db.verbinden(schreibend=True); c.execute("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc) VALUES ('fernwerk-studio/neu', ?, 12, 118, ?)", (lang_zweig, zh_mittag(4))); c.commit(); c.close()
s = seite()
pruefe_kopf(s, 2)
assert nb("Zwei Zweige warten auf main.") in s and '<b>2</b><div class="bunter"><ul class="barten"><li><a href="#handlung-zweige">2 Zweige warten auf main</a></li></ul>' in s, "Plural bei zwei Zweigen"
assert len(lang_zweig) > 150 and s.count(lang_zweig) == 4, "voller Name im Befundtext, im title, im Kurztext der Zeile und im Paar «Zweige» des Aufklappdetails, nie serverseitig gekürzt"
# Der Singular «seit 1 Tag» ist für Zweige seit der Frist nicht mehr erreichbar (jünger als 3 Tage ist kein Befund); geprüft wird das Alter.
assert '<span class="hbrest">seit 4 Tagen</span>' in s, "Alter des langen Zweigs"
c = db.verbinden(schreibend=True); c.execute("DELETE FROM zweige"); c.commit(); c.close()
pruefe_kopf(seite(), 0)
# Zweigfrist (26.09.2026): ein Zweig vor main ist erst ein Befund, wenn sein letzter Commit älter als 3 Tage ist, sekundengenau.
# Eigene Datenbank, fester Zeitanker auf 12:00 eines Zürcher Kalendertags (nie Wochentagsname, nie UTC-Datum), Abstände als timedelta in
# Sekunden. Handlungsbedarf, Kopfzahl samt Sprunglink und Lagesatz müssen in jedem Fall dasselbe sagen; die Projektzeile nennt jeden Zweig.
haupt_f, db.DB = db.DB, Path(tempfile.mkdtemp()) / "zweigfrist.db"
jf = datetime(2026, 9, 24, 12, 0, tzinfo=web.TZ)
iso_f = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
c = db.verbinden(schreibend=True)
c.execute("INSERT INTO repos (repo, default_branch, gesehen) VALUES ('fernwerk-studio/f', 'main', ?)", (iso_f(jf),))
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES ('f1', 'fernwerk-studio/f', ?, 'dev@fernwerk-studio.example', 'f')", (iso_f(jf - timedelta(hours=1)),))
c.commit(); c.close()
def frist_fall(zweige_, sprache="de"):
    c_ = db.verbinden(schreibend=True); c_.execute("DELETE FROM zweige")
    c_.executemany("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc) VALUES ('fernwerk-studio/f', ?, 1, 0, ?)", [(n_, iso_f(jf - a_)) for n_, a_ in zweige_]); c_.commit(); c_.close()
    return web.seite(jetzt=jf, sprache=sprache)
drei = timedelta(days=3)
TEXTE_F = {"de": {1: ("Ein Zweig wartet auf main.", "Zweig wartet auf main"), 2: ("Zwei Zweige warten auf main.", "Zweige warten auf main"), 0: "Kein Handlungsbedarf"},
           "en": {1: ("One branch is waiting on main.", "branch waiting on main"), 2: ("Two branches are waiting on main.", "branches waiting on main"), 0: "Nothing to act on"}}
for sprache_ in ("de", "en"):
    for zweige_, n_ in (([("alt", drei + timedelta(seconds=1))], 1), ([("frisch", drei - timedelta(seconds=1))], 0),
                        ([("alt", drei + timedelta(seconds=1)), ("frisch", drei - timedelta(seconds=1))], 1),
                        ([("alt", drei + timedelta(seconds=1)), ("aelter", timedelta(days=10))], 2)):
        sf = frist_fall(zweige_, sprache_)
        lage_f = sf[sf.index('<div class="lage">'):sf.index('<div class="tiles3">')]
        hb_f = sf[sf.index('<section class="card handlung"'):sf.index("</section>", sf.index('<section class="card handlung"'))] if 'id="handlung-zweige"' in sf else ""
        erwartet_ = [n for n, a in zweige_ if a > drei]
        assert sorted(re.findall(r'<li id="hb-[^"]*" title="(?:Zweig|Branch) (\S+) in f', hb_f)) == sorted(erwartet_) or (not n_ and not hb_f), (sprache_, zweige_, hb_f[:300])
        if n_:
            satz_f, zahl_f = TEXTE_F[sprache_][n_]
            assert nb(satz_f) in lage_f, (sprache_, n_, lage_f[:400])
            assert f'<b>{n_}</b><div class="bunter"><ul class="barten"><li><a href="#handlung-zweige">{n_} {zahl_f}</a></li></ul>' in lage_f, (sprache_, n_, lage_f[:600])
        else:
            assert TEXTE_F[sprache_][0] in lage_f and "#handlung-zweige" not in sf and 'id="handlung-zweige"' not in sf, (sprache_, zweige_)
            assert not any(w in lage_f for w in ("Zweig", "branch")), (sprache_, lage_f[:400])
        for n, _ in zweige_:
            assert ("Zweig " if sprache_ == "de" else "Branch ") + f"{n} +1" in sf, ("Projektzeile nennt jeden Zweig", n)
db.DB = haupt_f
# befund() ohne Kalender, die Erwartung aus den Eingaben: Anteil gegen die übergebene Summe der Wochenkachel, nicht gegen die
# Summe der Karten (4 Commits, davon 1 in einem Repo ausserhalb der Karenz: 2 von 4 sind 50%, nicht 67%).
karten_a = [{"name": "a", "woche": 2, "tage_her": 0, "befunde": []}, {"name": "b", "woche": 1, "tage_her": 3, "befunde": []}]
satz_a, zahlen_a, rang_a, handlungen_a = web.befund({"aktuell": 1, "heute": True}, karten_a, 4)
assert (satz_a, zahlen_a, rang_a, handlungen_a) == ("Diese Woche am meisten a, 50% der Commits. Heute wieder aktiv.",
                                                    [(1, "Tag, heute wieder aktiv")], [("a", 2, 50.0), ("b", 1, 25.0)], []), (satz_a, rang_a)
# Rangliste: höchstens drei, nach Zahl, bei Gleichstand in der Reihenfolge der Karten (letzter Commit), ohne Repos mit 0.
karten_r = [{"name": n, "woche": w, "tage_her": 0, "befunde": []} for n, w in (("x", 1), ("y", 3), ("z", 0), ("u", 3), ("v", 2))]
assert [n for n, _, _ in web.befund({"aktuell": 1}, karten_r, 9)[2]] == ["y", "u", "v"]
zweig_x = {"zweig": "x", "ahead": 1, "behind": 0, "seit": 3, "branch": "main"}
karte_b = {"name": "b", "repo": "fernwerk-studio/b", "woche": 0, "tage_her": 5, "befunde": [zweig_x]}
assert web.befund({"aktuell": 0}, [{"name": "a", "woche": 0, "tage_her": 9, "befunde": []}, karte_b], 0) == (
    "Diese Woche noch kein Commit. Seit 5 Tagen kein Commit. Ein Zweig wartet auf main.",
    [(5, "Tage ohne Commit")], [], [([karte_b], zweig_x)])
assert web.befund({"aktuell": 0}, [{"name": "a", "woche": 0, "tage_her": 1, "befunde": []}], 0)[:2] == (
    "Diese Woche noch kein Commit. Seit 1 Tag kein Commit.", [(1, "Tag ohne Commit")]), "Singular bei einem Tag Pause"
# Mehrere Arten (25.09.2026): ein einziger Satz «N Punkte mit Handlungsbedarf.», die Arten nennt Kopfspalte 3; eine Art behält ihren Satz.
karte_w = {"name": "w", "repo": "fernwerk-studio/w", "woche": 0, "tage_her": 5, "befunde": [], "warn": {"n": 3, "stufe": "high"}}
karte_c = {"name": "c", "repo": "fernwerk-studio/c", "woche": 0, "tage_her": 5, "befunde": [], "ci": {"workflow": "CI", "alter": "vor 2 Std."}}
assert web.befund({"aktuell": 0}, [karte_b, karte_w], 0)[0] == "Diese Woche noch kein Commit. Seit 5 Tagen kein Commit. Zwei Punkte mit Handlungsbedarf."
assert web.befund({"aktuell": 0}, [karte_b, karte_w, karte_c], 0)[0].endswith(" Drei Punkte mit Handlungsbedarf.")
assert web.befund({"aktuell": 0}, [karte_w], 0)[0].endswith(" Ein Repo hat offene Sicherheitswarnungen.")
viele_b = [dict(karte_b, befunde=[dict(zweig_x, zweig=f"z{i}") for i in range(11)]), karte_w]
assert web.befund({"aktuell": 0}, viele_b, 0)[0].endswith(" Zwölf Punkte mit Handlungsbedarf.") and web.befund({"aktuell": 0}, viele_b + [karte_c], 0)[0].endswith(" 13 Punkte mit Handlungsbedarf.")
assert [web.seit_text(n) for n in (0, 1, 2)] == ["seit heute", "seit 1 Tag", "seit 2 Tagen"]
# Singular und Plural der Zweigzahl: ein Zweig «wartet», zwei und drei «warten», nie «wartetn».
for n_z in (1, 2, 3):
    satz_z = web.befund({"aktuell": 0}, [{"name": "a", "woche": 0, "tage_her": None, "befunde": [dict(zweig_x, zweig=f"z{i}") for i in range(n_z)]}], 0)[0]
    erwartet_z = f"{ZAHLWORT[n_z]} Zweig wartet auf main." if n_z == 1 else f"{ZAHLWORT[n_z]} Zweige warten auf main."
    assert satz_z == f"Diese Woche noch kein Commit. Noch kein eigener Commit. {erwartet_z}" and "wartetn" not in satz_z, (n_z, satz_z)

assert lauf(z(59), ["fernwerk-studio/neu", "fernwerk-studio/ruhig", "fernwerk-studio/still", "fernwerk-studio/archiv"])
roh = (("r1", "fernwerk-studio/still", 10, "Ruhender Betreff <eins> & Co, und ein langer Rest, der in der Zeile nur mit Ellipse Platz findet, nie serverseitig gekürzt"), ("r2", "fernwerk-studio/still", 12, "Ruhender Betreff zwei"), ("u1", "fernwerk-studio/archiv", 100, "Stiller Betreff"))
c = db.verbinden(schreibend=True)
for sha, repo, tage, betreff in roh:
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, ?, ?, 'dev@fernwerk-studio.example', ?)",
              (sha, repo, (jetzt - timedelta(days=tage)).strftime("%Y-%m-%dT12:00:00Z"), betreff))
c.execute("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc) VALUES ('fernwerk-studio/still', 'test/x', 1, 118, ?)", ((jetzt - timedelta(days=44)).strftime("%Y-%m-%dT12:00:00Z"),))
c.commit(); c.close()
s = seite()
gelistet, letzte = gelistet_roh(), letzte_roh()
aktiv_roh = sorted((r for r in gelistet if r in letzte and tage_roh(letzte[r]) <= 7), key=lambda r: letzte[r], reverse=True)
ruhend_roh = sorted((r for r in gelistet if r not in aktiv_roh), key=lambda r: letzte.get(r, ""), reverse=True)
reihenfolge = re.findall(r'<details class="prow(?: gedaempft)?" data-repo="([^"]+)"', s)
assert sorted(reihenfolge) == sorted(gelistet) and len(gelistet) == 4, ("Liste enthält alle Repos", reihenfolge, gelistet)
assert reihenfolge == aktiv_roh + ruhend_roh, ("nach letztem Commit, ruhende am Ende", reihenfolge, aktiv_roh + ruhend_roh)
assert re.findall(r'<details class="prow gedaempft" data-repo="([^"]+)"', s) == ruhend_roh == ["fernwerk-studio/still", "fernwerk-studio/archiv"], "gedämpft genau die nicht aktiven"
assert s.count('class="ruhend"') == 0 and "Ruht:" not in s, "keine Ruhezeile mehr"
assert f'<span class="palter">vor {tage_roh(letzte["fernwerk-studio/still"])} Tagen, ruht</span>' in summary_von(s, "fernwerk-studio/still")
assert f'<span class="palter">vor {tage_roh(letzte["fernwerk-studio/archiv"])} Tagen, still</span>' in summary_von(s, "fernwerk-studio/archiv")
for r in aktiv_roh:
    assert ", ruht<" not in summary_von(s, r) and ", still<" not in summary_von(s, r) and "aktiv" not in summary_von(s, r), r
assert '<span class="pbefund"><a href="#hb-zweige-fernwerk-studio_2f_still~test_2f_x">Zweig test/x +1</a></span>' in summary_von(s, "fernwerk-studio/still") and s.count('class="pbefund"') == 1, "Befund nur in seiner Zeile"
montag_roh = heute_roh - timedelta(days=heute_roh.weekday())
mit_roh = sum(1 for r in gelistet if any(montag_roh <= datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(web.TZ).date() <= heute_roh
                                         for (t,) in db.verbinden().execute("SELECT zeitstempel_utc FROM commits WHERE repo = ?", (r,))))
# Zwei Gruppen statt der Zählzeile (25.09.2026): Anzahlen aus den Rohdaten, jedes Repo genau einmal, darin nach letztem Commit.
gr = gruppen_von(s)
aktiv_w = [r for r in reihenfolge if any(montag_roh <= datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(web.TZ).date() <= heute_roh
                                         for (t,) in db.verbinden().execute("SELECT zeitstempel_utc FROM commits WHERE repo = ?", (r,)))]
erwartet_gr = [(t, rs) for t, rs in (("aktiv", aktiv_w), ("Ohne Commits diese Woche", [r for r in reihenfolge if r not in aktiv_w])) if rs]
assert [(t, rs) for t, _, _, rs in gr] == erwartet_gr and [n for _, n, _, _ in gr] == [len(rs) for _, rs in erwartet_gr] and mit_roh == len(aktiv_w), (gr, erwartet_gr)
assert [g for _, _, g, _ in gr] == [False] + [True] * (len(gr) - 1) and "Repos:" not in s, "Linie nur über der zweiten Gruppe, keine Zählzeile"
assert all(sorted(rs, key=lambda r: letzte.get(r, ""), reverse=True) == rs for _, _, _, rs in gr), "in der Gruppe nach letztem Commit"
assert "Zweig test/x in still: 1 Commit vor main" in s, "Befund im Kopf"
# Mikrokurve: eine je Zeile, 120x20 (seit 27.09.2026, vorher 96x20), ohne Zeiger-Attribute, ohne Achse, ohne Schrift; im Kopf der Zeile keine Zeigerstelle.
for r in gelistet:
    kopf = summary_von(s, r)
    mikro = re.findall(r'<(svg|span) class="mikro"[^>]*>.*?</\1>', kopf)
    assert len(mikro) == 1 and "data-stellen" not in kopf and 'class="zl"' not in kopf and "zbox" not in kopf and "zzeile" not in kopf, (r, kopf)
    assert "ylab" not in kopf and "xlab" not in kopf and "<text" not in kopf and "polygon" not in kopf, r
m = re.search(r'<svg class="mikro"[^>]*>', summary_von(s, "fernwerk-studio/still")).group(0)
assert 'width="120"' in m and 'height="20"' in m and 'viewBox="0 0 120 20"' in m, m
# Betreffspalte: der Betreff des letzten eigenen Commits, voll im HTML (gekürzt wird nur im Browser per Ellipse), leer,
# wenn das Repo im Zwölfwochenfenster der Kurve keinen Commit hat. Erwartung aus eigenem SQL über die Commits.
start12_roh = heute_roh - timedelta(days=heute_roh.weekday(), weeks=11)
with db.verbinden() as c2:
    for r in gelistet:
        zeilen_roh = c2.execute("SELECT zeitstempel_utc, betreff FROM commits WHERE repo = ? AND autor_adresse = 'dev@fernwerk-studio.example' ORDER BY zeitstempel_utc DESC", (r,)).fetchall()
        im_fenster = any(datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(web.TZ).date() >= start12_roh for t, _ in zeilen_roh)
        erwartet = web.html.escape((zeilen_roh[0][1] or "") if zeilen_roh and im_fenster else "")
        kopf = summary_von(s, r)
        assert kopf.count('class="pbetreff"') == 1 and f'<span class="pbetreff" title="{erwartet}" translate="no">{erwartet}</span>' in kopf, (r, erwartet, kopf)
        assert kopf.index('class="palter"') < kopf.index('class="pbetreff"') < kopf.index('class="chev"'), r
assert len(roh[0][3]) > 100 and web.html.escape(roh[0][3]) in summary_von(s, "fernwerk-studio/still"), "langer Betreff voll im HTML"
assert '<span class="pbetreff" title="" translate="no"></span>' in summary_von(s, "fernwerk-studio/archiv"), "ohne Commit im Fenster keine Betreffzeile"
assert summary_von(s, "fernwerk-studio/still").index('class="pbetreff"') < summary_von(s, "fernwerk-studio/still").index('class="pbefund"'), "Befund nach dem Betreff"
# Ellipse und Wegfall unter 700 px stehen im Stylesheet: einzeilig, abgeschnitten mit Auslassungszeichen, schrumpfbar.
css = (web.WEB / "style.css").read_text()
regeln = {}
for sel, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):   # Kommentare vor dem Selektor gehören nicht dazu
    for teil in sel.split(","):
        regeln.setdefault(teil.strip(), []).append(decl)
betreff_css = " ".join(regeln[".pbetreff"])
for d in ("white-space: nowrap", "overflow: hidden", "text-overflow: ellipsis", "min-width: 0"):
    assert d in betreff_css, (d, betreff_css)
schmal = css[css.index("@media (max-width: 700px)"):]
schmal = schmal[:schmal.index("\n@media")]
assert re.search(r"\.pbetreff[^{]*\{\s*display: none", schmal) and re.search(r"\.mikro[^{]*\{\s*display: none", schmal), schmal
# Unter 700 px fällt auch der Befund weg (entschieden 13.09.2026), er steht im Kopf der Zone; Wert und Alter stehen in festen
# Spuren, damit sie über alle Zeilen fluchten.
assert re.search(r"\.pbefund[^{]*\{\s*display: none", schmal), schmal
# Fokus der Zeile (20.09.2026): volle Textfarbe tragen GENAU ZWEI Dinge, Wochenwert und Betreff des letzten
# Commits. Alles andere ordnet ein und bleibt gedämpft; der Repo-Name bleibt der Akzentlink; eine ruhende Zeile
# ist als Ganzes gedämpft, auch ihr Betreff.
assert "color: var(--txt)" in " ".join(regeln[".pzahl"]) and "color: var(--txt)" in " ".join(regeln[".pbetreff"])
for sel, farbe in ((".pwert .delta", "--lbl"), (".palter", "--lbl"), (".pbefund", "--lbl"), (".mikro rect", "--lbl")):
    assert f"var({farbe})" in " ".join(regeln[sel]), (sel, regeln.get(sel))
assert "var(--lbl)" in " ".join(regeln[".chev"]) and "var(--eis)" not in " ".join(regeln[".pbefund"]), "Befund nicht mehr im Akzent"
assert "color: var(--lbl)" in " ".join(regeln[".prow.gedaempft .pbetreff"]), regeln.get(".prow.gedaempft .pbetreff")
assert "color: var(--eis)" in " ".join(regeln["a"]), "der Repo-Name bleibt der Akzentlink"
# Genau zwei Regeln in der Zeile setzen die volle Textfarbe; kommt eine dritte dazu, ist der Fokus wieder weg.
# .pmarke ist die Spitzenmarke der Grafiken, nicht Teil der Zeile.
zeilen_sel = (".pname", ".pwert", ".pzahl", ".pwert .delta", ".palter", ".pbetreff", ".pbefund", ".chev", ".mikro rect", ".prow .mikro")
in_zeile = [sel for sel in zeilen_sel if any("color: var(--txt)" in d for d in regeln.get(sel, []))]
assert in_zeile == [".pzahl", ".pbetreff"], in_zeile
# Mehr Luft: Innenabstand je Zeile in rem, deutlich grösser als die .6rem davor.
padding = re.search(r"details\.prow > summary \{[^}]*(padding: [^;]*);", css).group(1)
assert re.fullmatch(r"padding: ([\d.]+)rem 0", padding) and float(padding.split()[1][:-3]) >= .9, padding
assert re.search(r"grid-template-columns: minmax\(0, 1fr\) [\d.]+rem [\d.]+rem 12px;", schmal), schmal
assert "Zweig test/x in still: 1 Commit vor main, 118 dahinter" in s, "der verborgene Befund steht im Kopf der Zone"
# Kopf der Projektliste in der Fläche (seit 13.09.2026): der Abschnitt ist die Fläche wie der Rückblick, Titel
# und Zählzeile stehen darin vor der Liste; die Liste selbst trägt keine zweite Fläche.
kopf_zeilen = [f'<section class="card {name}">\n  <h3 class="titel">{titel} <span class="lbl"' for name, titel in (("rueckblick", "Rückblick"),)]
kopf_zeilen.append(f'<section class="card projekte">\n  <h3 class="titel">Projekte</h3>\n  <p class="delta bezug pkopf"><span class="kwert">{pruefe_gesamt(s)}</span>')
assert all(k in s for k in kopf_zeilen), [k for k in kopf_zeilen if k not in s]
assert s.index('<section class="card projekte">') < s.index('class="titel">Projekte') < s.index('<div class="pliste">') < s.index("</section>", s.index('<section class="card projekte">'))
assert not any(d in " ".join(regeln[".pliste"]) for d in ("background", "border", "padding")), regeln[".pliste"]
# Gestrichen am 13.09.2026 mit der Linie: «ein einzelner Datenpunkt ist ein kurzer Strich» (<line>, Breite höchstens 12 px),
# «ohne Commits im Fenster kein <svg>» und «drei Werte ergeben eine <polyline>».
# Kopf der GitHub-Zone im Stylesheet (25.09.2026): die Reihe hat dieselbe Regel wie die Kacheln (Spalten und Abstand), und wo die
# Kacheln untereinander stehen, stehen auch die drei Spalten untereinander.
# Seit 26.09.2026 steht auch der Block Handlungsbedarf (.hraster) auf derselben Regel.
assert ".tiles3, .nums, .hraster { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--s4); }" in css, "eine Regel für Kacheln und Kopfreihe"
assert "@media (max-width: 820px) { .tiles3, .nums, .hraster, .rraster { grid-template-columns: 1fr; } }" in css
# Serverzone (27.09.2026): Lage und Dienste ohne Fläche, auf der Spaltenregel der Fussblöcke; die Fussblöcke behalten ihre Kacheln.
assert regeln[".zwei"][0].count("repeat(auto-fit, minmax(min(26rem, 100%), 1fr))") == 1 and regeln[".fuss"][0].count("repeat(auto-fit, minmax(min(26rem, 100%), 1fr))") == 1
assert "background" not in " ".join(regeln[".zone.server .tile"]) and "border" not in " ".join(regeln[".zone.server .tile"]) and "background: var(--card)" in regeln[".meta"][0]
# Kopfspalte 3 (26.09.2026): Serie und Repos untereinander in einer Spalte, beide Zahlen über dieselbe Regel .n b (gleiche Grösse).
assert "grid-template-columns: minmax(0, 1fr);" in " ".join(regeln[".npaar"]) and "repeat(2" not in " ".join(regeln[".npaar"]) and "min-width: 0" in " ".join(regeln[".npaar"]), regeln.get(".npaar")
assert not any("font-size" in d for sel, ds in regeln.items() if ("npaar" in sel or "repos-spalte" in sel) for d in ds), "keine eigene Schriftgrösse im Paar"
# Befundblock als dritte Spalte: keine Trennlinie mehr, kein Kasten, Zeilen einzeilig mit Ellipse, Stand gedämpft, Zweig in Monospace.
# Keine Linie und kein Kasten im Kopf; die grosse Zahl der Zweige trägt die Akzentfarbe über .n b wie die Serie.
assert not any(re.search(r"border|outline|box-shadow", d) for d in regeln[".n"] + regeln[".n.befunde"] + regeln[".bunter"] + regeln[".barten"] + regeln[".lage"]), "keine Linie im Kopf"
# Kopfspalte 3 hat als Mindesthöhe ihren vollen Stand (Zahl, Abstand, vier Zeilen), die Kopfreihe hängt nicht an der Zahl der Befunde.
# Seit 02.10.2026 dazu der Innenabstand der Kacheln (--s5), damit die Spalte auf der Innenkante der Kachel darunter beginnt.
# Seit 09.10.2026 gilt die Mindesthöhe nur nebeneinander: einspaltig entfällt sie, die Kopfspalten stehen dort mit --s5 wie Serie und Repos.
assert regeln[".n.befunde"] == [" padding-inline-start: var(--s5); ", " min-height: calc(2rem + .75rem + .75rem * 1.3 + .4rem + 5 * .75rem * 1.2); ", " min-height: 0; "], regeln.get(".n.befunde")
assert "@media (max-width: 820px) { .n.befunde { min-height: 0; } .nums { row-gap: var(--s5); } }" in css and "gap: var(--s5)" in " ".join(regeln[".npaar"])
# Aufklapper im Handlungsbedarf (30.09.2026): der Warnungseintrag ohne den allgemeinen Chevron als ::before, mit dem
# Chevron der Gruppe «Ohne Commits diese Woche» aus denselben Regeln für Lage und Drehung, keine eigenen Werte.
assert not any("hbmehr" in sel for sel in regeln), "«alle N anzeigen» entfällt seit 04.10.2026"
for art_ in ("hbeintrag",):
    assert regeln[f"details.{art_} > summary::before"] == [" content: none; "], art_
    for sel_ in ("details.gruppe > summary", "details.gruppe > summary .chev", "details.gruppe[open] > summary .chev"):
        assert all(d in regeln[sel_.replace("gruppe", art_)] for d in regeln[sel_]), (art_, sel_)
    assert not any(".chev" in sel and art_ in sel and sel != f"details.{art_} > summary .chev" and sel != f"details.{art_}[open] > summary .chev" for sel in regeln), art_
# Artzeilen (Nachbesserung 25.09.2026): eigene Liste mit Abstand zur Unterzeile, Punkt in Akzentfarbe, Text in voller Textfarbe,
# unterstrichen beim Überfahren; nicht mehr gedämpft.
assert "margin: .4rem 0 0" in " ".join(regeln[".barten"]) and "color: var(--txt)" in " ".join(regeln[".barten li"]) and "color: var(--txt)" in " ".join(regeln[".barten a"])
# Die Mindesthöhe rechnet mit .75rem × 1.25 je Artzeile; eine andere Zeilenhöhe hebt die Kopfreihe über die Ringspalte (111.59 px).
# Seit 26.09.2026 Zeilenhöhe 1.2: fünf Arten (mit «nicht abrufbar») bleiben mit 138 px unter der Ringspalte (139.59 px).
assert "font-size: .75rem; line-height: 1.2;" in " ".join(regeln[".barten li"]) and "* .75rem * 1.2)" in " ".join(regeln[".n.befunde"]), regeln[".barten li"]
assert 'content: "•"' in " ".join(regeln[".barten li::before"]) and "color: var(--eis)" in " ".join(regeln[".barten li::before"])
assert "text-decoration: underline" in " ".join(regeln[".barten a:hover"]) and ".bart" not in regeln, regeln.get(".barten a:hover")
assert "color: var(--eis)" in " ".join(regeln[".n b"]), regeln[".n b"]
# Laufweite der Lagesätze (25.09.2026): im GitHub-Kopf höchstens zwei Kachelspalten plus Zwischenraum aus dem Raster (drei Spalten,
# Abstand --s4), nur nebeneinander; umbrochen mit balance. In der Serverkachel pretty, weil Chrome über sechs Zeilen nicht balanciert.
# Seit 26.09.2026 höchstens bis zur Mitte der Zone (50 % der Inhaltsbreite), untereinander volle Breite.
assert "@media (min-width: 821px) { .zone.github > .lage > .f, .zone.github > .lage > .stapel { max-width: 50%; } }" in css, "Lagesatz bis zur Zonenmitte"
assert "text-wrap: balance" in " ".join(regeln[".f"]) and "text-wrap: pretty" in " ".join(regeln[".tile.lage .f"]), (regeln[".f"], regeln[".tile.lage .f"])
assert "font-size: 1.35rem" in " ".join(regeln[".f"]) and "font-size: 1.25rem" in " ".join(regeln[".tile.lage .f"]), "Schriftgrösse bleibt"
# Luft unter der grossen Zahl (25.09.2026): der Abstand der Reihe steht in rem und ist mindestens .75rem; gemessen im Browser (bei .6rem lag das g in «16 Tage» 7.7 px über der Zeile).
luft = re.search(r"gap: ([\d.]+)rem", " ".join(regeln[".n"]))
assert luft and float(luft.group(1)) >= .75, regeln[".n"]
# Einträge im Block Handlungsbedarf (26.09.2026): jede Zeile einzeilig mit Ellipse, Repo in Textfarbe, der Rest gedämpft, Zweig in Monospace.
# Seit 03.10.2026 je Eintrag ein Listenpunkt: derselbe Punkt wie die Befundliste des Kopfs (eine Regel für beide, dieselbe Grösse),
# Repo-Zeile und Unterzeilen in der zweiten Spalte (hängender Einzug), Abstände aus den Tokens.
assert regeln[".hbliste > li::before"][0] == regeln[".barten li::before"][0] and "color: var(--eis)" in regeln[".barten li::before"][0], regeln[".hbliste > li::before"]
assert "font-size: .75rem" in " ".join(regeln[".hbliste > li::before"]) and "font-size: .75rem" in " ".join(regeln[".barten li"]), "Punkt gleich gross"
assert "grid-template-columns: auto minmax(0, 1fr)" in " ".join(regeln[".hbliste > li"]) and "grid-column: 2" in " ".join(regeln[".hbliste > li > *"])
assert "gap: var(--s4)" in " ".join(regeln[".hbliste"]) and "row-gap: .2rem" in " ".join(regeln[".hbliste > li"]) and "margin-top: var(--s3)" in " ".join(regeln[".handlung .hbart + .hbliste"])
assert "-webkit-line-clamp: 2" in " ".join(regeln[".hbliste > li > .hbzwei"]), "Betreff höchstens zwei Zeilen"
zeile_css = " ".join(regeln[".hbliste > li > *"])
assert all(d in zeile_css for d in ("white-space: nowrap", "overflow: hidden", "text-overflow: ellipsis", "min-width: 0")), zeile_css
assert "color: var(--txt)" in " ".join(regeln[".hbliste"]) and "color: inherit" in " ".join(regeln[".hbliste a"]) and "color: var(--lbl)" in " ".join(regeln[".hbrest"]) \
    and "monospace" in " ".join(regeln[".hbliste code"])
# Ring: Segmente in der Akzentfarbe, abgestuft nur über die Deckkraft (1, .67, .33), der Rest gedämpft; keine andere Farbe.
assert regeln[".s1"][0] == regeln[".s2"][0] == regeln[".s3"][0] == " stroke: var(--eis); " and regeln[".s1"][1:] == [" stroke-opacity: 1; "] and regeln[".s2"][1:] == [" stroke-opacity: .67; "] \
    and regeln[".s3"][1:] == [" stroke-opacity: .33; "] and regeln[".srest"] == [" stroke: var(--lbl); "], {k: regeln.get(k) for k in (".s1", ".s2", ".s3", ".srest")}
assert not re.search(r"(--glut|--mint|#[0-9a-fA-F]{3,6}|rgb)", " ".join(regeln[".rseg"] + regeln[".s1"] + regeln[".s2"] + regeln[".s3"] + regeln[".srest"])), "keine eigene Farbe je Segment"
assert "glut" not in " ".join(d for sel, ds in regeln.items() if "befund" in sel or sel.startswith(".b") for d in ds), "keine Warnfarbe im Befundblock"
# Mikrokurve seit 27.09.2026 als 30 Tagesbalken auf demselben Fenster der letzten 30 Kalendertage (vorher zwölf Wochen): je Zeile 30
# Rechtecke mit denselben x-Werten, gleicher Breite (mindestens 2 px) und gleichem Abstand, innerhalb 120 x 20. Ein Tag ohne Commits ist
# ein Grundstrich von 1 px am Boden, der Tag mit den meisten Commits der Zeile füllt die Höhe, die übrigen stehen im Verhältnis (auf ganze
# Pixel gerundet, mindestens 2 px, damit ein Tag mit Commits nie wie ein leerer aussieht). Erwartete Tageswerte aus den Rohzeilen nach
# Zürcher Datum, nie aus tagesreihen().
balken_von = lambda h: [tuple(int(v) for v in b) for b in re.findall(r'<rect(?: class="jetzt")? x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"/>', h)]
def tage_roh(c2, repo, heute_):
    """Seit 27.09.2026 zeigt die Mikrokurve 30 Kalendertage bis heute nach Zürcher Datum; Erwartung aus den Rohzeilen, nie aus tagesreihen()."""
    werte = [0] * 30
    for (t,) in c2.execute("SELECT zeitstempel_utc FROM commits WHERE repo = ? AND autor_adresse = 'dev@fernwerk-studio.example'", (repo,)):
        i = 29 - (heute_ - datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(web.TZ).date()).days
        if 0 <= i <= 29:
            werte[i] += 1
    return werte
def pruefe_balken(h, werte):
    b = balken_von(h)
    assert len(b) == 30 == h.count("<rect") and "<line" not in h and "<polyline" not in h, (len(b), h)
    xs, breiten = [x for x, _, _, _ in b], {w for _, _, w, _ in b}
    # Genau ein Balken im Akzent, immer an der Stelle des laufenden Tags (des letzten), auch bei null.
    klassen = re.findall(r'<rect( class="jetzt")? x=', h)
    assert [bool(k) for k in klassen] == [False] * 29 + [True], ("Akzent nur auf dem laufenden Tag", werte, h)
    assert len(breiten) == 1 and next(iter(breiten)) >= 2 and len({xs[i + 1] - xs[i] for i in range(29)}) == 1 and xs[1] - xs[0] > next(iter(breiten)), (xs, breiten)
    assert xs[0] >= 0 and xs[-1] + next(iter(breiten)) <= 120 and all(y + hh == 20 for _, y, _, hh in b), b
    oben = max(werte)
    for (_, _, _, hh), v in zip(b, werte):
        if not v:
            assert hh == 1, ("leerer Tag: Grundstrich 1 px", werte, b)
        else:
            # Die Untergrenze gehört in die Erwartung: 3 von 70 ergäben 0.86 px und stehen als 2 px (gemessen live am 13.09.2026).
            assert hh >= 2 and abs(hh - max(2, 20 * v / oben)) <= 1 and (v != oben or hh == 20), ("Massstab auf das Maximum der Zeile", werte, b)
    return xs
with db.verbinden() as c2:
    fenster_x = {r: pruefe_balken(re.search(r'<svg class="mikro".*?</svg>', summary_von(s, r)).group(0), tage_roh(c2, r, heute_roh)) for r in gelistet}
    werte_roh = {r: tage_roh(c2, r, heute_roh) for r in gelistet}
assert len({tuple(x) for x in fenster_x.values()}) == 1, ("alle Zeilen auf denselben x-Werten", fenster_x)
assert werte_roh["fernwerk-studio/ruhig"] == [0] * 22 + [1] + [0] * 7, ("ruhig: ein Commit vor sieben Tagen, ein Balken an seiner Stelle", werte_roh["fernwerk-studio/ruhig"])
assert werte_roh["fernwerk-studio/archiv"] == [0] * 30, "archiv: ohne Commit im Fenster, 30 Grundstriche"
assert " ".join(regeln[".mikro rect"]).count("fill: var(--lbl)") == 1, "gedämpft wie die Linie davor, Farbe als CSS, nicht als Attribut"
# Aufklappdetail: zuerst die Wochenkurve mit Zeiger, darunter das Raster des Stands und die letzten Betreffzeilen, auch beim ruhenden Repo.
with db.verbinden() as c2:
    for r in ("fernwerk-studio/still", "fernwerk-studio/archiv"):
        roh5 = c2.execute("SELECT zeitstempel_utc, betreff FROM commits WHERE repo = ? AND autor_adresse = 'dev@fernwerk-studio.example' ORDER BY zeitstempel_utc DESC LIMIT 5", (r,)).fetchall()
        betreffe = [b for _, b in roh5]
        detail = zeile_von(s, r)[zeile_von(s, r).index("</summary>"):]
        assert betreffe and all(f"</span><span translate=\"no\">{web.html.escape(b)}</span></li>" in detail for b in betreffe), (r, betreffe, detail)
        # Datumsspalte: je Commit ein eigenes Datumselement mit dem Zürcher Datum des Rohzeitstempels, kurz ohne Jahr (26.09.2026), direkt davor der Betreff
        erwartet_li = "".join(f'<li><span class="datum">{datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(web.TZ):%d.%m.}</span><span translate="no">{web.html.escape(b)}</span></li>' for t, b in roh5)
        assert f'<ul class="commits">{erwartet_li}</ul>' in detail, (r, erwartet_li)
        assert '<dl class="pmeta">' in detail and 'class="chips"' not in detail, r
assert zeile_von(s, "fernwerk-studio/still").index('class="zbox"') < zeile_von(s, "fernwerk-studio/still").index('class="pmeta"') and "data-stellen" in zeile_von(s, "fernwerk-studio/still"), "Kurve mit Zeiger vor dem Stand"
commits_li = " ".join(regeln[".commits li"])
assert "grid-template-columns: 3.75rem minmax(0, 1fr)" in commits_li and "column-gap:" in commits_li, commits_li
assert "white-space: nowrap" in " ".join(regeln[".commits .datum"]), "das Datum bricht nicht in den Betreff"
assert zeile_von(s, "fernwerk-studio/still").count('<div class="pstand">') == 1, "Stand und Commits als eine Spalte neben der Kurve"
assert "Kurve je Repo" not in s, "Erklärzeile unter der Liste entfällt"
# Seit 27.09.2026 zeigt jedes Repo die sieben Tage der laufenden Woche, auch ohne Commit: vergangene Tage als Grundstrich, heute in Akzentfarbe.
_ar = zeile_von(s, "fernwerk-studio/archiv")[zeile_von(s, "fernwerk-studio/archiv").index('class="pdetail"'):]
_ar = _ar[_ar.index('data-fenster="7">'):_ar.index('data-fenster="30" hidden>')]   # seit 03.10.2026 liegen 30 und 90 Tage daneben, verborgen
assert 'class="zbox"' in _ar and _ar.count('<rect class="b leer"') == heute_roh.weekday() and _ar.count('<rect class="b jetzt leer"') == 1 and _ar.count("<rect") == heute_roh.weekday() + 1, ("ohne Commits diese Woche: Grundstriche bis heute", heute_roh, _ar)
# Umschalter im Detail (03.10.2026): je Projektzeile genau einer über der Grafik, ausserhalb der Fenster, «Woche» (Wert 7) gedrückt und als einziges
# Fenster sichtbar; 30 und 90 Tage liegen verborgen in derselben Rasterzelle, jede Grafik mit Zeiger.
for r in ("fernwerk-studio/still", "fernwerk-studio/archiv"):
    _det = zeile_von(s, r)[zeile_von(s, r).index('class="pdetail"'):]
    _navs = re.findall(r'<nav class="seg klein umschalter" aria-label="Zeitraum der Grafik" data-gruppe="pd">(.*?)</nav>', _det)
    assert len(_navs) == 1 and re.findall(r">([^<]+)</button>", _navs[0]) == ["Woche", "30 Tage", "90 Tage"], (r, _navs)
    assert re.findall(r'data-fenster="(\w+)" aria-pressed="true"', _navs[0]) == ["7"], _navs
    assert re.findall(r'<div class="fenster" data-gruppe="pd" data-fenster="(\w+)"( hidden)?>', _det) == [("7", ""), ("30", " hidden"), ("90", " hidden")], r
    assert _det.index("data-gruppe=\"pd\">") < _det.index('<div class="stapel">') and _det.count('class="zbox"') == 3, r
assert "addEventListener('toggle'" in s, "Zeiger im Detail erst beim ersten Öffnen"
c = db.verbinden(schreibend=True); c.execute("DELETE FROM zweige"); c.execute("DELETE FROM commits WHERE sha IN ('r1', 'r2', 'u1')"); c.execute("DELETE FROM repos WHERE repo IN ('fernwerk-studio/still', 'fernwerk-studio/archiv')"); c.commit(); c.close()
# 2d: Container-Eintrag ohne Schlüssel bild (Schreiber vor dem Fix bei «fehlt», Störung 11.09.2026 nach der
# Umbenennung von wochenlog in trainingslog) und Eintrag mit bild null: die Seite rendert, der Zustand steht als
# «unbekannt» in Warnfarbe, kein 500.
kaputt = json.loads(json.dumps(beispiel))
kaputt["erhoben_utc"] = web.iso_utc(jetzt)
kaputt["dienste"][3]["container"] = [{"name": "wochenlog-wochenlog-1", "status": "fehlt"}]
kaputt["dienste"][1]["container"][0]["bild"] = None
h = web.server_html(kaputt, jetzt)
assert 'class="glut">wochenlog-wochenlog-1 nicht gefunden, tools/dienste.json prüfen</span>' in h, h[h.index("wochenlog-wochenlog-1"):][:200]
assert 'Container wochenlog-wochenlog-1 nicht gefunden, tools/dienste.json prüfen</span></span></summary>' in h, "Dienstzeile nennt den gesuchten Namen"
assert 'preisbeobachter-preise-1 läuft seit' in h and h.count('Bild <span class="glut">unbekannt</span>') == 1, "bild null beim laufenden Container"
# Timer fehlt: der Container läuft, der konfigurierte Timer ist nicht zu finden; die Dienstzeile nennt den Timernamen
# und den Hinweis in Warnfarbe, das Detail auch, und der Zustandspunkt steht auf Warnfarbe.
ohne_timer = json.loads(json.dumps(beispiel))
ohne_timer["erhoben_utc"] = web.iso_utc(jetzt)
ohne_timer["dienste"][1]["container"][0]["status"] = "running"
ohne_timer["dienste"][1]["timer"] = [{"unit": "beobachter-lauf", "status": "fehlt"}]
h = web.server_html(ohne_timer, jetzt)
zeile = h[h.index(">Preisbeobachter</a>"):]
zeile = zeile[:zeile.index("</details>")]
assert '<span class="glut">Timer beobachter-lauf nicht gefunden, tools/dienste.json prüfen</span></span></summary>' in zeile, zeile[:300]
assert '<span class="dk">Lauf</span><span class="glut">Timer beobachter-lauf nicht gefunden, tools/dienste.json prüfen</span>' in zeile, "Detail nennt den Timer"
assert 'punkt glut' in h[h.rindex("<details", 0, h.index(">Preisbeobachter</a>")):h.index(">Preisbeobachter</a>")], "Zustandspunkt in Warnfarbe"
assert "Block konnte nicht gezeichnet werden" not in h
# Dienst ohne Timer (namensprobe, 25.09.2026): läuft der Container, grüner Punkt und «läuft seit», kein «kein Lauf»; steht er, Warnfarbe.
kein_timer = json.loads(json.dumps(beispiel))
kein_timer["erhoben_utc"] = web.iso_utc(jetzt)
kein_timer["dienste"].append({"name": "namensprobe", "zweck": "Namens-Prüfung", "link": "https://host-01.example.ts.net:8447/", "repo": "fernwerk-studio/namensprobe",
                              "container": [dict(kein_timer["dienste"][2]["container"][0], name="namensprobe-namensprobe-1", status="running")], "timer": [], "deploy": None})
h = web.server_html(kein_timer, jetzt)
kopf_n = h[h.rindex("<details", 0, h.index(">namensprobe</a>")):]
kopf_n = kopf_n[:kopf_n.index("</summary>")]
assert "punkt mint" in kopf_n and "läuft seit" in kopf_n and "ohne Timer" in kopf_n and "kein Lauf" not in kopf_n, kopf_n
assert 'href="https://host-01.example.ts.net:8447/"' in kopf_n and h.count('class="dienst"') == 5, kopf_n
kein_timer["dienste"][-1]["container"][0]["status"] = "exited"
h = web.server_html(kein_timer, jetzt)
assert "punkt glut" in h[h.rindex("<details", 0, h.index(">namensprobe</a>")):h.index(">namensprobe</a>")], "gestoppt: Warnfarbe"
web.SERVER.write_text(json.dumps(kaputt))
assert "unbekannt" in seite() and "Trainingslog: Container wochenlog-wochenlog-1 fehlt" in web.server_lage(kaputt)[0]
web.SERVER.write_text(json.dumps(beispiel))

# 2e: Absturzschutz je Block. Eine künstlich ausgelöste Ausnahme in einem Block ergibt an seiner Stelle eine Zeile in
# Warnfarbe, die Seite rendert (Handler: 200), alle anderen Blöcke sind vollständig. Der Traceback geht ins Log.
import io, contextlib
MARKER = {"Serverstand": 'class="stand"', "Server-Lage": '<div class="tile lage"><p class="f">', "Dienste": 'details class="dienst"',
          "Maschine": ">Produkt<", "Installiert": ">Docker<", "Firewall": ">Zweite Reihe<", "Updates": ">Pakete<",
          "Sammelstand": "GitHub-Daten von", "GitHub-Lage": 'class="nums"', "Kachel": 'class="card tile kpi"', "Repozahl": 'class="n repos-spalte"',
          "Projekte": "<h3 class=\"titel\">Projekte</h3>", "Rückblick": "Rückblick <span",
          "Fusszeile": '<footer class="fusszeile"><span>© '}
def kaputt_fn(*a, **k):
    raise RuntimeError("künstlich")
FEHLER = "Block konnte nicht gezeichnet werden: RuntimeError"
for name, fn, erwartet in (("Serverstand", "server_stand_block", 1), ("Server-Lage", "server_lage_block", 1), ("Dienste", "server_dienste_block", 1),
                           ("Maschine", "fuss_maschine", 1), ("Installiert", "fuss_installiert", 1), ("Firewall", "fuss_firewall", 1), ("Updates", "fuss_updates", 1),
                           ("Sammelstand", "stand_block", 1), ("GitHub-Lage", "lage_block", 1), ("Kachel", "kachel_block", 5), ("Repozahl", "repos_html", 1),
                           ("Projekte", "projekte_block", 1), ("Rückblick", "rueckblick_block", 1),
                           ("Fusszeile", "fusszeile", 1)):
    echt = getattr(web, fn)
    setattr(web, fn, kaputt_fn)
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            s = seite()
    finally:
        setattr(web, fn, echt)
    assert s.count(FEHLER) == erwartet and "<html" in s, (name, s.count(FEHLER))
    assert 'data-block="' + name in s or (name == "Kachel" and 'data-block="Kachel Commits heute"' in s), name
    for andere, marker in MARKER.items():
        if andere != name and (name, andere) != ("GitHub-Lage", "Repozahl"):   # die Repozahl steckt im Kopf und fällt mit ihm
            assert marker in s, f"{name} kaputt, aber {andere} fehlt ({marker})"
    assert "Traceback (most recent call last)" in log.getvalue() and 'raise RuntimeError("künstlich")' in log.getvalue(), name
assert FEHLER not in seite(), "ohne Störung keine Fehlerzeile"
# Die Teilstücke der Projektliste hängen in derselben Isolation: je Zeile ein Block, darin Mikrokurve, Wochenkurve
# und Stand je ein eigener. Bricht eines, steht die Fehlerzeile genau einmal je Repo, der Rest der Zeile und der
# Seite bleibt. Die Zahl der Repos kommt aus den Rohdaten.
n_roh = len(gelistet_roh())
for fn, name, bleibt in (("projekt_zeile", "Projektzeile", ()), ("mikrokurve", "Mikrokurve", ('class="pmeta"', 'class="palter"')),
                         ("karte_kurve", "Wochenkurve", ('class="mikro"', 'class="pmeta"')), ("projekt_stand", "Projektstand", ('class="mikro"', 'class="palter"'))):
    echt = getattr(web, fn)
    setattr(web, fn, kaputt_fn)
    fehl = []
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            s = seite(fehler=fehl)
    finally:
        setattr(web, fn, echt)
    assert n_roh == 2 and s.count(FEHLER) == n_roh and s.count(f'data-block="{name} ') == n_roh, (name, s.count(FEHLER))
    assert all(marker in s for marker in MARKER.values()), name
    assert s.count("<details class=\"prow") == (0 if name == "Projektzeile" else n_roh), name
    for b in bleibt:
        assert s.count(b) == n_roh, (name, b)
    assert fehl == [f"{name} neu", f"{name} ruhig"], (name, fehl)
assert FEHLER not in seite()
# Handler antwortet 200, auch wenn ein Block kaputt ist
class Antwort:
    def __init__(self): self.code = None; self.body = b""
    def send_response(self, code): self.code = code
    def send_header(self, *a): pass
    def end_headers(self): pass
    def __getattr__(self, n): return None
echt_pb, web.projekte_block = web.projekte_block, kaputt_fn
try:
    from urllib.parse import urlparse
    h_ = web.Handler.__new__(web.Handler); h_.wfile = io.BytesIO(); h_.kopf_nur = False; h_.headers = {}
    h_.send_response = lambda code: setattr(h_, "code", code); h_.send_header = lambda *a: None; h_.end_headers = lambda: None
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        h_.bedienen(urlparse("/"))
    assert h_.code == 200 and FEHLER.encode() in h_.wfile.getvalue(), h_.code
finally:
    web.projekte_block = echt_pb
# 2f: server.json und deploy.json je als ungültiges JSON, als leere Datei und mit einem Dienst-Eintrag ohne jeden
# Schlüssel: die Seite rendert jedes Mal (Handler: 200). Ungültig und leer sind seit 11.10.2026 «nicht lesbar» (Codex-Audit, Befund 6,
# vorher «fehlt»); der leere Eintrag betrifft nur seine eigene Zeile (Befund 8, vorher brach er Lage und Dienste mit KeyError).
def antwortcode():
    h_ = web.Handler.__new__(web.Handler); h_.wfile = io.BytesIO(); h_.kopf_nur = False; h_.headers = {}
    h_.send_response = lambda code: setattr(h_, "code", code); h_.send_header = lambda *a: None; h_.end_headers = lambda: None
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        h_.bedienen(urlparse("/"))
    return h_.code, h_.wfile.getvalue().decode()
for inhalt in ("{", "", "[1, 2"):
    web.SERVER.write_text(inhalt); web.DEPLOY.write_text(inhalt)
    code, s = antwortcode()
    assert code == 200 and "Serverstand nicht lesbar" in s and "Noch kein Serverstand" not in s and "Rückblick <span" in s, (repr(inhalt), code)
ohne = json.loads(json.dumps(beispiel)); ohne["dienste"] = [{}]
web.SERVER.write_text(json.dumps(ohne)); web.DEPLOY.write_text(json.dumps({"fernwerk-studio/neu": {}}))
code, s = antwortcode()
assert code == 200 and "Block konnte nicht gezeichnet werden" not in s and "?\u00a0Eintrag nicht gemessen" in s.replace(":", "") \
    and "nicht gemessen (Eintrag ungültig, server.json)" in s and ">Produkt<" in s and "Rückblick <span" in s, code
web.SERVER.write_text(json.dumps(beispiel)); web.DEPLOY.unlink()

# 4: die zweite Reihe. Vorhanden (iptables, sieben INPUT- und vier DOCKER-USER-Regeln je Familie, gemessen
# am 08.09.2026 nach dem Neustart 17:03) ist grün; leere Kette, fehlende Persistenz, Abweichung vom
# gespeicherten Stand und gar keine Reihe sind Befunde, keine grünen Zustände.
fw = json.loads(json.dumps(beispiel))
fw["erhoben_utc"] = web.iso_utc(jetzt)      # Abschnitt 3 hat die Datei altern lassen, das färbt sonst die Standzeile
fw["zustand"]["neustart_noetig"] = False
h = web.server_html(fw, jetzt)
assert "iptables, ufw nicht installiert" in h and "INPUT 7" in h and "DOCKER-USER 4" in h and "IPv6" in h, "zweite Reihe benannt"
assert "netfilter-persistent aktiv" in h and "wieder da, Stand vom 08.09.,\u00a016:52" in h and "glut" not in h, h[h.index("Zweite Reihe"):][:400]
assert not web.firewall_befunde(fw["firewall"]), "vollständige Reihe ist kein Befund"

leer = json.loads(json.dumps(fw))
leer["firewall"]["familien"]["IPv4"]["DOCKER-USER"] = 0
assert web.firewall_befunde(leer["firewall"]) == ["Firewall IPv4: Kette DOCKER-USER leer"]
h = web.server_html(leer, jetzt)
assert '<span class="glut">DOCKER-USER leer</span>' in h and "Kette DOCKER-USER leer" in web.server_lage(leer)[0]

ohne = json.loads(json.dumps(fw))
ohne["firewall"]["persistent"] = False
assert web.firewall_befunde(ohne["firewall"]) == ["netfilter-persistent nicht aktiv: die Regeln überleben keinen Neustart"]
assert '<span class="glut">netfilter-persistent nicht aktiv</span>' in web.server_html(ohne, jetzt)

weicht = json.loads(json.dumps(fw))
weicht["firewall"].update(wie_gespeichert=False, seit_neustart=False)
assert web.firewall_befunde(weicht["firewall"]) == ["Firewall: der laufende Stand weicht vom gespeicherten ab"]
assert "weicht vom gespeicherten Stand ab" in web.server_html(weicht, jetzt)

keine = json.loads(json.dumps(fw))
keine["firewall"] = {"art": "keine", "ufw": None, "familien": {}, "persistent": False, "wie_gespeichert": False,
                     "gespeichert_utc": None, "seit_neustart": False}
assert web.firewall_befunde(keine["firewall"]) == ["keine zweite Reihe: weder ufw noch Regeln in iptables"]
assert '<span class="glut">keine, weder ufw noch iptables</span>' in web.server_html(keine, jetzt)
# Fremde Hosts (11.10.2026): None heisst nicht gemessen und ist kein Befund; eine fehlende Kette DOCKER-USER (Rootless-Docker, Podman) ist
# nicht leer; nftables steht mit seinem Namen als nicht gemessen.
ufw_host = json.loads(json.dumps(fw))
ufw_host["firewall"] = {"art": "ufw", "ufw": "active", "familien": {"IPv4": {"INPUT": 6, "DOCKER-USER": 3}}, "nicht_gemessen": [],
                        "persistent": None, "wie_gespeichert": None, "gespeichert_utc": None, "seit_neustart": False}
assert not web.firewall_befunde(ufw_host["firewall"]), web.firewall_befunde(ufw_host["firewall"])
h = web.server_html(ufw_host, jetzt)
assert "weicht" not in h and "netfilter-persistent nicht aktiv" not in h and "glut" not in h[h.index("Zweite Reihe"):][:600], h[h.index("Zweite Reihe"):][:600]
rootless = json.loads(json.dumps(ufw_host))
rootless["firewall"].update(art="iptables", ufw=None, familien={"IPv4": {"INPUT": 2}})
assert not web.firewall_befunde(rootless["firewall"]) and "DOCKER-USER" not in web.server_html(rootless, jetzt)
nft = json.loads(json.dumps(ufw_host))
nft["firewall"].update(art="nftables", ufw=None, familien={})
nft["ungemessen"] = {"firewall.nftables": "nftables-Regeln wertet v1 nicht aus"}
assert not web.firewall_befunde(nft["firewall"])
h = web.server_html(nft, jetzt)
assert 'title="nftables-Regeln wertet v1 nicht aus">nicht gemessen' in h and "glut" not in h[h.index("Zweite Reihe"):][:600]
alt_json = json.loads(json.dumps(fw))
del alt_json["firewall"]   # server.json aus der Zeit vor dieser Messung: die Seite rendert, ohne zu behaupten
assert "noch nicht gemessen" in web.server_html(alt_json, jetzt) and not web.firewall_befunde(None)

# 5: offene Updates nach Herkunft. Null, nur Debian (deckt der Automatismus), nur Docker (von Hand,
# und dann die eine Zahl der Kachel), beide.
def mit(quellen):
    x = json.loads(json.dumps(fw))
    x["zustand"]["pakete_quellen"] = quellen
    x["zustand"]["quellen_auto"] = {q: q.startswith("Debian") for q in quellen}   # so misst tools/server-stand die Deckung auf dem VPS
    x["zustand"]["pakete_upgradebar"] = sum(quellen.values())
    return x

h = web.server_html(mit({}), jetzt)
assert "keine offen" in h and "von Hand" not in h and "läuft seit" in h, "null Updates: die Laufzeit bleibt die Zahl"
assert not web.von_hand(mit({})["zustand"])

nur_debian = mit({"Debian": 4})
assert not web.von_hand(nur_debian["zustand"])
h = web.server_html(nur_debian, jetzt)
assert "4 upgradebar" in h and "4 offen, deckt der Automatismus" in h and "von Hand" not in h and "läuft seit" in h
assert "von Hand zu ziehen" not in web.server_lage(nur_debian)[0]

nur_docker = mit({"Docker CE": 2})
assert web.von_hand(nur_docker["zustand"]) == {"Docker CE": 2}
h = web.server_html(nur_docker, jetzt)
assert "2 offen, von Hand" in h and 'class="blau">2</b><span>Updates von Hand zu ziehen: Docker CE' in h, h[h.index("class=\"n\""):][:300]
assert "Docker CE: zwei Updates offen, von Hand zu ziehen" in web.server_lage(nur_docker)[0]
assert not web.firewall_befunde(nur_docker["firewall"]) and "glut" not in h, "offene Updates sind eine Aufgabe, keine Abweichung"

beide = mit({"Debian": 4, "Docker CE": 1})
assert web.von_hand(beide["zustand"]) == {"Docker CE": 1}
h = web.server_html(beide, jetzt)
assert "5 upgradebar" in h and "4 offen, deckt der Automatismus" in h and "1 offen, von Hand" in h
assert 'class="blau">1</b><span>Update von Hand zu ziehen: Docker CE' in h
assert "Docker CE: ein Update offen, von Hand zu ziehen" in web.server_lage(beide)[0]

# 6: Achsen und Raster. Ohne Skala ist eine Kurve keine Aussage, und genau an den dünnen Fällen
# bricht eine Skala: leere Reihe, ein einziger Punkt, Höchstwert null.
for mx, oben in ((0, 1), (1, 1), (3, 3), (5, 6), (21, 30), (54, 60), (128, 150), (229, 300), (1318, 1500)):
    o, marken = web.achse(mx)
    assert o == oben, (mx, o, oben)
    assert marken[0] == 0 and marken[-1] == o and 2 <= len(marken) <= 5, (mx, marken)
    assert o >= mx, (mx, o)

leere = web.kurve([0, 0, 0], "01.09.", "03.09.", bezug=(0, "Durchschnitt"), max_marke="0")
assert 'style="top:0.0%">1<' in leere and 'style="top:100.0%">0<' in leere, "leere Reihe bekommt Achse 0 bis 1"
assert "pmarke" not in leere and 'class="hz"' not in leere, "Höchstwert null und Durchschnitt null sind keine Aussage"

einer = web.kurve([5], "08.09.", "08.09.", bezug=(5, "Durchschnitt"), max_marke="5 am 08.09.")
assert 'x1="460.0"' in einer and "<path" not in einer and "polygon" not in einer, "ein Punkt: kurzer Strich, keine Fläche"
assert '<div class="xlab"><span class="einzeln">08.09.</span></div>' in einer and '<span style="left:' not in einer, "beide x-Enden gleich: nur eine Beschriftung, mittig"
# Eine einzige Beschriftung (25.09.2026) spannt über die Fläche und steht mittig unter dem Punkt (50%), nie mit der Regel für die letzte.
einzeln_css = " ".join(regeln[".xlab span.einzeln"])
assert all(d in einzeln_css for d in ("left: 0", "right: 0", "transform: none", "text-align: center", "overflow: hidden")), einzeln_css
assert 'x1="460.0"' in einer and 'x2="540.0"' in einer, "der eine Punkt liegt mittig, 500 von 1000"
assert web.kurve([3], "21.09.", "21.09.", klasse="mini", laufend=True, angebrochen={0}).count('class="einzeln"') == 1, "Detailkurve mit einer Woche"
assert 'class="hz"' not in einer, "Durchschnitt über einen Punkt ist kein Vergleich"
assert "pmarke" in einer, "der Höchstwert wird trotzdem benannt"

zwei = web.kurve([2, 8], "07.09.", "08.09.", bezug=(5, "Durchschnitt"))
assert "<path" in zwei and 'class="hz"' in zwei and 'preserveAspectRatio="none"' in zwei
assert "<text" not in zwei, "keine Schrift im gestreckten SVG, sie würde verzerren"

# Legende und Bild aus einer Hand: der Durchschnitt darf nicht im Satz stehen, wenn er im Bild fehlt.
assert web.zeigt_bezug([2, 8], 5) and not web.zeigt_bezug([5], 5) and not web.zeigt_bezug([0, 0], 0)
assert "Ein Punkt" not in seite(), "die Einheit steht in der Linienbenennung, nicht zweimal"

# Kennzahl-Kacheln: jede zeigt genau den Vergleich ihrer Zahl. Endpunkt der vollen Linie = grosse Zahl,
# Endpunkt der gestrichelten = Vergleichswert der Zeile 1 (heute: der Punkt um dieselbe Uhrzeit); Kacheltext
# ohne Klammern, ohne Durchschnitt, höchstens zwei Zeilen; Repos nach der Regel der Karten.
with db.verbinden() as c2:
    tage_ = web.tageszaehlung(c2)
    karten_ = web.projekte(c2, jetzt_zh.date(), jetzt_zh)
    kacheln = web.kacheldaten(c2, jetzt_zh.date(), jetzt_zh)
assert [k["label"] for k in kacheln] == ["Commits heute", "Commits, letzte 7 Tage", "Commits, letzte 30 Tage"]
for k in kacheln:
    if "voll" in k:   # die Heute-Kachel trägt seit 13.09.2026 keine Kurve mehr
        assert (k["voll"][-1][1] if isinstance(k["voll"][-1], tuple) else k["voll"][-1]) == k["n"], k["label"]
    assert "(" not in k["zeile1"] + k["zeile2"] and "Ø" not in k["zeile1"] + k["zeile2"], k
    assert str(k["vor"]) in k["zeile1"], (k["zeile1"], k["vor"])
h_, tw, t30 = kacheln
# Heute-Kachel: ganze Kalendertage, Mitternacht bis Mitternacht in der Zone des Systems. Der Tagesbeginn 06:00
# vom 20.09.2026 ist am selben Tag zurückgebaut — gitpuls wird frei weitergegeben, und dort ist eine eigenwillige
# Tagesgrenze ein Fehler und keine Eigenheit. Die Erwartung bildet die Grenze selbst aus zoneinfo, nicht über
# web.tag0: sonst prüfte sich der Code gegen sich selbst.
roh_alle = lambda c2: [(datetime.fromisoformat(t.replace("Z", "+00:00")), t, r, b) for t, r, b in c2.execute("SELECT zeitstempel_utc, repo, betreff FROM commits WHERE autor_adresse = 'dev@fernwerk-studio.example'")]
# In UTC rechnen: Python addiert und subtrahiert Zeiten derselben Zone als Wanduhr und verliert dabei fold; am 25.10.2026
# um 02:30 CET lag ein so gerechnetes Fensterende eine Stunde zu früh (gemessen 13.09.2026 mit vorgestellter Uhr).
utc_ = lambda d: datetime.combine(d, time(0), web.TZ).astimezone(timezone.utc)
obergrenze_ = lambda d: d.replace(microsecond=0) + (timedelta(seconds=1) if d.microsecond else timedelta(0))
def heute_roh_von(c2, jz):
    t0, g0 = utc_(jz.date()), utc_(jz.date() - timedelta(days=1))
    ende, zeilen = obergrenze_(jz.astimezone(timezone.utc)), roh_alle(c2)
    heute = sorted(((t, r, b) for d, t, r, b in zeilen if t0 <= d < ende), reverse=True)
    return heute, sum(1 for d, *_ in zeilen if g0 <= d < t0)
# Der Tagesbeginn 06:00 ist zurückgebaut: weder Konstante noch die zwei Helfer bleiben stehen.
assert not any(hasattr(web, n) for n in ("TAGESBEGINN", "TAGESBEGINN_ROH", "tagesbeginn", "arbeitstag", "tag_von", "TB")), "Tagesbeginn ersatzlos weg (zweimal: 20.09. und 25.09.2026)"
assert "Tag ab" not in seite() and "tagab" not in seite(), "kein Hinweis auf einen Tagesbeginn in der Oberfläche"
assert web.tag0(jetzt_zh.date()) == utc_(jetzt_zh.date()), (web.tag0(jetzt_zh.date()), utc_(jetzt_zh.date()))
def sieben_roh_von(c2, jz):
    """Die letzten sieben Zürcher Kalendertage bis jetzt (heute zuletzt) und die sieben ganzen Tage davor: je Tag die Zahl, dazu die
    verschiedenen Repos des eigenen Fensters."""
    heute_, zeilen = jz.date(), roh_alle(c2)
    ab_, ende = heute_ - timedelta(days=6), obergrenze_(jz.astimezone(timezone.utc))
    w = [(d, r) for d, _, r, _ in zeilen if utc_(ab_) <= d < ende]
    v = [d for d, _, _, _ in zeilen if utc_(ab_ - timedelta(days=7)) <= d < utc_(ab_)]
    je_tag = lambda tage, a: [sum(1 for d in tage if d.astimezone(web.TZ).date() == a + timedelta(days=i)) for i in range(7)]
    return je_tag([d for d, _ in w], ab_), je_tag(v, ab_ - timedelta(days=7)), len({r for _, r in w})
def vor_roh(c2, jz, tage):
    """Vergleichswert der Kachel über `tage` Tage seit 11.10.2026 (Codex-Audit, Befund 10): die Vorperiode bis zum selben Zeitpunkt, ab
    Mitternacht des ersten Vortags bis zum Tag heute minus `tage` im selben Abstand ab dessen Tagesbeginn."""
    heute_ = jz.date()
    ende = obergrenze_(utc_(heute_ - timedelta(days=tage)) + (jz.astimezone(timezone.utc) - utc_(heute_)))
    return sum(1 for d, *_ in roh_alle(c2) if utc_(heute_ - timedelta(days=2 * tage - 1)) <= d < ende)
kum = lambda xs: [sum(xs[: i + 1]) for i in range(len(xs))]
auf_li = lambda h: h[h.index('<ul class="heute">'):h.index("</ul>", h.index('<ul class="heute">'))].count("<li")   # Zeilen der Aufteilung, ohne Commitliste
def aufteilung_roh(zeilen):
    """Die Aufteilung aus Rohzeilen (t, repo, betreff): je Repo Zahl und letzter Zeitstempel, nach Zahl, dann jüngster zuerst."""
    je = {}
    for t, r, _ in zeilen:
        je.setdefault(r, []).append(t)
    nach_zeit = sorted(((r, len(ts), max(ts)) for r, ts in je.items()), key=lambda x: x[2], reverse=True)
    return sorted(nach_zeit, key=lambda x: -x[1])   # stabil: bei gleicher Zahl bleibt der jüngere vorn
def zuletzt_roh(c2, jz):
    """Ist der Tag von jz leer: der letzte Zürcher Kalendertag davor mit eigenem Commit und seine Rohzeilen, sonst (None, [])."""
    if heute_roh_von(c2, jz)[0]:
        return None, []
    zeilen = roh_alle(c2)
    frueher = [d.astimezone(web.TZ).date() for d, *_ in zeilen if d < utc_(jz.date())]
    if not frueher:
        return None, []
    tag = max(frueher)
    return tag, sorted(((t, r, b) for d, t, r, b in zeilen if utc_(tag) <= d < utc_(tag + timedelta(days=1))), reverse=True)
pro = lambda a, b: (f", {(a - b) * 100 / b:+.0f}%".replace("-", "\u2212").replace("+0%", "0%")) if b else ""   # echtes Minuszeichen seit 25.09.2026
with db.verbinden() as c2:
    heute_r, vor_r = heute_roh_von(c2, jetzt_zh)
    w_tage, w_vor, w_repos = sieben_roh_von(c2, jetzt_zh)
    vor7_jetzt, vor30_jetzt = vor_roh(c2, jetzt_zh, 7), vor_roh(c2, jetzt_zh, 30)
assert h_["n"] == len(heute_r) and h_["aufteilung"] == aufteilung_roh(heute_r) and h_["vor"] == vor_r, (h_["n"], heute_r, h_["vor"], vor_r)
assert h_["zeile2"] == "" and h_["zeile1"] == f"gestern: {vor_r}{pro(len(heute_r), vor_r)}", h_["zeile1"]
assert "um diese Zeit" not in h_["zeile1"] and "vergangen" not in h_, h_
# Kachel letzte 7 Tage (30.09.2026, ersetzt die Kalenderwoche): heute und sechs Tage davor gegen die sieben Tage davor bis zum selben
# Zeitpunkt (seit 11.10.2026, Codex-Audit Befund 10; vorher die ganzen sieben Tage), dazu der gleitende Durchschnitt je Tag über die
# sieben ganzen Tage bis und mit diesem Tag, von Hand aus den 14 Rohtagen. Die gestrichelte 30-Tage-Linie zeigt ganze Tage, ihr Endpunkt
# ist deshalb nicht mehr der Vergleichswert.
assert t30["vor"] == vor30_jetzt and t30["vergleich"][-1] >= t30["vor"] and len(t30["vergleich"]) == 30, (t30["vor"], vor30_jetzt)
assert tw["tage"] == w_tage and tw["n"] == sum(w_tage) and tw["vor"] == vor7_jetzt, (tw["tage"], w_tage, tw["vor"], vor7_jetzt)
vierzehn_ = w_vor + w_tage
assert tw["schnitt"] == [sum(vierzehn_[i + 1:i + 8]) / 7 for i in range(7)] and tw["schnitt"][-1] == sum(w_tage) / 7, tw["schnitt"]
assert tw["zeile1"] == f"7 Tage davor: {vor7_jetzt}{pro(sum(w_tage), vor7_jetzt)}", tw["zeile1"]
assert tw["zeile2"] == f"an {sum(1 for v in w_tage if v)} von 7 Tagen, in {w_repos} Repo{'' if w_repos == 1 else 's'}", tw["zeile2"]
assert len(tw["xlabels"]) == 7 and len(tw["stellen"]) == 7, (tw["xlabels"], tw["stellen"])
assert t30["zeile2"] == "an 3 von 30 Tagen, in 2 Repos" and t30["zeile1"] == "30 Tage davor: 0", t30
s = seite()
for text in ("Commits je Tag · ", "Ø 7 Tage</p>", "letzte 30 Tage · ", "30 Tage davor</p>"):
    assert text in s, text
# Wieder mit Kurven (Entscheid 25.09.2026, nimmt Teil C zurück): in der gezeichneten Seite trägt jede der zwei Kacheln die volle und die
# gestrichelte Reihe, und jede Stelle ihres Zeigers hat die Marke beider Reihen, Wert und y aus den Kacheldaten.
kacheln_k = s[s.index('<div class="tiles3">'):s.index('<section class="card projekte">')]
stellen_k = [[f.split("|") for f in web.html.unescape(g).split(";")] for g in re.findall(r'data-stellen="([^"]+)"', kacheln_k)]
# Seit 30.09.2026 mit Umschaltern: Kachel 2 in den Fenstern 7 Tage und Woche (Balken), Kachel 3 in 30 Tage und Monat (Linien,
# Vormonat seit 04.10.2026 entfallen).
assert kacheln_k.count('<div class="chart klein') == 4 and len(stellen_k) == 4, kacheln_k.count('class="chart')
assert kacheln_k.count('<path class="schnitt"') == 1 and kacheln_k.count('<path class="schnitt ref"') == 1 and kacheln_k.count('<path class="hz"') == 2
n_woche_ = jetzt_zh.weekday() + 1
assert kacheln_k.count('<rect class="b') == 7 + n_woche_ and kacheln_k.count('<rect class="b jetzt laeuft') == 2, "sieben Tagesbalken und die Tage der Woche, heute läuft"
# Die Linien der Kachel 3 sind voll bis zum Punkt (10.10.2026, tests/test_endpunkt.py), kein gepunktetes letztes Stück mehr.
assert kacheln_k.count("<title>läuft noch</title>") == 2 and "l teil" not in kacheln_k and kacheln_k.count('class="pjetzt"') == 2, "laufender Balken, Punkt am Ende beider Linien (30 Tage, Monat)"
# Erwartung in de-CH von Hand (CLDR: Gruppierung mit U+2019), nicht aus dem Formatmodul. Die letzte Stelle einer laufenden Reihe trägt «läuft noch».
ch = lambda v: f"{v:,}".replace(",", "\u2019")
eine_stelle = lambda v: ch(v) if float(v).is_integer() else f"{v:.1f}"
lauf_ = lambda xs: xs[:-1] + [xs[-1] + " · läuft noch"]
for k, st, voll_, vgl_ in ((tw, stellen_k[0], tw["tage"], tw["schnitt"]), (t30, stellen_k[2], t30["voll"], t30["vergleich"])):
    assert [f[2] for f in st] == [ch(v) for v in voll_] and [f[4] for f in st] == [eine_stelle(v) for v in vgl_], (k["label"], st)
    assert all(len(f) == 6 and f[3] and f[5] for f in st) and [f[1] for f in st] == lauf_(k["stellen"]), (k["label"], st)
kacheln_txt = s[s.index('<div class="tiles3">'):s.index('<section class="card projekte">')]
for text in ("Commits, letzte 7 Tage", "7 Tage davor", "Commits diese Woche", "Vorwoche zum selben Zeitpunkt", "Commits, letzte 30 Tage",
             "Commits diesen Monat", "Vormonat zum selben Zeitpunkt"):
    assert text in kacheln_txt, text
# (bis zum Beginn der Projektliste: deren Detailkurven tragen seit 13.09.2026 ein Ø in der Legende)
kacheln_html = s[s.index('<div class="tiles3">'):s.index('<section class="card projekte">')]
assert "Durchschnitt" not in kacheln_html and kacheln_html.count("Ø") == 1 and "Ø 7 Tage" in kacheln_html and "Vorwoche je Tag" in kacheln_html
# Vergleichsreihe im Zeichner: zwei Pfade, Fläche nur unter der vollen Linie und nur bis zu deren letztem Punkt
zwei_reihen = web.kurve([(0, 0), (0.25, 2), (0.5, 3)], "00:00", "24:00", vergleich=[(0, 0), (0.5, 1), (1.0, 4)])
assert zwei_reihen.count("<path") == 2 and zwei_reihen.count("<polygon") == 1 and 'points="0.0,260 0.0,260.0 250.0,130.0 500.0,65.0 500.0,260"' in zwei_reihen, zwei_reihen
assert "Achse bis 4" in zwei_reihen, "die Achse gilt für beide Reihen"
nullen = web.kurve([0, 0, 0, 0], "Tag 1", "Tag 4", vergleich=[0, 0, 0, 0])
assert "Achse bis 1" in nullen and nullen.count('y1="260.0"') >= 1 and "260.0 L333.3,260.0" in nullen, "lauter Nullen: flache Linien auf der Grundlinie"

# Der Zeilenblock trägt die Zahl, nicht drei namenlose Kurven: die Höchstwerte der drei Reihen standen
# am 08.09.2026 bei 128, 81 und 3, ein gemeinsamer Massstab drückt die kleinste in die Grundlinie.
s = seite()
assert "Ruht:" not in s and s.count('<details class="prow"') == 2 and "gedaempft" not in s, "neu und ruhig sind aktiv: zwei ungedämpfte Zeilen, keine Ruhezeile"
assert '<div class="pzeile"' not in s and 'class="pzeilen"' not in s, "kein eigener Block mehr"
assert s.count('class="chart') == 4 + 2 * 3 + 5, "vier Kachelfenster mit Kurve, zwei Projektdetails (neu, ruhig) mit je 7, 30 und 90 Tagen, fünf Fenster des Rückblicks"

# Karenz über den Sammlerpfad: geloescht zuletzt vor 50 Stunden gelistet, aussetzer vor 26 Stunden (fehlt im
# jüngsten Lauf vor 23 Stunden, also EIN verpasster Lauf innerhalb der Karenz), neu und ruhig in jedem Lauf.
assert lauf(z(50), ["fernwerk-studio/neu", "fernwerk-studio/ruhig", "fernwerk-studio/geloescht"]) and lauf(z(26), ["fernwerk-studio/neu", "fernwerk-studio/ruhig", "fernwerk-studio/aussetzer"]) and lauf(z(23), ["fernwerk-studio/neu", "fernwerk-studio/ruhig"])
s = seite()
assert "aussetzer" in s, "ein verpasster Lauf darf kein Repo verschwinden lassen"
assert "geloescht" not in s, "nach der Karenz faellt ein nicht mehr gelistetes Repo weg"
# Kachel-Repozahl: ein Repo ohne Karte (jenseits der Karenz) mit Commits im 7- und 30-Tage-Fenster erhöht Zahl
# und Repozahl der Kacheln gemeinsam; Kopf und Karten bleiben bei der Kartenzählung.
with db.verbinden() as c2:
    tage_ = web.tageszaehlung(c2); vorher7, vorher30 = web.kacheldaten(c2, jetzt_zh.date(), jetzt_zh)[1:]
c = db.verbinden(schreibend=True)
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES ('s9', 'fernwerk-studio/geloescht', ?, 'dev@fernwerk-studio.example')", (gestern,))
c.commit(); c.close()
with db.verbinden() as c2:
    tage_ = web.tageszaehlung(c2); k7, k30 = web.kacheldaten(c2, jetzt_zh.date(), jetzt_zh)[1:]
# Vorher je Fenster: 7 Tage nur neu, 30 Tage neu und ruhig.
for vorher, nachher, repos in ((vorher7, k7, ("in 1 Repo", "in 2 Repos")), (vorher30, k30, ("in 2 Repos", "in 3 Repos"))):
    assert nachher["n"] == vorher["n"] + 1, (vorher["n"], nachher["n"])
    assert sum(nachher["tage"]) == sum(vorher["tage"]) + 1 if "tage" in vorher else nachher["voll"][-1] == vorher["voll"][-1] + 1, vorher["label"]
    assert vorher["zeile2"].endswith(repos[0]) and nachher["zeile2"].endswith(repos[1]), (vorher["zeile2"], nachher["zeile2"])
s = seite()
assert "geloescht" not in s, "Kopf und Karten zählen weiter nach der Karenz"
assert [(t, n) for t, n, _, _ in gruppen_von(s)] == [("aktiv", 1), ("Ohne Commits diese Woche", 2)], "Zahl unverändert: neu, ruhig, aussetzer; geloescht bleibt draussen"
# Ein Repo, dessen einziger Commit nach jetzt liegt, verändert weder Zahl noch Kurve noch Repozahl: alle drei
# Werte stammen aus einer Abfrage mit Unter- und Obergrenze.
c = db.verbinden(schreibend=True)
c.commit(); c.close()
assert lauf(z(0.25), ["fernwerk-studio/neu", "fernwerk-studio/ruhig", "fernwerk-studio/aussetzer", "fernwerk-studio/morgen"])
c = db.verbinden(schreibend=True)
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES ('z1', 'fernwerk-studio/morgen', ?, 'dev@fernwerk-studio.example')", ((jetzt + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),))
c.commit(); c.close()
with db.verbinden() as c2:
    z7, z30 = web.kacheldaten(c2, jetzt_zh.date(), jetzt_zh)[1:]
for vorher, nachher, felder_ in ((k7, z7, ("n", "tage", "schnitt", "zeile2")), (k30, z30, ("n", "voll", "vergleich", "zeile2"))):
    assert [nachher[f] for f in felder_] == [vorher[f] for f in felder_], (vorher["zeile2"], nachher["zeile2"])
c = db.verbinden(schreibend=True); c.execute("DELETE FROM commits WHERE sha = 'z1'"); c.execute("DELETE FROM repos WHERE repo = 'fernwerk-studio/morgen'"); c.commit(); c.close()

# Tagesgrenzen an den Zürcher Wechseltagen 2026: 25.10. (03:00 CEST -> 02:00 CET, 25 Stunden) und 29.03.
# (02:00 CET -> 03:00 CEST, 23 Stunden); im Herbst liegen die zwei 02-Uhr-Stunden getrennt. Seit dem
# 20.09.2026 zählt die Heute-Kachel den ganzen Kalendertag bis jetzt gegen den ganzen gestrigen, der
# Vergleichswert ist an einem Wechseltag also zu jeder Uhrzeit derselbe. Geprüft bleiben beide Tage.
# Die Wechseltage laufen in einer eigenen Datenbank: ihre Commits liegen auf festen Daten, und steht die Uhr des Tests
# auf einem dieser Tage oder dem Tag danach, fielen die relativen Commits von neu (heute, gestern) in dieselben Intervalle.
from datetime import date
haupt_db, db.DB = db.DB, Path(tempfile.mkdtemp()) / "wechseltage.db"
c = db.verbinden(schreibend=True)
for sha, z in (("o3", "2026-10-24T23:30:00Z"), ("o1", "2026-10-25T00:30:00Z"), ("o2", "2026-10-25T01:30:00Z"), ("o4", "2026-10-24T00:30:00Z"),
               ("m1", "2026-03-29T00:30:00Z"), ("m2", "2026-03-29T01:30:00Z"), ("m3", "2026-03-28T00:30:00Z")):
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES (?, 'fernwerk-studio/neu', ?, 'dev@fernwerk-studio.example')", (sha, z))
c.commit(); c.close()
zh = lambda *a, fold=0: datetime(*a, tzinfo=web.TZ, fold=fold)
def heute_kachel(jetzt):
    with db.verbinden() as c2:
        return web.kacheldaten(c2, jetzt.date(), jetzt)[0]
assert web.tag0(date(2026, 10, 25)) == datetime(2026, 10, 24, 22, tzinfo=timezone.utc) and web.tag0(date(2026, 10, 26)) == datetime(2026, 10, 25, 23, tzinfo=timezone.utc)
# Herbst: o4 liegt am 24.10. (02:30 CEST), die drei Nachtcommits (01:30 und zweimal 02:30, vor und nach dem
# Sprung) am 25.10. Der Vergleichswert ist der ganze 24.10. und deshalb zu jeder Uhrzeit derselbe; nur die
# eigene Zahl wächst über den Tag, weil die Obergrenze jetzt ist.
for jz_, n_ in ((zh(2026, 10, 25, 1, 45), 1), (zh(2026, 10, 25, 2, 45, fold=0), 2),
                (zh(2026, 10, 25, 2, 45, fold=1), 3), (zh(2026, 10, 25, 5, 0), 3), (zh(2026, 10, 25, 12, 0), 3)):
    k = heute_kachel(jz_)
    assert (k["n"], k["vor"]) == (n_, 1), (jz_, k["n"], k["vor"], n_)
    assert k["zeile1"] == f"gestern: 1{pro(n_, 1)}" and "um diese Zeit" not in k["zeile1"], k["zeile1"]
assert heute_kachel(zh(2026, 10, 25, 5, 0))["aufteilung"] == [("fernwerk-studio/neu", 3, "2026-10-25T01:30:00Z")]
assert web.tag0(date(2026, 3, 29)) == datetime(2026, 3, 28, 23, tzinfo=timezone.utc) and web.tag0(date(2026, 3, 30)) == datetime(2026, 3, 29, 22, tzinfo=timezone.utc)
# Frühling: m1 (01:30 CET) und m2 (03:30 CEST) liegen beide am 29.03., m3 am 28.03.
for jz_, n_ in ((zh(2026, 3, 29, 1, 45), 1), (zh(2026, 3, 29, 3, 45), 2), (zh(2026, 3, 29, 12, 0), 2)):
    k = heute_kachel(jz_)
    assert (k["n"], k["vor"]) == (n_, 1), (jz_, k["n"], k["vor"], n_)
# Randfälle der Tagesgrenze an einem normalen Tag, eigene Datenbank: ein Commit um 23:59:59 gehört zum alten
# Kalendertag, einer um 00:00:00 zum neuen. Beide zählen genau einmal, keiner zweimal und keiner gar nicht.
haupt_r, db.DB = db.DB, Path(tempfile.mkdtemp()) / "randfaelle.db"
c = db.verbinden(schreibend=True)
# 16./17.09.2026, Sommerzeit: lokale Mitternacht des 17.09. ist 2026-09-16T22:00:00Z.
rand = (("r1", "2026-09-16T21:59:59Z", "eine Sekunde vor Mitternacht"), ("r2", "2026-09-16T22:00:00Z", "genau Mitternacht"),
        ("r0", "2026-09-16T12:00:00Z", "derselbe Tag, am Nachmittag"))
for sha, z_, b in rand:
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, 'fernwerk-studio/neu', ?, 'dev@fernwerk-studio.example', ?)", (sha, z_, b))
c.commit(); c.close()
assert web.tag0(date(2026, 9, 17)) == datetime(2026, 9, 16, 22, tzinfo=timezone.utc)
# 23:59:59.5 lokal: die Obergrenze rundet die angebrochene Sekunde auf, r1 ist damit drin, r2 noch nicht.
k_vor = heute_kachel(zh(2026, 9, 16, 23, 59, 59, 500000))
assert k_vor["n"] == 2 and k_vor["aufteilung"] == [("fernwerk-studio/neu", 2, "2026-09-16T21:59:59Z")] and k_vor["zuletzt"] is None, k_vor
k_nach = heute_kachel(zh(2026, 9, 17, 0, 0, 1))
assert k_nach["n"] == 1 and k_nach["aufteilung"] == [("fernwerk-studio/neu", 1, "2026-09-16T22:00:00Z")] and k_nach["zuletzt"] is None, k_nach
# Rückfall an der Grenze (Kriterium 4): genau um 00:00:00 ist der neue Tag noch leer (die Obergrenze ist halboffen), die Kachel
# zeigt den 16.09. mit r0 und r1 und NICHT den Commit von 00:00:00; eine halbe Sekunde später zählt dieser heute, ohne Rückfall.
k_null = heute_kachel(zh(2026, 9, 17, 0, 0, 0))
assert (k_null["n"], k_null["zuletzt"], k_null["aufteilung"]) == (0, date(2026, 9, 16), [("fernwerk-studio/neu", 2, "2026-09-16T21:59:59Z")]), k_null
assert '<span class="delta bezug heute-kopf">Zuletzt gearbeitet, Mi.,\u00a016.9., in 1 Repo</span>' in web.kachel_block(k_null) and '<span class="big mid">0</span>' in web.kachel_block(k_null)
k_halb = heute_kachel(zh(2026, 9, 17, 0, 0, 0, 500000))
assert (k_halb["n"], k_halb["zuletzt"], k_halb["aufteilung"]) == (1, None, [("fernwerk-studio/neu", 1, "2026-09-16T22:00:00Z")]), k_halb
assert sum(n for _, n, _ in k_null["aufteilung"]) + k_halb["n"] == len(rand), "jeder Commit genau einmal"
assert k_nach["vor"] == 2 and k_nach["zeile1"] == f"gestern: 2{pro(1, 2)}" == "gestern: 2, \u221250%", k_nach["zeile1"]
assert k_vor["n"] + k_nach["n"] == 3 == len(rand) and k_vor["vor"] == 0, (k_vor["n"], k_nach["n"], k_vor["vor"])
db.DB = haupt_r

# Die Wochenkachel an denselben zwei Tagen (beide Sonntage): das Vergleichsfenster endet im selben ABSTAND ab dem
# Tagesbeginn des Vorwochentags, nicht bei «jetzt minus sieben Tage» und nicht auf derselben Wanduhrzeit. Nur so
# sind beide Fenster exakt gleich lang; am 29.03. wären es sonst 11 Stunden gegen 12 (der Tag hat 23 Stunden), und
# eine angebrochene Periode stünde gegen eine längere.
def wochenkachel(jz):
    with db.verbinden() as c2:
        return web.kacheldaten(c2, jz.date(), jz)[1]
for jz_, tage_erw in ((zh(2026, 10, 25, 5, 0), [0, 0, 0, 0, 0, 1, 3]), (zh(2026, 3, 29, 12, 0), [0, 0, 0, 0, 0, 1, 2])):
    kw_ = wochenkachel(jz_)
    montag_ = jz_.date() - timedelta(days=jz_.weekday())
    # Seit 30.09.2026 die letzten 7 Tage; an einem Sonntag sind das genau Montag bis Sonntag, die Tage bleiben dieselben.
    assert kw_["tage"] == tage_erw and kw_["vor"] == 0, (jz_, kw_["tage"])
    assert kw_["zeile1"] == "7 Tage davor: 0" and kw_["zeile2"].startswith(f"an 2 von 7 Tagen"), (jz_, kw_["zeile1"], kw_["zeile2"])
    jz_u = jz_.astimezone(timezone.utc)
    v_ende = web.tag0(jz_.date() - timedelta(weeks=1)) + (jz_u - web.tag0(jz_.date()))
    assert jz_u - web.tag0(montag_) == v_ende - web.tag0(montag_ - timedelta(weeks=1)), (jz_, "beide Fenster gleich lang")
assert web.tag0(date(2026, 3, 22)) + (zh(2026, 3, 29, 12, 0).astimezone(timezone.utc) - web.tag0(date(2026, 3, 29))) \
       == datetime(2026, 3, 22, 10, tzinfo=timezone.utc), "29.03. hat 23 Stunden: 12 Uhr entspricht 11 Uhr der Vorwoche"

# Die Wochenkachel an einem festen MITTWOCH, in eigener Datenbank: läuft die Uhr des Tests an einem Sonntag, sind
# «Montag der Kalenderwoche» und «heute minus sechs Tage» dasselbe, und ein rollendes Fenster fiele nicht auf
# (gemessen am 20.09.2026, einem Sonntag). Der Sonntag davor gehört zur Vorwoche und darf nicht mitzählen.
haupt_wo, db.DB = db.DB, Path(tempfile.mkdtemp()) / "wochenkachel.db"
jw = datetime(2026, 9, 16, 12, 0, tzinfo=web.TZ)         # Mittwoch
roh_w = [(datetime(2026, 9, 13, 20, 0, tzinfo=web.TZ), "Sonntag der Vorwoche"),
         (datetime(2026, 9, 14, 9, 0, tzinfo=web.TZ), "Montag"),
         (datetime(2026, 9, 16, 9, 0, tzinfo=web.TZ), "Mittwoch früh"),
         (datetime(2026, 9, 16, 13, 0, tzinfo=web.TZ), "Mittwoch nach jetzt"),
         (datetime(2026, 9, 9, 11, 0, tzinfo=web.TZ), "Mittwoch der Vorwoche, vor der Uhrzeit"),
         (datetime(2026, 9, 9, 13, 0, tzinfo=web.TZ), "Mittwoch der Vorwoche, nach der Uhrzeit"),
         (datetime(2026, 9, 7, 8, 0, tzinfo=web.TZ), "Montag der Vorwoche")]
c = db.verbinden(schreibend=True)
for i, (zeitpunkt, betreff) in enumerate(roh_w):
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, 'fernwerk-studio/w', ?, 'dev@fernwerk-studio.example', ?)",
              (f"w{i}", zeitpunkt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), betreff))
c.execute("INSERT INTO repos (repo, default_branch, gesehen) VALUES ('fernwerk-studio/w', 'main', ?)", (web.iso_utc(jw.astimezone(timezone.utc)),))
c.commit(); c.close()
with db.verbinden() as c2:
    kw_mi = web.kacheldaten(c2, jw.date(), jw)[1]
# Seit 30.09.2026 die letzten 7 Tage, Do 10.09. bis Mi 16.09. 12:00: der Sonntag 13.09. zählt jetzt mit, der Commit von 13:00 liegt nach
# jetzt. Die sieben Tage davor (Do 03.09. bis Mi 09.09.) zählen seit 11.10.2026 bis zum selben Zeitpunkt (Codex-Audit, Befund 10): der
# Mittwoch um 13:00 nicht mehr, der um 11:00 schon. Der gleitende Durchschnitt rechnet weiter mit ganzen Tagen.
assert kw_mi["tage"] == [0, 0, 0, 1, 1, 0, 1] and kw_mi["n"] == 3 and kw_mi["vor"] == 2, (kw_mi["tage"], kw_mi["vor"])
assert kw_mi["schnitt"] == [3 / 7, 3 / 7, 3 / 7, 4 / 7, 4 / 7, 4 / 7, 3 / 7], kw_mi["schnitt"]
assert kw_mi["zeile1"] == "7 Tage davor: 2, +50%" and kw_mi["zeile2"] == "an 3 von 7 Tagen, in 1 Repo", (kw_mi["zeile1"], kw_mi["zeile2"])
assert kw_mi["xlabels"] == ["Do.", "Fr.", "Sa.", "So.", "Mo.", "Di.", "Mi."] and len(kw_mi["stellen"]) == 7, (kw_mi["xlabels"], kw_mi["stellen"])
assert kw_mi["legende"].endswith("Ø 7 Tage") and "Commits je Tag" in kw_mi["legende"], kw_mi["legende"]
# Projektzeilen rechnen die Vorwoche seit dem 25.09.2026 bis zum selben Zeitpunkt wie die Kachel (Mi 09.09. 12:00), nicht mehr bis zum Ende
# des Mittwochs: «Mittwoch der Vorwoche, nach der Uhrzeit» (13:00) zählt nicht mehr. Ein zweites Repo prüft die Grenze sekundengenau
# (11:59:59 zählt, 12:00:00 nicht) und den Donnerstag. Erwartung aus den Rohzeilen gegen eine eigene Zeitgrenze, nicht aus der App;
# die Summe der Projektzeilen ist der Vorwochenwert der Kachel, weil beide Repos gelistet sind.
c = db.verbinden(schreibend=True)
c.executemany("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, 'fernwerk-studio/w2', ?, 'dev@fernwerk-studio.example', 'x')",
              [(f"v{i}", datetime(2026, 9, t_, h_, m_, s_, tzinfo=web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
               for i, (t_, h_, m_, s_) in enumerate(((9, 11, 59, 59), (9, 12, 0, 0), (10, 9, 0, 0)))])
c.execute("INSERT INTO repos (repo, default_branch, gesehen) VALUES ('fernwerk-studio/w2', 'main', ?)", (web.iso_utc(jw.astimezone(timezone.utc)),))
c.commit(); c.close()
grenze_vw = datetime(2026, 9, 9, 12, 0, tzinfo=web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
mo_vw = datetime(2026, 9, 7, 0, 0, tzinfo=web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
import sqlite3  # noqa: E402
with sqlite3.connect(f"file:{db.DB}?mode=ro", uri=True) as roh_c:
    roh_vw = dict(roh_c.execute("SELECT repo, COUNT(*) FROM commits WHERE zeitstempel_utc >= ? AND zeitstempel_utc < ? GROUP BY repo", (mo_vw, grenze_vw)))
assert roh_vw == {"fernwerk-studio/w": 2, "fernwerk-studio/w2": 1}, roh_vw
s_vw = web.seite(jetzt=jw)
zeile_vw = dict(re.findall(r'data-repo="(fernwerk-studio/w2?)".*?class="pwert" title="[^"]*Vorwoche zum selben Zeitpunkt (\d+)"', s_vw))
assert {k: int(v) for k, v in zeile_vw.items()} == roh_vw, (zeile_vw, roh_vw)
c = db.verbinden(schreibend=True); c.execute("DELETE FROM commits WHERE repo = 'fernwerk-studio/w2'"); c.execute("DELETE FROM repos WHERE repo = 'fernwerk-studio/w2'"); c.commit(); c.close()
# Fenstergrenze 00:00 des ersten der sieben Tage mit einem zweiten Repo (seit 30.09.2026, vorher Montag 00:00): Mi 09.09. 23:59:59 in
# fernwerk-studio/v gehört zu den sieben Tagen davor, Do 10.09. 00:00:00 genau in fernwerk-studio/w zum Fenster. Ein Fenster, das eine Stunde früher begänne,
# nähme fernwerk-studio/v mit und zählte «in 2 Repos».
c = db.verbinden(schreibend=True)
for sha, repo, zp in (("wg1", "fernwerk-studio/v", datetime(2026, 9, 9, 23, 59, 59, tzinfo=web.TZ)), ("wg2", "fernwerk-studio/w", datetime(2026, 9, 10, 0, 0, 0, tzinfo=web.TZ))):
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, ?, ?, 'dev@fernwerk-studio.example', 'Grenze')",
              (sha, repo, zp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
c.commit(); c.close()
with db.verbinden() as c2:
    kw_g = web.kacheldaten(c2, jw.date(), jw)[1]
    tage_g = web.tageszaehlung(c2)
# Der Vergleich zählt seit 11.10.2026 bis zum selben Zeitpunkt (Codex-Audit, Befund 10): vom Mi 09.09. nur 11:00, nicht 13:00 und nicht
# 23:59:59; dazu Mo 07.09. 08:00, also 2 (vorher 4, die ganzen Tage).
assert kw_g["tage"] == [1, 0, 0, 1, 1, 0, 1] and kw_g["zeile2"] == "an 4 von 7 Tagen, in 1 Repo" and kw_g["vor"] == 2, (kw_g["tage"], kw_g["zeile2"], kw_g["vor"])
assert tage_g[date(2026, 9, 9)] == 3 and tage_g[date(2026, 9, 10)] == 1, tage_g
db.DB = haupt_wo

# Umschalter der Kacheln 2 und 3 (30.09.2026), eigene Datenbank, jetzt Dienstag 31.03.2026 12:00: der Februar ist kürzer, für «Vormonat zum
# selben Zeitpunkt» gilt sein letzter Tag bis 12:00. Erwartungen von Hand aus den Rohzeilen.
haupt_u, db.DB = db.DB, Path(tempfile.mkdtemp()) / "umschalter.db"
ju = datetime(2026, 3, 31, 12, 0, tzinfo=web.TZ)
roh_u = [(1, 15, 10), (2, 1, 9), (2, 28, 11), (2, 28, 13), (3, 1, 10), (3, 23, 8), (3, 24, 11), (3, 24, 13), (3, 29, 10), (3, 30, 10), (3, 31, 9), (3, 31, 13)]
c = db.verbinden(schreibend=True)
for i, (m_, d_, h_) in enumerate(roh_u):
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, 'fernwerk-studio/u', ?, 'dev@fernwerk-studio.example', 'u')",
              (f"u{i}", datetime(2026, m_, d_, h_, 0, tzinfo=web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
c.execute("INSERT INTO repos (repo, default_branch, gesehen) VALUES ('fernwerk-studio/u', 'main', ?)", (web.iso_utc(ju.astimezone(timezone.utc)),))
c.commit(); c.close()
with db.verbinden() as c2:
    var_u = web.kachelvarianten(c2, ju.date(), ju)
(_, wo_u), = var_u["k2"]
(_, mo_u), = var_u["k3"]
# Woche Mo 30.03. bis jetzt: 30.03. und 31.03. 09:00 (13:00 liegt nach jetzt); Vorwoche bis Di 24.03. 12:00: 23.03. und 24.03. 11:00.
assert (wo_u["tage"], wo_u["n"], wo_u["vor"], wo_u["zeile1"]) == ([1, 1], 2, 2, "Vorwoche zum selben Zeitpunkt: 2, 0%"), wo_u
assert wo_u["vorwoche"] == [1, 2, 0, 0, 0, 0, 1] and wo_u["label"] == "Commits diese Woche" and len(wo_u["xlabels"]) == 7, "ganze Vorwoche je Wochentag, sieben Plätze"
g_u = web.kachel_block(wo_u, web.umschalter("k2", "woche"), False)
assert g_u.count("<rect") == 2 and g_u.count('<path class="schnitt ref"') == 1 and g_u.count('<span class="platz"') == 7, "künftige Tage leer, Achse ganze Woche"
# Ganze Vorwoche Mo 23.03. bis So 29.03. als absolute Zahl (26.10.2026): 1 + 2 + 1 aus den Rohdaten, als dritte Metazeile, ohne Prozent.
assert '<span class="delta bezug zeilen">Ganze Vorwoche: <span class="kwert">4</span></span></div>' in g_u, g_u[:1500]
assert all("Ganze Vorwoche" not in web.kachel_block(k) for _, k in var_u["k3"]), "nur in der Fassung Woche"
# Vergleich der Woche (01.10.2026): die Vorwoche je Wochentag, Mo 23.03. 1, Di 24.03. 2, So 29.03. 1, gestrichelt auf den sieben Plätzen;
# vorher eine waagrechte Linie auf dem Tagesmittel 4/7, die nichts verglich. Der Zeiger liest an jedem Tag den Wert der Vorwoche ab.
ref_y = [float(p.split(",")[1]) for p in re.search(r'<path class="schnitt ref" d="M([^"]+)"', g_u).group(1).split(" L")]
assert len(ref_y) == 7 and ref_y[0] == ref_y[6] > ref_y[1] and len(set(ref_y)) == 3, ("Vorwoche je Wochentag", ref_y)
stellen_u = [s.split("|") for s in web.html.unescape(re.search(r'data-stellen="([^"]+)"', g_u).group(1)).split(";")]
assert [s[4] for s in stellen_u] == ["1", "2", "0", "0", "0", "0", "1"], stellen_u
# Monat: 1. bis jetzt 7 Commits; Vormonat zum selben Zeitpunkt 1.02. bis 28.02. 12:00 = 2; die gestrichelte Linie der ganze Februar (3).
assert (mo_u["n"], mo_u["vor"], mo_u["label"], mo_u["zeile1"]) == (7, 2, "Commits diesen Monat", "Vormonat zum selben Zeitpunkt: 2, +250%"), mo_u
assert len(mo_u["voll"]) == 31 and mo_u["voll"][-1] == (1.0, 7) and len(mo_u["vergleich"]) == 28 and mo_u["vergleich"][-1] == (27 / 30, 3) and mo_u["x"] == ("01.03.", "31.03."), mo_u["x"]
s_u = web.seite(jetzt=ju)
for g_, segmente in (("k2", ("7 Tage", "Woche")), ("k3", ("30 Tage", "Monat"))):
    navs = re.findall(rf'<nav class="seg klein umschalter" aria-label="Zeitraum der Kachel" data-gruppe="{g_}">(.*?)</nav>', s_u)
    assert len(navs) == len(segmente) and all(re.findall(r">([^<]+)</button>", n) == list(segmente) for n in navs), (g_, navs)
    assert [re.search(r'data-fenster="(\w+)" aria-pressed="true"', n).group(1) for n in navs] == [x for x, _ in web.UMSCHALTER[g_]], "jedes Fenster drückt sich selbst"
    sichtbar_u = re.findall(rf'<div class="fenster" data-gruppe="{g_}" data-fenster="(\w+)">', s_u)
    assert sichtbar_u == [web.UMSCHALTER[g_][0][0]] * (3 if g_ == "k2" else 1), (g_, sichtbar_u)   # k2: Kachel, Lagesatz, Ring
assert "localStorage.setItem('gitpuls-'+g,f)" in web.SKRIPT and "localStorage.getItem('gitpuls-'+g)" in web.SKRIPT and "localStorage.setItem(nav.dataset.speicher,f)" in web.SKRIPT
assert 'data-speicher="gitpuls-rueckblick"' in s_u and "grid-column: 1 / -1" in " ".join(regeln[".tile .wert.mit-nav > :not(.big):not(.umschalter)"]), "Rückblick gemerkt, Umschalter neben der Zahl"
assert s_u.count('<div class="wert mit-nav"><span class="big mid">') == 4, "Umschalter in der Zahlenzeile aller vier Fenster"
# Gemerkte Wahl vor dem ersten Bild (30.09.2026): Kopfskript und Regeln stehen im head vor dem Stylesheet, die Listen kommen aus UMSCHALTER und
# RUECKBLICK, jedes Fenster hat seine Zeigeregel, ?rueckblick= geht vor, und das Skript am Ende führt das Attribut bei jedem Wechsel nach.
kopf_u = s_u[:s_u.index("</head>")]
assert kopf_u.index(web.WAHL_KOPF) < kopf_u.index('rel="stylesheet"') and '{"k2":["7","woche"],"k3":["30","monat"],"pd":["7","30","90"],"rb":["7","30","60","90","beginn"]}' in web.WAHL_KOPF
for g_, fs_ in web.WAHL.items():
    for f_ in fs_:
        assert (f'html[data-{g_}="{f_}"] .fenster[data-gruppe="{g_}"][data-fenster="{f_}"]' if g_ != "rb" else f'html[data-rb="{f_}"] .rueckblick .fenster[data-fenster="{f_}"]') in web.WAHL_KOPF, (g_, f_)
assert all(f'html[data-pd="{f_}"] nav[data-gruppe="pd"] button[data-fenster="{f_}"]' in web.WAHL_KOPF for f_ in ("7", "30", "90")), "gedrückter Knopf im Detail vor dem Skript"
assert "localStorage.getItem('gitpuls-'+g)" in web.SKRIPT and "['k2','k3','pd'].forEach" in web.SKRIPT and "if(!n.closest('.fenster'))" in web.SKRIPT
assert "has('rueckblick'))continue" in web.WAHL_KOPF and "try{" in web.WAHL_KOPF and "}catch(e){}" in web.WAHL_KOPF
assert "wurzel.setAttribute('data-'+g,f)" in web.SKRIPT and "if(nav.dataset.speicher)wurzel.setAttribute('data-rb',f)" in web.SKRIPT
db.DB = haupt_u

# Heute-Kachel als Aufteilung je Repo (seit 25.09.2026), in einer eigenen Datenbank mit festem Tag: eine Zeile je Repo mit Name,
# Zahl, Balken und «bis HH:MM», nach Zahl, bei Gleichstand nach letztem Commit; so viele, wie in die Kachel passen, darunter
# «und N weitere Repos». Ein Commit GENAU um lokale Mitternacht zählt zum laufenden Tag, einer eine Sekunde davor zum vorigen,
# einer nach jetzt zu keinem; Zahl, Aufteilung und Repozahl stammen aus demselben Fenster. Die Erwartung aus den Rohzeilen.
db.DB = Path(tempfile.mkdtemp()) / "heute.db"
c = db.verbinden(schreibend=True)
jz = datetime(2026, 9, 16, 12, 0, tzinfo=web.TZ)         # 10:00Z, Mitternacht des 16.09. = 2026-09-15T22:00:00Z
# Fünf Repos (Kriterium 1): alpha 3, beta 2, gamma 2 (Gleichstand, gamma jünger), delta 1, epsilon 1 (epsilon jünger).
heute_daten = (("h1", "fernwerk-studio/alpha", "2026-09-15T22:00:00Z"), ("h2", "fernwerk-studio/beta", "2026-09-16T02:10:00Z"),
               ("h3", "fernwerk-studio/alpha", "2026-09-16T05:00:00Z"), ("h4", "fernwerk-studio/gamma", "2026-09-16T07:30:00Z"),
               ("h5", "fernwerk-studio/beta", "2026-09-16T08:15:00Z"), ("h6", "fernwerk-studio/alpha", "2026-09-16T09:59:59Z"),
               ("h7", "fernwerk-studio/gamma", "2026-09-16T09:00:00Z"), ("h8", "fernwerk-studio/delta", "2026-09-16T03:00:00Z"), ("h9", "fernwerk-studio/epsilon", "2026-09-16T04:00:00Z"),
               ("x1", "fernwerk-studio/alpha", "2026-09-15T21:59:59Z"), ("x2", "fernwerk-studio/alpha", "2026-09-16T10:00:01Z"),
               ("g1", "fernwerk-studio/beta", "2026-09-15T06:00:00Z"))
for sha, repo, t in heute_daten:
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, ?, ?, 'dev@fernwerk-studio.example', ?)", (sha, repo, t, sha))
c.commit(); c.close()
with db.verbinden() as c2:
    hk = web.kacheldaten(c2, jz.date(), jz)[0]
    erwartet_h, erwartet_vor = heute_roh_von(c2, jz)
    # Eigenes SQL je Repo: letzter Commit und Zahl im Zürcher Kalendertag bis jetzt.
    sql_je_repo = {r: (n, t) for r, n, t in c2.execute("SELECT repo, COUNT(*), MAX(zeitstempel_utc) FROM commits WHERE zeitstempel_utc >= '2026-09-15T22:00:00Z' AND zeitstempel_utc <= '2026-09-16T10:00:00Z' GROUP BY repo")}
assert len(erwartet_h) == 9 and hk["n"] == 9 and hk["aufteilung"] == aufteilung_roh(erwartet_h) and hk["vor"] == erwartet_vor == 2, (hk["n"], hk["aufteilung"])
assert [r.split("/")[1] for r, _, _ in hk["aufteilung"]] == ["alpha", "gamma", "beta", "epsilon", "delta"], hk["aufteilung"]
# Die Repozahl steht beim Tag der Liste, nicht hinter dem Vergleichswert (Nachtrag 25.09.2026).
assert hk["zeile1"] == "gestern: 2, +350%" and "Repo" not in hk["zeile1"], hk["zeile1"]
kh = web.kachel_block(hk)
eintraege = re.findall(r'<li(?: class="nur-schmal")?><a href="#([^"]*)" title="([^"]*)"><span class="hrepo">([^<]*)</span><span class="hzahl">([^<]*)</span>'
                       r'<span class="fbalken" aria-hidden="true"><i style="width:([\d.]+)%"></i></span><span class="uhr">bis ([^<]*)</span></a></li>', kh)
# Kriterium 1: fünf Zeilen, Summe gleich Kachelzahl, Reihenfolge nach Zahl, Uhrzeit gleich letztem Commit je Repo aus eigenem SQL.
assert len(eintraege) == 5 and sum(int(z) for *_, z, _, _ in eintraege) == hk["n"] == 9, eintraege
assert [int(z) for *_, z, _, _ in eintraege] == sorted((int(z) for *_, z, _, _ in eintraege), reverse=True)
for anker_, repo, name, zahl, breite, uhr_ in eintraege:
    n_sql, t_sql = sql_je_repo[repo]
    assert (anker_, name, int(zahl), uhr_) == ("p-" + repo.replace("/", "_2f_"), repo.split("/")[1], n_sql, datetime.fromisoformat(t_sql.replace("Z", "+00:00")).astimezone(web.TZ).strftime("%H:%M")), (repo, anker_, zahl, uhr_)
    assert abs(float(breite) - n_sql * 100 / 3) < .06, ("Balken gegen das grösste Repo des Tages", repo, breite)
kh_auf = kh[:kh.index("</ul>") + 5]   # die Aufteilung; darunter steht seit 25.09.2026 die Commitliste
assert "heute-rest" not in kh and "nur-schmal" not in kh_auf and "zbox" not in kh and "Noch kein Commit" not in kh and "Zuletzt gearbeitet" not in kh, kh
assert '<span class="big mid">9</span>' in kh and '<div class="kmeta"><span class="delta bezug heute-kopf">Heute in 5 Repos</span></div><ul class="heute">' in kh, kh[:600]
# Stylesheet: Name einzeilig mit Ellipse, Zahl in Monospace mit tabular-nums, Balken einfarbig und ohne Farbwechsel, unter 480 px
# fallen Balken und Uhrzeit weg.
hrepo = " ".join(regeln[".hrepo"])
assert "text-overflow: ellipsis" in hrepo and "white-space: nowrap" in hrepo, hrepo
hzahl = " ".join(regeln[".hzahl"])
assert "monospace" in hzahl and "tabular-nums" in hzahl, hzahl
schmal480 = css[css.index("@media (max-width: 480px)"):]
assert re.search(r"\.fbalken, \.heute \.uhr \{ display: none; \}", schmal480), schmal480[:400]
assert "background: var(--eis)" in " ".join(regeln[".fbalken i"]) and not any("glut" in d for d in regeln[".fbalken i"]), regeln[".fbalken i"]
# vier Commits in drei Repos, einer, keiner
with db.verbinden() as c2:
    vier = web.kacheldaten(c2, jz.date(), datetime(2026, 9, 16, 9, 31, tzinfo=web.TZ))[0]   # 07:31Z: h1 bis h4, h8, h9
    einer = web.kacheldaten(c2, jz.date(), datetime(2026, 9, 16, 0, 30, tzinfo=web.TZ))[0]   # nur h1, direkt nach der Grenze
assert vier["n"] == 6 and auf_li(web.kachel_block(vier)) == 5 and "heute-rest" not in web.kachel_block(vier), vier["n"]
assert einer["n"] == 1 and auf_li(web.kachel_block(einer)) == 1 and '<span class="delta bezug heute-kopf">Heute in 1 Repo</span>' in web.kachel_block(einer), einer
# Kriterium 3: heute null, letzter Commit vor fünf Tagen. Die Kachel nennt den Tag und dessen Aufteilung, die grosse 0 bleibt;
# nicht «gestern»: der Tag davor ist leer.
jz5 = datetime(2026, 9, 21, 12, 0, tzinfo=web.TZ)
with db.verbinden() as c2:
    fuenf = web.kacheldaten(c2, jz5.date(), jz5)[0]
    tag_r, zeilen_r = zuletzt_roh(c2, jz5)
assert tag_r == date(2026, 9, 16) == jz5.date() - timedelta(days=5) and fuenf["zuletzt"] == tag_r, (tag_r, fuenf["zuletzt"])
assert fuenf["n"] == 0 and fuenf["aufteilung"] == aufteilung_roh(zeilen_r) and len(zeilen_r) == 10, (fuenf["aufteilung"], zeilen_r)
kf = web.kachel_block(fuenf)
assert '<span class="big mid">0</span>' in kf and '<div class="kmeta"><span class="delta bezug heute-kopf">Zuletzt gearbeitet, Mi.,\u00a016.9., in 5 Repos</span></div><ul class="heute">' in kf and auf_li(kf) == 5, kf
assert fuenf["zeile1"] == "gestern: 0", fuenf["zeile1"]
# Randfall leere Datenbank: «Noch kein Commit heute», keine Liste, kein Blockfehler.
haupt_h, db.DB = db.DB, Path(tempfile.mkdtemp()) / "ganz-leer.db"
db.verbinden(schreibend=True).close()
with db.verbinden() as c2:
    leer = web.kacheldaten(c2, date(2026, 9, 20), datetime(2026, 9, 20, 12, 0, tzinfo=web.TZ))[0]
kl = web.kachel_block(leer)
assert leer["n"] == 0 and leer["zuletzt"] is None and '<p class="heute-leer">Noch kein Commit heute</p>' in kl and "<ul" not in kl and "Zuletzt" not in kl, kl
s_leer = web.seite(jetzt=datetime(2026, 9, 20, 12, 0, tzinfo=web.TZ))
assert "Noch kein Commit heute" in s_leer and "blockfehler" not in s_leer, "leere Datenbank ohne Blockfehler"
db.DB = haupt_h
# Platz: Zeilen aus Kachelhöhe und Zeilenhöhe, Untergrenze zwei, Obergrenze acht, die Restzeile nimmt eine Zeile. Die
# Erwartung rechnet die Zeilen unabhängig aus Platz und Zeile: Zeilen z brauchen z × Zeile + (z − 1) × Abstand.
zeilen_fuer = lambda platz: max(z for z in range(0, 50) if z * web.HEUTE_ZEILE_PX + max(z - 1, 0) * web.HEUTE_ABSTAND_PX <= platz)
# 9 passen bei 190.95 px (gemessen 26.10.2026, vorher 8 bei 173.58 px): 9 × 17.91 + 8 × 3.19 = 186.71, die Obergrenze bleibt acht.
assert zeilen_fuer(web.HEUTE_PLATZ_PX) == 9 and min(web.HEUTE_MAX, zeilen_fuer(web.HEUTE_PLATZ_PX)) == 8, zeilen_fuer(web.HEUTE_PLATZ_PX)
for platz in (10, 30, 60, 100, 172.17, 182.97, 400, 1000):
    grenze = max(2, min(8, zeilen_fuer(platz)))
    for n_ in range(0, 25):
        sichtbar, rest = web.heute_zeilen(n_, platz)
        assert sichtbar + rest == n_ and sichtbar <= 8 and (sichtbar >= 2 or sichtbar == n_), (platz, n_, sichtbar, rest)
        if n_ <= grenze:
            assert (sichtbar, rest) == (n_, 0), (platz, n_, sichtbar, rest)
        else:   # Restzeile zählt mit, ausser die Untergrenze zwei hält die Einträge
            assert sichtbar == max(2, grenze - 1) and rest > 0, (platz, n_, sichtbar, rest)
# Kriterium 2: zwölf Repos an einem Tag. Breit so viele Zeilen, wie der gemessene Platz fasst, eine davon ist die Restzeile mit
# der Zahl der übrigen Repos aus den Rohzeilen; schmal ALLE Repos ohne Obergrenze und ohne Restzeile (Nachtrag 25.09.2026).
# Die Kopfzeile liegt im Abstand über der Liste und kostet keinen Zeilenplatz, im Rückfall wie heute.
c = db.verbinden(schreibend=True)
for i in range(12):
    for j in range(12 - i):
        c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, ?, ?, 'dev@fernwerk-studio.example', 'x')",
                  (f"z{i}-{j}", f"fernwerk-studio/repo{i:02d}", f"2026-09-18T{6 + j % 6:02d}:{i * 3:02d}:{j:02d}Z"))
c.commit(); c.close()
jz12 = datetime(2026, 9, 18, 16, 0, tzinfo=web.TZ)
with db.verbinden() as c2:
    k12 = web.kacheldaten(c2, jz12.date(), jz12)[0]
    roh12, _ = heute_roh_von(c2, jz12)
    k12z = web.kacheldaten(c2, date(2026, 9, 19), datetime(2026, 9, 19, 12, 0, tzinfo=web.TZ))[0]   # leerer Tag, Rückfall auf den 18.
passt = min(web.HEUTE_MAX, zeilen_fuer(web.HEUTE_PLATZ_PX))   # die Obergrenze acht gilt auch bei mehr Platz
k12h = web.kachel_block(k12)
li12 = re.findall(r'<li( class="nur-schmal")?><a href="#p-fernwerk-studio_2f_(repo\d\d)"', k12h)
assert len({r for _, r, _ in roh12}) == 12 == len(k12["aufteilung"]) and k12["n"] == len(roh12), (len(roh12), k12["aufteilung"])
assert [r for _, r in li12] == [f"repo{i:02d}" for i in range(12)], ("alle zwölf im HTML", li12)
assert [bool(x) for x, _ in li12] == [False] * (passt - 1) + [True] * (len(li12) - passt + 1), (passt, li12)
assert f'<details class="gruppe heute-rest"><summary>und {12 - (passt - 1)} weitere Repos<span class="chev" aria-hidden="true"></span></summary></details>' in k12h, k12h[-200:]
# Aufklapper (04.10.2026) ohne eigenen Inhalt: die übrigen Repos stehen als nur-schmal in derselben Liste, offen zeigt sie das Stylesheet.
assert re.search(r'</ul><details class="gruppe heute-rest"><summary>[^<]*<span class="chev" aria-hidden="true"></span></summary></details>', k12h), k12h[-300:]
assert ".heute:has(+ details.heute-rest[open]) li.nur-schmal { display: grid; }" in (web.WEB / "style.css").read_text()
assert "details.hbeintrag > summary::before, details.heute-rest > summary::before { content: none; }" in (web.WEB / "style.css").read_text(), "nur der Chevron rechts, Text auf der Namenskante"
assert ".heute-rest > summary { color: inherit; }" in (web.WEB / "style.css").read_text(), "Restzeile in --lbl wie vor dem Aufklapper"
assert "<details class=\"gruppe heute-rest\" open>" in web.kachel_block(dict(k12, offen=True)), "offen mit ?heute=offen"
k12zh = web.kachel_block(k12z)
# Rückfall «Zuletzt gearbeitet»: dieselbe Vergleichszeile mit Doppelpunkt und Wert in voller Textfarbe (25.09.2026).
assert "Zuletzt gearbeitet" in k12zh and re.search(r'<span class="delta">gestern: <span class="vwert">\d+</span>', k12zh), k12zh[:600]
assert k12z["zuletzt"] == date(2026, 9, 18) and f'<details class="gruppe heute-rest"><summary>und {12 - (passt - 1)} weitere Repos<span' in k12zh and auf_li(k12zh) == 12, k12zh[-200:]
assert web.heute_liste([("fernwerk-studio/a", 3, "2026-09-18T08:00:00Z"), ("fernwerk-studio/b", 2, "2026-09-18T08:00:00Z"), ("fernwerk-studio/c", 1, "2026-09-18T08:00:00Z")], None)[:web.heute_liste([("fernwerk-studio/a", 3, "2026-09-18T08:00:00Z"), ("fernwerk-studio/b", 2, "2026-09-18T08:00:00Z"), ("fernwerk-studio/c", 1, "2026-09-18T08:00:00Z")], None).index("</ul>")].count("<li>") == 3
# Commitliste unter der Aufteilung (25.09.2026). Platz unabhängig nachgerechnet: gemessener Platz ab der Liste minus die Zeilen
# der Aufteilung samt Restzeile, 12 px Abstand, Zeile «Zuletzt» mit Zeilenabstand und 4 px Rand; jeder Eintrag Zeile plus Abstand.
def plaetze_roh(zeilen):
    # Die Werte der Liste stehen hier als Messwerte (Platz vom 26.10.2026, Zeile und Abstand vom 25.09.2026), nicht aus web: sonst prüfte
    # sich der Code gegen sich selbst.
    frei = 190.95 - (zeilen * 17.91 + max(zeilen - 1, 0) * 3.19) - 12 - 17.91 - 3.19 - 4
    return max(n for n in range(0, 20) if n == 0 or n * 17.91 + (n - 1) * 3.19 <= frei)
assert [web.heute_commit_plaetze(z) for z in range(1, 9)] == [plaetze_roh(z) for z in range(1, 9)] and web.heute_commit_plaetze(3) == 4 and web.heute_commit_plaetze(5) == 2 and web.heute_commit_plaetze(7) == 0, [web.heute_commit_plaetze(z) for z in range(1, 9)]
liste_von = lambda h: re.findall(r'<li( class="nur-(?:breit|schmal)")?><span class="hzeit">([^<]*)</span><span class="hcrepo">([^<]*)</span><span class="hcbetreff" title="([^"]*)" translate="no">([^<]*)</span></li>', h)
db_liste = db.DB
db.DB = Path(tempfile.mkdtemp()) / "heute-liste.db"
c = db.verbinden(schreibend=True)
jl = datetime(2026, 9, 16, 20, 0, tzinfo=web.TZ)
for i in range(9):
    for j in range(3):
        c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, ?, ?, 'dev@fernwerk-studio.example', ?)",
                  (f"l{i}-{j}", f"fernwerk-studio/l{i}", datetime(2026, 9, 16, 8 + j * 3, i * 5, tzinfo=web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), f"Betreff {i}-{j} <&>"))
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES ('lx', 'fernwerk-studio/l0', '2026-09-16T18:30:00Z', 'dev@fernwerk-studio.example', 'nach jetzt')")
c.commit(); c.close()
def heute_mit(repos_, jz_):
    c = db.verbinden(schreibend=True); c.execute("DELETE FROM commits WHERE repo NOT IN (%s)" % ",".join("?" * len(repos_)), repos_); c.commit(); c.close()
    with db.verbinden() as c2:
        return web.kacheldaten(c2, jz_.date(), jz_)[0], heute_roh_von(c2, jz_)[0]
# (b) 9 Repos (sieben Zeilen und «und 2 weitere Repos») und 7 Repos: nebeneinander keine Liste und keine Zeile «Zuletzt»; untereinander fünf.
k9, roh9 = heute_mit([f"fernwerk-studio/l{i}" for i in range(9)], jl)
h9 = web.kachel_block(k9)
assert '<details class="gruppe heute-rest"><summary>und 2 weitere Repos<span class="chev" aria-hidden="true"></span></summary>' in h9 and '<div class="heute-zuletzt nur-schmal"><p>Zuletzt</p>' in h9 and [x for x, *_ in liste_von(h9)] == [' class="nur-schmal"'] * 5, h9[-900:]
k7, roh7 = heute_mit([f"fernwerk-studio/l{i}" for i in range(7)], jl)
h7 = web.kachel_block(k7)
# Mit 7 Repos ohne Restzeile passt seit der einzeiligen Codezeile (193.59 px, 25.09.2026) nebeneinander kein Eintrag mehr, untereinander
# fünf; bei 210.97 px war es einer.
assert "heute-rest" not in h7 and '<div class="heute-zuletzt nur-schmal"><p>Zuletzt</p>' in h7 and [x[0] for x in liste_von(h7)] == [' class="nur-schmal"'] * 5, h7[-600:]
# (a, c) 3 Repos: vier Einträge nebeneinander (plaetze_roh(3), seit 26.10.2026; vorher drei), untereinander fünf; die Liste ist das Tagesfenster aus den Rohzeilen,
# jüngste zuerst, der Commit nach jetzt fehlt; Betreff voll im title, escaped.
k3, roh3 = heute_mit(["fernwerk-studio/l0", "fernwerk-studio/l1", "fernwerk-studio/l2"], jl)
h3 = web.kachel_block(k3)
e3 = liste_von(h3)
erwartet3 = [(datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(web.TZ).strftime("%H:%M"), r.split("/")[1], web.html.escape(b), web.html.escape(b)) for t, r, b in roh3[:5]]
assert [tuple(x[1:]) for x in e3] == erwartet3 and len(roh3) == 9 and "nach jetzt" not in h3, (e3, erwartet3)
assert [x[0] for x in e3] == [""] * plaetze_roh(3) + [' class="nur-schmal"'] * (5 - plaetze_roh(3)) and plaetze_roh(3) == 4, e3
assert '<div class="heute-zuletzt"><p>Zuletzt</p><ul class="hcommits">' in h3 and h3.index('<ul class="heute">') < h3.index('<div class="heute-zuletzt">'), h3[-900:]
assert [z[0] for z in roh3] == sorted((z[0] for z in roh3), reverse=True), "jüngste zuerst"
# (d) Rückfall: am 17.09. leer, die Liste zeigt den 16.09., dieselben Zeilen wie die Aufteilung.
with db.verbinden() as c2:
    kr = web.kacheldaten(c2, date(2026, 9, 17), datetime(2026, 9, 17, 12, 0, tzinfo=web.TZ))[0]
    roh_r = sorted(((t, r, b) for d, t, r, b in roh_alle(c2) if utc_(date(2026, 9, 16)) <= d < utc_(date(2026, 9, 17))), reverse=True)
hr = web.kachel_block(kr)
assert kr["zuletzt"] == date(2026, 9, 16) and [tuple(x[1:3]) for x in liste_von(hr)] == [(datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(web.TZ).strftime("%H:%M"), r.split("/")[1]) for t, r, _ in roh_r[:5]], hr[-600:]
assert "nach jetzt" in [b for _, _, b in roh_r][0] and "nach jetzt" in hr, "im Rückfall ist der ganze Tag das Fenster"
assert sum(n for _, n, _ in kr["aufteilung"]) == len(roh_r) == len(kr["commits"]), "Aufteilung und Liste aus denselben Zeilen"
# Stylesheet: Betreff einzeilig mit Ellipse, Uhrzeit Monospace und gedämpft, schmal ohne Repo-Spalte, Umschaltung nach Breite.
assert all(d in " ".join(regeln[".hcbetreff"]) for d in ("white-space: nowrap", "text-overflow: ellipsis")) and "monospace" in " ".join(regeln[".hzeit"]) and "var(--lbl)" in " ".join(regeln[".hzeit"])
assert ".hcrepo { display: none; }" in css[css.index("@media (max-width: 480px)"):], "unter 480 px ohne Repo-Spalte"
assert "@media (max-width: 820px) { .hcommits li.nur-breit { display: none; } .hcommits li.nur-schmal { display: grid; } .heute-zuletzt.nur-schmal { display: grid; } }" in css
# Blockisolation der Liste: bricht sie, bleibt die Aufteilung.
echt_hc = web.heute_commits
web.heute_commits = kaputt_fn
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        hk_ = web.kachel_block(k3)
finally:
    web.heute_commits = echt_hc
assert 'data-block="Heute-Commits"' in hk_ and hk_.count("Block konnte nicht gezeichnet werden") == 1 and auf_li(hk_) == 3, hk_[-400:]
db.DB = db_liste
schmal_css = css[css.index("@media (max-width: 820px) { .heute li.nur-schmal"):]
assert "display: none" in " ".join(regeln[".heute li.nur-schmal"]) and ".heute-rest { display: none; }" in schmal_css.split("\n")[0], schmal_css[:160]
# Ausrichtung: ohne Commit überhaupt nimmt die Leerzeile die Resthöhe der Kachel und steht darin senkrecht mittig; mit Commits
# bleibt die Liste oben bündig und die Regel greift nicht.
leer_css = " ".join(regeln[".tile.kpi .heute-leer"])
assert all(d in leer_css for d in ("flex: 1", "display: flex", "align-items: center")), leer_css
assert "heute-leer" not in kh and "align-items: center" not in " ".join(regeln[".heute"]), "Liste oben bündig"
s_h = web.seite(jetzt=jz)
assert s_h.count('class="heute"') == 1 and 'data-block="Kachel Commits heute"' not in s_h and s_h.count('class="zbox"') >= 2, "Heute-Kachel auf der Seite, zwei Kacheln behalten Kurve und Zeiger"
# Mikrokurve an festem Tag in eigener Datenbank: voll hat Commits in allen zwölf Kalenderwochen, einmal genau Montag 00:30 Zürcher
# Zeit (Sonntag 22:30Z, gehört zur neuen Woche) und einmal eine Minute vor dem Fensterbeginn (zählt nicht); einwoche hat nur in der
# Woche vor vier Wochen Commits; leer nur vor zwanzig Wochen; klein hat eine sehr kleine Woche (1 Commit vor sechs Wochen) neben einer
# grossen (60 vor zwei Wochen): 20 × 1 / 60 ergäbe 0.33 px, die Untergrenze setzt 2 px. Das Fenster ist für alle Zeilen dasselbe.
db.DB = Path(tempfile.mkdtemp()) / "mikro.db"
jm = datetime(2026, 9, 16, 12, 0, tzinfo=web.TZ)   # Mittwoch, KW 38
montag_m = datetime(2026, 9, 14, tzinfo=web.TZ)
roh_m = [("fernwerk-studio/voll", montag_m - timedelta(weeks=k) + timedelta(days=1, hours=10, minutes=i)) for k in range(12) for i in range(k % 4 + 1)]
roh_m += [("fernwerk-studio/voll", montag_m - timedelta(weeks=3) + timedelta(minutes=30)), ("fernwerk-studio/voll", montag_m - timedelta(weeks=11, minutes=1)),
          ("fernwerk-studio/einwoche", montag_m - timedelta(weeks=4) + timedelta(days=2, hours=9)), ("fernwerk-studio/einwoche", montag_m - timedelta(weeks=4) + timedelta(days=4, hours=9)),
          ("fernwerk-studio/leer", montag_m - timedelta(weeks=20)), ("fernwerk-studio/klein", montag_m - timedelta(weeks=6) + timedelta(days=1, hours=9))]
roh_m += [("fernwerk-studio/klein", montag_m - timedelta(weeks=2) + timedelta(days=3, hours=8, minutes=i)) for i in range(60)]
roh_m += [("fernwerk-studio/klein", montag_m - timedelta(weeks=1) + timedelta(days=2, hours=9))]   # 27.09.2026: ein Tag mit 1 neben 60 im 30-Tage-Fenster
roh_m += [("fernwerk-studio/eins", montag_m + timedelta(days=1, hours=10))]   # ein einziger Datenpunkt: nur die laufende Woche
# fix/teilwochen: zwei volle Wochen (4, 6) neben einer grossen laufenden (9); eine volle Woche (3) neben der laufenden (1); ein erster Commit
# am Samstag, die Woche bleibt voll (8, 2, 2, laufend 0), sonst läge der Ø bei 2.0 statt 4.0.
roh_m += [("fernwerk-studio/zwei", montag_m - timedelta(weeks=2) + timedelta(days=3, hours=10, minutes=i)) for i in range(4)]
roh_m += [("fernwerk-studio/zwei", montag_m - timedelta(weeks=1) + timedelta(days=2, hours=10, minutes=i)) for i in range(6)]
roh_m += [("fernwerk-studio/zwei", montag_m + timedelta(days=1, hours=9, minutes=i)) for i in range(9)]
roh_m += [("fernwerk-studio/einvoll", montag_m - timedelta(weeks=1) + timedelta(days=1, hours=10, minutes=i)) for i in range(3)] + [("fernwerk-studio/einvoll", montag_m + timedelta(days=1, hours=11))]
roh_m += [("fernwerk-studio/mitte", montag_m - timedelta(weeks=3) + timedelta(days=5, hours=10, minutes=i)) for i in range(8)]
roh_m += [("fernwerk-studio/mitte", montag_m - timedelta(weeks=2) + timedelta(days=1, hours=10, minutes=i)) for i in range(2)]
roh_m += [("fernwerk-studio/mitte", montag_m - timedelta(weeks=1) + timedelta(hours=10, minutes=i)) for i in range(2)]
roh_m += [("fernwerk-studio/tage", montag_m + timedelta(minutes=30)), ("fernwerk-studio/tage", montag_m + timedelta(days=2, hours=9)), ("fernwerk-studio/tage", montag_m + timedelta(days=2, hours=15)),
          ("fernwerk-studio/tage", montag_m + timedelta(days=3, hours=10))] + [("fernwerk-studio/tage", montag_m + timedelta(days=1, hours=8, minutes=i)) for i in range(60)]
c = db.verbinden(schreibend=True)
for i, (repo, t) in enumerate(roh_m):
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, ?, ?, 'dev@fernwerk-studio.example', 'b')", (f"m{i}", repo, t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
for repo in ("fernwerk-studio/voll", "fernwerk-studio/einwoche", "fernwerk-studio/leer", "fernwerk-studio/klein", "fernwerk-studio/eins", "fernwerk-studio/zwei", "fernwerk-studio/einvoll", "fernwerk-studio/mitte", "fernwerk-studio/tage"):
    c.execute("INSERT INTO repos (repo, default_branch, gesehen) VALUES (?, 'main', ?)", (repo, jm.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
c.commit(); c.close()
s_m = web.seite(jetzt=jm)
with db.verbinden() as c2:
    werte_m = {r: tage_roh(c2, r, jm.date()) for r in ("fernwerk-studio/voll", "fernwerk-studio/einwoche", "fernwerk-studio/leer", "fernwerk-studio/klein")}
x_m = {r: pruefe_balken(re.search(r'<svg class="mikro".*?</svg>', summary_von(s_m, r)).group(0), w) for r, w in werte_m.items()}
assert len({tuple(x) for x in x_m.values()}) == 1, x_m
# Fenster 18.08. bis 16.09.2026. voll: je Dienstag 10:00 Commits, dazu Montag 24.08. 00:30 Zürcher Zeit (Sonntag 22:30Z) — der gehört auf den
# Montag (Index 6), nicht auf den Sonntag davor (Index 5).
assert werte_m["fernwerk-studio/voll"][5] == 0 and werte_m["fernwerk-studio/voll"][6] == 1 and sum(werte_m["fernwerk-studio/voll"]) > 1, ("Tagesgrenze nach Zürcher Zeit", werte_m["fernwerk-studio/voll"])
assert werte_m["fernwerk-studio/einwoche"] == [0, 1, 0, 1] + [0] * 26, werte_m["fernwerk-studio/einwoche"]
b_ein = balken_von(summary_von(s_m, "fernwerk-studio/einwoche"))
assert [h for _, _, _, h in b_ein] == [1, 20, 1, 20] + [1] * 26, ("Balken an der Stelle ihrer Tage", b_ein)
assert [h for _, _, _, h in balken_von(summary_von(s_m, "fernwerk-studio/leer"))] == [1] * 30, "ohne Commit im Fenster 30 Grundstriche"
assert werte_m["fernwerk-studio/klein"] == [0] * 16 + [60] + [0] * 5 + [1] + [0] * 7 and 20 * 1 / 60 < 1, werte_m["fernwerk-studio/klein"]
assert [h for _, _, _, h in balken_von(summary_von(s_m, "fernwerk-studio/klein"))] == [1] * 16 + [20] + [1] * 5 + [2] + [1] * 7, "sehr kleiner Tag: Untergrenze 2 px, nicht Grundstrich"
# Detailkurve mit Bezugsgrösse (13.09.2026): gestrichelt das Mittel des Repos über seine vollen Wochen ab der ersten Woche mit Commits,
# in der Legende mit Wert und dem Maximum der Achse. Seit fix/teilwochen fällt die laufende Woche aus dem Mittel und ihr Segment ist
# gestrichelt; unter zwei vollen Wochen keine Mittellinie und kein Ø. Die erste Woche bleibt drin, das Fenster beginnt an einem Montag. Erwartung aus den Rohzeilen in Zürcher Datum; das Achsenmaximum nach der
# dokumentierten Regel (erster Schritt aus 1, 2, 5, 10, 20, 25, 50 …, bei dem das Maximum in vier Schritte passt, aufgerundet).
def achse_roh(mx):
    schritt = next(x for x in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000) if mx <= 4 * x)
    return -(-mx // schritt) * schritt
def pruefe_grafikbalken(g, werte, oben, px_h, angebrochen=(), laufend=False, plaetze=None, laeuft=False):
    """Balken einer Grafik für Commits je Periode (27.09.2026) gegen die Rohwerte: ein Balken je Periode in der Mitte seines Platzes,
    gedämpft (Klasse b), der laufende Wert in Akzentfarbe (b jetzt), eine angebrochene Periode als Umriss (b teil, 1.5 px Kontur nach
    innen), leer ein Grundstrich von 1 px, mit Commits mindestens 2 px; keine Fläche und keine Linie der Reihe, Farbe nie als Attribut.
    plaetze: feste Zahl von Plätzen, die Werte füllen die ersten (Detailgrafik: 7 Tage). Gibt die Mitten in Prozent zurück."""
    e, m = 260 / px_h, plaetze or len(werte)
    # laeuft (30.09.2026): die letzte Periode läuft noch, Klasse laeuft und title «läuft noch» im Balken.
    rects = re.findall(r'<rect class="([^"]+)" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"(?:/>|><title>läuft noch</title></rect>)', g)
    assert g.count("<title>läuft noch</title>") == (1 if laeuft else 0), laeuft
    assert len(rects) == len(werte) and "flaeche" not in g and '<path class="l' not in g and "fill=" not in g and "stroke=" not in g, (len(rects), len(werte))
    mitten = []
    for i, ((kl, x_, y_, b_, h_), v) in enumerate(zip(rects, werte)):
        teil = i in angebrochen
        assert kl == "b" + (" teil" if teil else " jetzt" if laufend and i == len(werte) - 1 else "") + (" laeuft" if laeuft and i == len(werte) - 1 else "") \
            + ("" if v else " leer"), (i, kl, v)
        voll_h = max(2 * e, 260 * v / oben) if v else e
        soll = voll_h - 1.5 * e if teil and v else voll_h
        assert abs(float(h_) - soll) < .02 and abs(float(y_) + float(h_) - (260 - (.75 * e if teil and v else 0))) < .02, (i, v, h_, y_)
        mitte = float(x_) + float(b_) / 2
        assert abs(mitte - (i + .5) / m * 1000) < .11 and abs(float(b_) - 1000 / m * .7) < .06, (i, x_, b_)
        mitten.append(mitte)
    return mitten


# Detailgrafik (27.09.2026): die sieben Tage der laufenden Kalenderwoche auf sieben festen Plätzen, für jedes Repo dieselbe Breite; vorher
# zwölf Wochen ab der ersten mit Commits, auf die volle Breite gestreckt. Erwartung je Wochentag aus den Rohzeilen in Zürcher Datum, bis
# jetzt (halboffen): Balken nur bis heute (heute in Akzentfarbe), danach kein Balken und keine Ablesestelle, keine Mittellinie, Summe
# gleich dem Wochenwert der Zeile. Beschriftung je Platz mit kurzem Wochentag, von Hand notiert, nicht aus app/format.py.
WT_DE = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."]
wt_m = jm.weekday()
breiten_m = set()
for repo in ("fernwerk-studio/voll", "fernwerk-studio/einwoche", "fernwerk-studio/klein", "fernwerk-studio/eins", "fernwerk-studio/leer", "fernwerk-studio/zwei", "fernwerk-studio/einvoll", "fernwerk-studio/mitte", "fernwerk-studio/tage"):
    tage_w = [sum(1 for r_, t in roh_m if r_ == repo and t < jm and t.astimezone(web.TZ).date() == montag_m.date() + timedelta(days=i)) for i in range(7)]
    assert sum(tage_w[wt_m + 1:]) == 0, (repo, "nach heute liegt bis jetzt nichts")
    bisher = tage_w[:wt_m + 1]
    oben_roh = achse_roh(max(bisher)) if max(bisher) else 1
    zm = zeile_von(s_m, repo)
    assert f'<span class="pzahl">{sum(tage_w)}</span>' in summary_von(s_m, repo), (repo, "Summe der Tage gleich Wochenwert der Zeile", tage_w)
    detail = zm[zm.index('class="pdetail"'):]
    lz = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", z)).strip() for z in re.findall(r'<span class="lz">(.*?)</span>', re.search(r'<div class="pkurve">.*?<p class="sub legende2">(.*?)</p></div>', detail).group(1))]
    assert lz == [f"Commits je Tag · Achse bis {oben_roh}"], (repo, lz)
    g_m = re.search(r'<div class="pkurve">(.*?)<p class="sub legende2">', detail).group(1)
    assert f'<span style="top:0.0%">{oben_roh}</span>' in g_m, (repo, "Legende und Achse nennen dasselbe Maximum")
    assert 'class="hz"' not in g_m and "pmarke" not in g_m and 'class="pjetzt"' not in g_m, (repo, "keine Mittellinie, keine Wertmarke")
    mitten_m = pruefe_grafikbalken(g_m, bisher, oben_roh, 96, laufend=True, plaetze=7)
    breiten_m |= {r[3] for r in re.findall(r'<rect class="([^"]+)" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)"', g_m)}
    felder_m = [f.split("|") for f in web.html.unescape(re.search(r'data-stellen="([^"]+)"', g_m).group(1)).split(";")]
    assert len(felder_m) == wt_m + 1 and all(abs(m - float(f[0])) < .11 and f[2] == str(v) for m, f, v in zip(mitten_m, felder_m, bisher)), (repo, "Ablesestellen nur bis heute, auf den Balkenmitten")
    assert re.findall(r'<span class="platz" style="left:([\d.]+)%">([^<]+)</span>', g_m) == [(f"{100 * (i + .5) / 7:.1f}", WT_DE[i]) for i in range(7)], (repo, g_m[-500:])
assert breiten_m == {f"{700 / 7:.1f}"}, ("eine Balkenbreite für alle Repos", breiten_m)
# fernwerk-studio/tage: Montag 00:30 Zürcher Zeit (Sonntag 22:30Z) gehört auf Montag, 60 am Dienstag, 1 am Mittwoch (1/60 von 96 px wäre 1.6 px, die
# Untergrenze setzt 2), Mittwoch 15:00 und Donnerstag liegen nach jetzt und zählen nicht.
assert [sum(1 for r_, t in roh_m if r_ == "fernwerk-studio/tage" and t < jm and t.astimezone(web.TZ).date() == montag_m.date() + timedelta(days=i)) for i in range(3)] == [1, 60, 1]
assert '<span class="pzahl">62</span>' in summary_von(s_m, "fernwerk-studio/tage") and "Achse bis 60</span></p>" in zeile_von(s_m, "fernwerk-studio/tage")
# Zukunftscommit in der Projektzeile (fix/teilwochen), nach dem Vorbild der Kacheln: jetzt ist Mittwoch 16.09.2026 12:00. Repo y hat
# einen Commit am Montag und einen am Donnerstag 17.09. in derselben Kalenderwoche, Repo z einen am Dienstag. Erwartung aus den Rohzeilen
# bis jetzt: z steht vor y, y zeigt Alter, Betreff und Wochenwert nur des Montagscommits, der Zukunftscommit steht nirgends.
haupt_m, db.DB = db.DB, Path(tempfile.mkdtemp()) / "zukunft-zeile.db"
c = db.verbinden(schreibend=True)
roh_z = [("z_y1", "fernwerk-studio/y", datetime(2026, 9, 14, 9, 0, tzinfo=web.TZ), "normal am Montag"), ("z_y2", "fernwerk-studio/y", datetime(2026, 9, 17, 10, 0, tzinfo=web.TZ), "aus der Zukunft"),
         ("z_z1", "fernwerk-studio/z", datetime(2026, 9, 15, 9, 0, tzinfo=web.TZ), "normal am Dienstag")]
for sha, repo, t, b in roh_z:
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, ?, ?, 'dev@fernwerk-studio.example', ?)", (sha, repo, t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), b))
for repo in ("fernwerk-studio/y", "fernwerk-studio/z"):
    c.execute("INSERT INTO repos (repo, default_branch, gesehen) VALUES (?, 'main', ?)", (repo, jm.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
c.commit(); c.close()
s_z = web.seite(jetzt=jm)
bis_jetzt = [(repo, t, b) for _, repo, t, b in roh_z if t <= jm]
juengster = {repo: max((t, b) for r_, t, b in bis_jetzt if r_ == repo) for repo in ("fernwerk-studio/y", "fernwerk-studio/z")}
assert s_z.index('data-repo="fernwerk-studio/z"') < s_z.index('data-repo="fernwerk-studio/y"'), "Sortierung nach jüngstem Commit bis jetzt"
zy = summary_von(s_z, "fernwerk-studio/y")
tage_y = (jm.date() - juengster["fernwerk-studio/y"][0].date()).days
assert f'<span class="palter">vor {tage_y} Tagen</span>' in zy and tage_y == 2, zy
assert f'class="pbetreff" title="{juengster["fernwerk-studio/y"][1]}"' in zy and "aus der Zukunft" not in zeile_von(s_z, "fernwerk-studio/y"), zy
woche_y = sum(1 for r_, t, _ in bis_jetzt if r_ == "fernwerk-studio/y" and t.date() >= jm.date() - timedelta(days=jm.weekday()))
assert f'<span class="pzahl">{woche_y}</span>' in zy and woche_y == 1, zy
with db.verbinden() as c2:
    k_y = next(k for k in web.projekte(c2, jm.date(), jm) if k["repo"] == "fernwerk-studio/y")
assert k_y["tage_her"] == tage_y and [z["betreff"] for z in k_y["letzte"]] == ["normal am Montag"], k_y
db.DB = haupt_m
assert achse_roh(5) == 6 and achse_roh(60) == 60 and achse_roh(1) == 1 and achse_roh(229) == 300, "Regel der Achse an Randwerten"
assert "Achse bis 1</span></p>" in zeile_von(s_m, "fernwerk-studio/voll") and "Achse bis 10</span></p>" in zeile_von(s_m, "fernwerk-studio/zwei"), "zwei Repos, zwei Massstäbe, beide genannt"

# Rückblick mit drei Fenstern (13.09.2026), eigene Datenbank, fester Tag Mittwoch 16.09.2026. Die Commits liegen je auf beiden
# Seiten einer Fenstergrenze in Zürcher Zeit, der erste auf 00:30 Uhr am 21.04., in UTC noch der 20.04. Die Erwartung zählt mit
# eigenem SQL über UTC-Grenzen aus Zürcher Mitternacht, Tage und Wochen in Python aus den Rohzeilen; Daten stehen als Literal.
db.DB = Path(tempfile.mkdtemp()) / "rueckblick.db"
jr = datetime(2026, 9, 16, 12, 0, tzinfo=web.TZ)
roh_r = ["2026-04-20T22:30:00Z", "2026-05-05T09:00:00Z", "2026-06-18T21:59:00Z", "2026-06-18T22:00:00Z",
         *["2026-07-08T09:00:00Z"] * 30, "2026-08-17T21:59:59Z", "2026-08-17T22:00:00Z", *["2026-09-14T08:00:00Z"] * 3, "2026-09-16T08:00:00Z"]
c = db.verbinden(schreibend=True)
for i, t in enumerate(roh_r):
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES (?, 'fernwerk-studio/neu', ?, 'dev@fernwerk-studio.example')", (f"r{i}", t))
c.commit(); c.close()
mitternacht = lambda d: datetime.combine(d, time(0), web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def summe_sql(von, bis):
    with db.verbinden() as c2:
        return c2.execute("SELECT COUNT(*) FROM commits WHERE zeitstempel_utc >= ? AND zeitstempel_utc < ? AND autor_adresse = 'dev@fernwerk-studio.example'",
                          (mitternacht(von), mitternacht(bis + timedelta(days=1)))).fetchone()[0]
zh_tage = [datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(web.TZ).date() for t in roh_r]
stellen_von = lambda g: [t.split("|") for t in web.html.unescape(re.search(r'data-stellen="([^"]+)"', g).group(1)).split(";")]
rb_teil = lambda s, f: s[s.index(f'<div class="fenster" data-fenster="{f}"'):s.index("</p></div>", s.index(f'<div class="fenster" data-fenster="{f}"'))]
rb_kopf = lambda s: dict(re.findall(r'<span class="fenster" data-fenster="(\w+)"(?: hidden)?>([^<]*)</span>', s))
sichtbar = lambda s: [f for f, h in re.findall(r'<div class="fenster" data-fenster="(\w+)"( hidden)?>', s[s.index('<section class="card rueckblick">'):s.index('</section>', s.index('<section class="card rueckblick">'))]) if not h]
s_r = web.seite(jetzt=jr)
rb = s_r[s_r.index('<section class="card rueckblick">'):s_r.index('</section>', s_r.index('<section class="card rueckblick">'))]
d30, d90, d0, dh = date(2026, 8, 18), date(2026, 6, 19), date(2026, 4, 21), date(2026, 9, 16)
assert min(zh_tage) == d0 and summe_sql(d0, dh) == len(roh_r) == 40, "erster Tag nach Zürcher Datum, nicht nach UTC"
n30, n90 = summe_sql(d30, dh), summe_sql(d90, dh)
assert (n30, n90) == (5, 37), (n30, n90)
# Seit fix/teilwochen (13.09.2026) laufen Spitze und Ø nur über volle Wochen: Montag im Fenster und nicht die laufende Woche.
# drei Fenster im HTML, je Grafik, Legende und Kopfzeile; Zeitraum und Summe je Fenster
# Seit 30.09.2026 fünf Fenster: 7, 30, 60, 90 Tage und seit Beginn.
d7, d60 = date(2026, 9, 10), date(2026, 7, 19)
n7, n60 = summe_sql(d7, dh), summe_sql(d60, dh)
assert (n7, n60) == (4, 6), (n7, n60)
assert rb.count('<div class="fenster" data-fenster=') == 5 and rb.count('<span class="fenster" data-fenster=') == 5 and rb.count('class="chart gross"') == 5, rb[:300]
assert rb_kopf(rb) == {"7": f"7 Tage, 10.–16.09.2026, Summe {n7}", "30": f"30 Tage, 18.08.\u202f–\u202f16.09.2026, Summe {n30}",
                       "60": f"60 Tage, 19.07.\u202f–\u202f16.09.2026, Summe {n60}", "90": f"90 Tage, 19.06.\u202f–\u202f16.09.2026, Summe {n90}",
                       "beginn": f"seit Beginn, 21.04.\u202f–\u202f16.09.2026, Summe {len(roh_r)}"}, rb_kopf(rb)
# Vorauswahl 30 Tage: Grafik, Kopfzeile und Reiter; die zwei anderen hidden, im Fluss
assert sichtbar(s_r) == ["30"] and '<span class="fenster" data-fenster="30">' in rb and rb.count(" hidden>") == 8, sichtbar(s_r)
assert re.findall(r'data-fenster="(\w+)" aria-pressed="true">([^<]*)<', rb) == [("30", "30 Tage")] and "seit Beginn</button>" in rb and "Jahr" not in rb
# 30 Tage je Tag (30 Punkte), 90 Tage und seit Beginn je Kalenderwoche; Werte, Zeigerzeile, Durchschnitt und Spitze aus den Rohzeilen
st30 = stellen_von(rb_teil(rb, "30"))
# Die letzte Stelle läuft noch (30.09.2026) und sagt es in der Zeigerzeile.
assert [f[1] for f in st30] == [f"{d30 + timedelta(days=i):%d.%m.%Y}" for i in range(29)] + ["16.09.2026 · läuft noch"] and [int(f[2]) for f in st30] == [sum(1 for d in zh_tage if d == d30 + timedelta(days=i)) for i in range(30)], st30
assert f"Commits je Tag, Summe über alle Repos" in rb_teil(rb, "30") and f"Ø {n30 / 30:.1f}" in rb_teil(rb, "30") and "ab dem ersten Tag" not in rb_teil(rb, "30") and ">3 am 14.09.</span>" in rb_teil(rb, "30")
# Seit dem 20.09.2026 ohne Kalenderwochen-Bezeichner: Zeigerstellen, Achsen und Spitze nennen nur noch Daten.
for f, von, montag, name in (("90", d90, date(2026, 6, 15), "19.–21.06."), ("beginn", d0, date(2026, 4, 20), "21.–26.04.")):
    wochen_n = (dh - montag).days // 7 + 1
    erwartet_w = [sum(1 for d in zh_tage if von <= d <= dh and montag + timedelta(weeks=w) <= d < montag + timedelta(weeks=w + 1)) for w in range(wochen_n)]
    st = stellen_von(rb_teil(rb, f))
    assert [int(x[2]) for x in st] == erwartet_w and sum(erwartet_w) == summe_sql(von, dh), (f, [x[2] for x in st], erwartet_w)
    assert st[0][1] == name and st[-1][1] == "14.–16.09. · läuft noch" and len(st) == wochen_n, (f, st[0][1], st[-1][1], len(st))
    teil = rb_teil(rb, f)
    voll_w = [erwartet_w[w] for w in range(wochen_n) if montag + timedelta(weeks=w) >= von and w != wochen_n - 1]
    assert "Commits je Woche, Summe über alle Repos" in teil and f"Ø {sum(voll_w) / len(voll_w):.1f} je Woche ohne angebrochene" in teil and 'title="Wochen ab Montag, Zone ' in teil, (f, teil[-300:])
    assert ">30 ab 06.07.</span>" in teil and "KW" not in teil and f'<span style="left:0%">{von:%d.%m.%Y}</span>' in teil and '<span style="left:100%">16.09.2026</span>' in teil, f
# Balken statt Linie (27.09.2026) in allen drei Reitern gegen die Rohwerte; Achse aus der Beschriftung oben. 30 Tage: der laufende Tag in
# Akzentfarbe; 90 Tage und seit Beginn: die laufende Woche und eine vom Fensterrand geschnittene erste als Umriss.
oben_von = lambda g: int(re.search(r'<span style="top:0.0%">(\d+)</span>', g).group(1))
pruefe_grafikbalken(rb_teil(rb, "30"), [int(f[2]) for f in st30], oben_von(rb_teil(rb, "30")), 208, laufend=True, laeuft=True)
for f, von, montag in (("90", d90, date(2026, 6, 15)), ("beginn", d0, date(2026, 4, 20))):
    w_ = [int(x[2]) for x in stellen_von(rb_teil(rb, f))]
    pruefe_grafikbalken(rb_teil(rb, f), w_, oben_von(rb_teil(rb, f)), 208, angebrochen=({0} if montag < von else set()) | {len(w_) - 1}, laeuft=True)
assert rb.count('class="sw balken"') == 5 and "<polygon" not in rb[:rb.index("Serie und Rekorde")], "Legende mit Balkenzeichen, keine Fläche"
assert web.TAGESPUNKTE_MAX == 30, "Grenze aus der Messung vom 13.09.2026: 9.3 px je Tag bei 430 px"
# Serie und Rekorde bleiben über alle Daten und stehen einmal, ausserhalb der Fenster; seit dem 20.09.2026 mit
# eigener Titelstufe auf der Höhe der übrigen Blocktitel (h3.titel, Gewicht und Grösse wie Rückblick und Projekte),
# nicht mehr als gedämpfte Zeile. Die beste Woche steht als Datumsspanne, nicht als 2026-W28.
# Seit 27.09.2026 (letzte Korrektur) steht der Bezugszeitraum als gedämpfte Klammer im Titel, die Zeile unter den Werten entfällt.
assert rb.count('<h3 class="titel">Serie und Rekorde <span class="lbl">(seit 21.04.2026)</span></h3>') == 1 and "über alle Daten" not in rb, rb[rb.index("Serie und Rekorde") - 80:][:600]
assert rb[rb.index("Serie und Rekorde"):].count('class="sub') == 0 and "</ul></div></div>" in rb[rb.index("Serie und Rekorde"):], "keine Zeile mehr unter den Werten"
# Englisch mit dem Datum aus app/format.py (en-US ohne Accept-Language), dieselbe Funktion wie jede andere Datumsangabe der Seite.
s_en = web.seite(jetzt=jr, sprache="en")
assert re.search(r'<h3 class="titel">Streaks and records <span class="lbl">\(since [^<]*2026\)</span></h3>', s_en) and "all data since" not in s_en, s_en[s_en.index("Streaks") - 40:][:200]
assert rb.index("Serie und Rekorde") > rb.rindex('<div class="fenster"') and "Beste Woche</span>06.–12.07.2026 mit 30" in rb, rb[rb.index("Beste Woche") - 20:][:120]
assert "W28" not in rb and "KW" not in rb, "kein Kalenderwochen-Bezeichner im Rückblick"
# Reiterwahl über die Adresse, ein unbekannter Wert fällt auf 30
def seite_von(pfad):
    h_ = web.Handler.__new__(web.Handler); h_.wfile = io.BytesIO(); h_.kopf_nur = False; h_.headers = {}
    h_.send_response = lambda code: setattr(h_, "code", code); h_.send_header = lambda *a: None; h_.end_headers = lambda: None
    h_.bedienen(urlparse(pfad))
    return h_.wfile.getvalue().decode()
for pfad, rbw in (("/?rueckblick=90", "90"), ("/?rueckblick=beginn", "beginn"), ("/?rueckblick=jahr", "30"), ("/", "30")):
    s_a = seite_von(pfad)
    assert sichtbar(s_a) == [rbw] and 'class="card fremde"' not in s_a, (pfad, sichtbar(s_a))
# Favicon (27.09.2026) über den echten Handler: /favicon.ico mit 200 und image/x-icon, ohne immutable (die Adresse trägt keine Prüfsumme);
# icon.svg und apple-touch-icon.png über den /s/<hash>-Weg mit ihrem Medientyp. Die drei link-Tags stehen im Kopf, der Platzhalter data:, ist weg.
def antwort_von(pfad):
    h_ = web.Handler.__new__(web.Handler); h_.wfile = io.BytesIO(); h_.kopf_nur = False; h_.headers = {}; h_.kopf = {}
    h_.send_response = lambda code: setattr(h_, "code", code); h_.send_header = lambda k, v: h_.kopf.__setitem__(k, v); h_.end_headers = lambda: None
    h_.bedienen(urlparse(pfad))
    return h_.code, h_.kopf, h_.wfile.getvalue()
code_i, kopf_i, ico = antwort_von("/favicon.ico")
assert code_i == 200 and kopf_i["Content-Type"] == "image/x-icon" and "immutable" not in kopf_i["Cache-Control"], (code_i, kopf_i)
assert ico[:4] == b"\x00\x00\x01\x00" and int.from_bytes(ico[4:6], "little") == 2 and sorted((ico[6 + 16 * i], ico[7 + 16 * i]) for i in range(2)) == [(16, 16), (32, 32)], ico[:40]
for n, typ in (("icon.svg", "image/svg+xml"), ("apple-touch-icon.png", "image/png")):
    code_s, kopf_s, _ = antwort_von(web.STATIK[n])
    assert code_s == 200 and kopf_s["Content-Type"] == typ and "immutable" in kopf_s["Cache-Control"], (n, code_s, kopf_s)
png180 = (web.WEB / "apple-touch-icon.png").read_bytes()
assert png180[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(png180[16:20], "big") == int.from_bytes(png180[20:24], "big") == 180, png180[:24]
kopf_html = s_r[:s_r.index("</head>")]
assert (f'<link rel="icon" href="/favicon.ico" sizes="32x32">' in kopf_html and f'<link rel="icon" href="{web.STATIK["icon.svg"]}" type="image/svg+xml">' in kopf_html
        and f'<link rel="apple-touch-icon" href="{web.STATIK["apple-touch-icon.png"]}">' in kopf_html and "data:," not in kopf_html), kopf_html[-500:]
# Das SVG trägt Farben nur in den zwei Modusblöcken: ausserhalb von @media (prefers-color-scheme: light|dark) keine Farbangabe, kein
# fill- oder stroke-Attribut; beide Blöcke setzen Fläche, Rand und Buchstabe, die Werte stammen aus den Tokens (--card, --border, --eis).
svg = (web.WEB / "icon.svg").read_text()
modi = dict(re.findall(r"@media \(prefers-color-scheme: (light|dark)\) \{(.*?\})\s*\}", svg))
ausserhalb = re.sub(r"@media \(prefers-color-scheme: (?:light|dark)\) \{.*?\}\s*\}", "", svg)
assert set(modi) == {"light", "dark"} and not re.search(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|\b(?:fill|stroke)\s*[=:]", ausserhalb), ausserhalb
tok = (web.WEB / "tokens.css").read_text()
paar = lambda name: re.search(rf"--{name}: light-dark\(([^,]+), (.+?)\);", tok).groups()
for i, modus in enumerate(("light", "dark")):
    assert all(f"{k}: {paar(t_)[i].strip()}" in modi[modus] for k, t_ in (("fill", "card"), ("stroke", "border"))) and f"fill: {paar('eis')[i].strip()}" in modi[modus], (modus, modi[modus])
assert 'font-weight="700"' in svg and "-apple-system" in svg and ">g</text>" in svg, svg
assert 'data-param="rueckblick" data-vorgabe="30"' in s_r and 'data-param="sterne"' not in s_r and s_r.count("data-param=") == 1
assert "q.set(nav.dataset.param,f)" in s_r and "q.delete(nav.dataset.param)" in s_r and "'/?sterne='" not in s_r and "history.replaceState(null,'',(q.toString()?'/?'+q:'/')+location.hash)" in s_r and "nav.parentNode.querySelectorAll('.fenster')" in s_r
# Leeres Fenster: am 10.01.2027 liegt der letzte Commit 116 Tage zurück. 30 und 90 Tage mit Summe 0, ohne Spitze und ohne Durchschnitt;
# seit Beginn trägt weiter alles. Ohne jeden Commit steht in allen drei Kopfzeilen «noch keine Daten».
jl = datetime(2027, 1, 10, 12, 0, tzinfo=web.TZ)
s_l = web.seite(jetzt=jl, rueckblick="90")
rl = s_l[s_l.index('<section class="card rueckblick">'):s_l.index('</section>', s_l.index('<section class="card rueckblick">'))]
assert summe_sql(date(2026, 12, 12), jl.date()) == 0 and summe_sql(date(2026, 10, 13), jl.date()) == 0
# Seit 02.10.2026 mit Jahr (CLDR-Intervall im Skelett mit Jahr): über den Jahreswechsel tragen beide Enden ihr Jahr, sonst nur das Ende.
assert rb_kopf(rl) == {"7": "7 Tage, 04.–10.01.2027, Summe 0", "30": "30 Tage, 12.12.2026\u202f–\u202f10.01.2027, Summe 0",
                       "60": "60 Tage, 12.11.2026\u202f–\u202f10.01.2027, Summe 0", "90": "90 Tage, 13.10.2026\u202f–\u202f10.01.2027, Summe 0",
                       "beginn": f"seit Beginn, 21.04.2026\u202f–\u202f10.01.2027, Summe {len(roh_r)}"}, rb_kopf(rl)
s_le = web.seite(jetzt=jl, rueckblick="90", sprache="en", accept="en-US")
rle = s_le[s_le.index('<section class="card rueckblick">'):s_le.index('</section>', s_le.index('<section class="card rueckblick">'))]
assert rb_kopf(rle) == {"7": "7 days, Jan\u00a04\u202f–\u202f10,\u00a02027, total 0", "30": "30 days, Dec\u00a012,\u00a02026\u202f–\u202fJan\u00a010,\u00a02027, total 0",
                        "60": "60 days, Nov\u00a012,\u00a02026\u202f–\u202fJan\u00a010,\u00a02027, total 0", "90": "90 days, Oct\u00a013,\u00a02026\u202f–\u202fJan\u00a010,\u00a02027, total 0",
                        "beginn": f"all time, Apr\u00a021,\u00a02026\u202f–\u202fJan\u00a010,\u00a02027, total {len(roh_r)}"}, rb_kopf(rle)
assert '<span style="left:100%">Jan\u00a010,\u00a02027</span>' in rle and "Best week</span>Jul\u00a06\u202f–\u202f12,\u00a02026 with 30" in rle, rle[rle.index("Best week") - 20:][:120]
for f in ("7", "30", "60", "90"):
    assert "pmarke" not in rb_teil(rl, f) and "Ø" not in rb_teil(rl, f) and 'class="hz"' not in rb_teil(rl, f) and "Achse bis 1" in rb_teil(rl, f), f
assert "pmarke" in rb_teil(rl, "beginn") and "Ø" in rb_teil(rl, "beginn") and sichtbar(s_l) == ["90"]
db.DB = Path(tempfile.mkdtemp()) / "rueckblick-einer.db"
c = db.verbinden(schreibend=True)
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES ('e1', 'fernwerk-studio/neu', '2026-09-16T08:00:00Z', 'dev@fernwerk-studio.example')")
c.commit(); c.close()
re1 = web.seite(jetzt=jr)
assert rb_kopf(re1) == {**{f: "1 Tag, 16.09.2026, Summe 1" for f in ("7", "30", "60", "90")}, "beginn": "seit Beginn, 16.09.2026, Summe 1"}, rb_kopf(re1)
assert re1.count('<rect class="b jetzt laeuft" x="150.0" y="0.00" width="700.0" height="260.00"><title>läuft noch</title></rect>') == 5 and "Ø" not in re1[re1.index('<section class="card rueckblick">'):re1.index("Serie und Rekorde")], "ein Tag: ein Balken in Akzentfarbe, kein Durchschnitt"
db.DB = Path(tempfile.mkdtemp()) / "rueckblick-leer.db"
db.verbinden(schreibend=True).close()
assert set(rb_kopf(web.seite(jetzt=jr)).values()) == {"noch keine Daten"} and len(rb_kopf(web.seite(jetzt=jr))) == 5

# Angebrochene Wochen im Rückblick (fix/teilwochen, 13.09.2026): heute ist Montag 14.09.2026 10:00, die laufende Woche hat einen Tag
# und darin 60 Commits; die Daten beginnen am Mittwoch 03.06.2026 mit 50 Commits; die vollen Wochen dazwischen tragen je 3 bis 7 am
# Donnerstag. Beide Teilwochen sind grösser als jede volle Woche: sie dürfen weder Spitze noch Ø sein und bleiben gezeichnet, ihr
# Segment gestrichelt. Erwartung aus den Rohzeilen in Zürcher Datum; Achse und Punktlage nach der dokumentierten Regel.
db.DB = Path(tempfile.mkdtemp()) / "rueckblick-teilwochen.db"
jt = datetime(2026, 9, 14, 10, 0, tzinfo=web.TZ)
roh_t = [datetime(2026, 6, 3, 10, 0, tzinfo=web.TZ)] * 50 + [datetime(2026, 9, 14, 8, 0, tzinfo=web.TZ)] * 60
k_ = 0
while date(2026, 6, 11) + timedelta(weeks=k_) < date(2026, 9, 14):
    roh_t += [datetime.combine(date(2026, 6, 11) + timedelta(weeks=k_), time(10), web.TZ)] * (k_ % 5 + 3)
    k_ += 1
c = db.verbinden(schreibend=True)
for i, t in enumerate(roh_t):
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse) VALUES (?, 'fernwerk-studio/neu', ?, 'dev@fernwerk-studio.example')", (f"t{i}", t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
c.commit(); c.close()
s_t = web.seite(jetzt=jt)
rt = s_t[s_t.index('<section class="card rueckblick">'):s_t.index('</section>', s_t.index('<section class="card rueckblick">'))]
tage_t = [t.date() for t in roh_t]
for f, von in (("90", date(2026, 6, 17)), ("beginn", date(2026, 6, 3))):
    montag = von - timedelta(days=von.weekday())
    n_w = (jt.date() - montag).days // 7 + 1
    w_roh = [sum(1 for d in tage_t if von <= d <= jt.date() and montag + timedelta(weeks=w) <= d < montag + timedelta(weeks=w + 1)) for w in range(n_w)]
    voll_i = [w for w in range(n_w) if montag + timedelta(weeks=w) >= von and w != n_w - 1]
    teil = rb_teil(rt, f)
    assert [int(x[2]) for x in stellen_von(teil)] == w_roh and len(voll_i) == n_w - 2, (f, w_roh)
    assert max(w_roh[0], w_roh[-1]) > max(w_roh[w] for w in voll_i), (f, "die Teilwochen müssen die grössten sein, sonst prüft der Test nichts")
    i_sp = max(voll_i, key=lambda w: w_roh[w])
    assert f'>{w_roh[i_sp]} ab {montag + timedelta(weeks=i_sp):%d.%m.}</span>' in teil, (f, re.findall(r'class="pmarke[^>]*>([^<]*)', teil))
    oben_sp = achse_roh(max(w_roh))
    assert re.findall(r'class="pmarke[^"]*" style="([^"]+)"', teil) == [f"left:{(i_sp + .5) / n_w * 100:.1f}%;top:{100 * (260 - 260 * w_roh[i_sp] / oben_sp) / 260:.1f}%"], (f, "Marke sitzt auf der vollen Woche", re.findall(r'class="pmarke[^"]*" style="([^"]+)"', teil))
    assert f"Ø {sum(w_roh[w] for w in voll_i) / len(voll_i):.1f} je Woche ohne angebrochene" in teil, (f, teil[-260:])
    oben_t = achse_roh(max(w_roh))
    # Seit 27.09.2026 Balken: beide Teilwochen als Umriss in Akzentfarbe, die vollen gedämpft, Ablesestellen auf den Balkenmitten.
    mitten_t = pruefe_grafikbalken(teil, w_roh, oben_t, 208, angebrochen={0, n_w - 1}, laeuft=True)
    assert all(abs(m - float(x[0])) < .11 for m, x in zip(mitten_t, stellen_von(teil))), (f, "Zeiger rastet auf die Balkenmitte")
    assert re.findall(r'<line class="hz" x1="0" y1="([\d.]+)"', teil) == [f"{260 - 260 * sum(w_roh[w] for w in voll_i) / len(voll_i) / oben_t:.1f}"], f
    assert [x[1] for x in stellen_von(teil)][0].endswith(ch_spanne(von, montag + timedelta(days=6))) and stellen_von(teil)[-1][1] == "14.09. · läuft noch", f
assert "b teil" not in rb_teil(rt, "30") and 'class="b jetzt laeuft"' in rb_teil(rt, "30") and ">60 am 14.09.</span>" in rb_teil(rt, "30") and "Ø" in rb_teil(rt, "30"), "Tagesauflösung unverändert, der laufende Tag zählt"
# Unter zwei vollen Wochen entfallen Spitze und Linie. Im Rückblick tritt das nicht ein: mit mehr als 30 Tagen liegen mindestens drei
# volle Wochen im Fenster, gezählt für jeden Anfangswochentag und jede Länge bis 120 Tage. Belegt ist der Fall an der Detailkurve.
min_voll = min(sum(1 for w in range(((s0 + timedelta(days=n0 - 1)) - (s0 - timedelta(days=s0.weekday()))).days // 7 + 1)
                   if s0 - timedelta(days=s0.weekday()) + timedelta(weeks=w) >= s0 and (s0 - timedelta(days=s0.weekday()) + timedelta(weeks=w + 1)) <= s0 + timedelta(days=n0 - 1))
               for s0 in (date(2026, 6, 1) + timedelta(days=d) for d in range(7)) for n0 in range(web.TAGESPUNKTE_MAX + 1, 121))
assert min_voll == 3, min_voll
assert web.ohne_angebrochene([4, 9], {1}) == (None, None) and web.ohne_angebrochene([4, 6, 9], {2}) == (1, 5.0)
db.DB = haupt_db
with db.verbinden() as c2:
    heute_n = c2.execute(f"SELECT COUNT(*) FROM commits WHERE zeitstempel_utc >= ? AND zeitstempel_utc < ? AND {web.IN_ADRESSEN}",
                         (web.iso_utc(web.tag0(jetzt_zh.date())), web.obergrenze(jetzt_zh), *web.ADRESSEN)).fetchone()[0]
assert heute_kachel(jetzt_zh)["n"] == heute_n and "heute" in seite()
with db.verbinden() as c2:
    assert c2.execute("SELECT COUNT(*) FROM repos WHERE repo='fernwerk-studio/geloescht'").fetchone()[0] == 1, "Altdaten bleiben in der Datenbank"

# ---- Zeitzone: eine Quelle für Python und SQL ----------------------------------------------------
# app/web.py::systemzone() muss dieselbe Zone liefern, die SQLites 'localtime' benutzt. Laufen die zwei
# auseinander, gruppiert SQL nach einem anderen Tag, als Python als Fenstergrenze setzt, und beides sieht
# einzeln richtig aus. Belegt an Zeitpunkten dies- und jenseits der lokalen Mitternacht.
mitternacht_utc = lambda d: datetime.combine(d, time(0), web.TZ).astimezone(timezone.utc)
with db.verbinden() as c2:
    for tag_ in (date(2026, 1, 4), date(2026, 3, 29), date(2026, 7, 1), date(2026, 10, 25)):
        for versatz in (timedelta(seconds=-1), timedelta(0), timedelta(hours=12)):
            zeitpunkt = mitternacht_utc(tag_) + versatz
            iso_ = zeitpunkt.strftime("%Y-%m-%dT%H:%M:%SZ")
            aus_sql = c2.execute("SELECT date(?, 'localtime'), strftime('%G-W%V', ?, 'localtime')", (iso_, iso_)).fetchone()
            aus_py = zeitpunkt.astimezone(web.TZ)
            assert aus_sql[0] == aus_py.date().isoformat(), (iso_, aus_sql[0], aus_py.date())
            assert aus_sql[1] == aus_py.strftime("%G-W%V"), (iso_, aus_sql[1], aus_py.strftime("%G-W%V"))
# Codex-Befund 20.09.2026: TZ kann Formen tragen, die libc und damit SQLite versteht, ZoneInfo aber nicht.
# Vorher fiel systemzone() dann auf einen FESTEN Offset zurück, und Python lag im Winter eine Stunde neben
# SQL (gemessen: 15.01. SQL 13:00, Python 14:00). Jede Form wird jetzt in einem eigenen Prozess gefahren, weil
# systemzone() beim Import läuft und TZ des Prozesses verändern darf.
import subprocess as _sp, sys, time as _zeit  # noqa: E402
PRUEFZONE = r"""
import os, sqlite3, tempfile
os.environ["GITPULS_DB"] = os.path.join(tempfile.mkdtemp(), "z.db")
os.environ.setdefault("GITPULS_ADRESSEN", "dev@fernwerk-studio.example")
from datetime import datetime
from app import web
ab = 0
with sqlite3.connect(":memory:") as c:
    for iso in ("2026-01-15T12:00:00Z", "2026-03-29T00:30:00Z", "2026-07-15T12:00:00Z", "2026-10-25T00:30:00Z"):
        sql = c.execute("SELECT strftime('%Y-%m-%d %H:%M', ?, 'localtime')", (iso,)).fetchone()[0]
        py = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(web.TZ).strftime("%Y-%m-%d %H:%M")
        ab += sql != py
print(f"{getattr(web.TZ, 'key', web.TZ)}|{ab}")
"""
# Ein TZ-Wert ohne Zonendatei (POSIX-Regel, Tippfehler) weicht auf die Zone des Systems aus — nie auf einen
# festen Offset, und nie still: eine Zeile im Log nennt den übergangenen Wert.
_system = web.zonenquelle(tz="")[1]   # die Zone des Systems (/etc/localtime), nicht die, die dieser Test für sich setzt (S28)
for tz_, erwartet in (("Europe/Zurich", "Europe/Zurich"), (":Europe/Zurich", "Europe/Zurich"),
                      ("Pacific/Auckland", "Pacific/Auckland"), ("Asia/Kolkata", "Asia/Kolkata"),
                      ("CET-1CEST,M3.5.0,M10.5.0/3", _system), ("Unsinn/Quatsch", _system), ("", _system)):
    umg = dict(os.environ, TZ=tz_) if tz_ else {k: v for k, v in os.environ.items() if k != "TZ"}
    r = _sp.run([sys.executable, "-c", PRUEFZONE], capture_output=True, text=True,
                cwd=str(web.WEB.parent.parent), env=umg | {"PYTHONPATH": str(web.WEB.parent.parent)})
    zeile = [x for x in r.stdout.splitlines() if "|" in x]
    assert zeile, (tz_, r.stdout, r.stderr[-400:])
    zone_, ab_ = zeile[-1].split("|")
    assert ab_ == "0", (tz_, "Python und SQL laufen auseinander", zone_, ab_)
    if erwartet:
        assert zone_ == erwartet, (tz_, zone_, erwartet)
    if tz_ in ("CET-1CEST,M3.5.0,M10.5.0/3", "Unsinn/Quatsch"):
        assert "gibt es keine Zonendatei" in r.stdout and tz_ in r.stdout, (tz_, "der übergangene Wert steht im Log")
# Die drei Quellen der Zone, jede einzeln: TZ (mit und ohne führenden Doppelpunkt), der Zonenname hinter
# /etc/localtime, der Inhalt von /etc/timezone. Der Dateizweig lag lange brach, weil /etc/timezone auf diesem
# System fehlt; er trägt aber überall dort, wo /etc/localtime eine Kopie statt eines Symlinks ist.
ordner_tz = Path(tempfile.mkdtemp())
(ordner_tz / "tz").write_text("Pacific/Auckland\n")
# zonenquelle liefert (Pfad, Name); der Pfad ist die Datei, aus der BEIDE Seiten lesen.
assert web.zonenquelle(tz=":Asia/Kolkata")[1] == "Asia/Kolkata", "führender Doppelpunkt wird abgestreift"
assert web.zonenquelle(tz="  Asia/Kolkata  ")[1] == "Asia/Kolkata"
assert os.path.isfile(web.zonenquelle(tz="Asia/Kolkata")[0]), "der Name wird zu einer Datei aufgelöst"
assert web.zonenquelle(tz="", namensdatei="/nichts")[1] == web.zonenquelle(tz="")[1], "Symlink-Zweig"
assert web.zonenquelle(tz="", localtime="/nichts", namensdatei=str(ordner_tz / "tz"))[1] == "Pacific/Auckland", "Dateizweig"
assert web.zonenquelle(tz="", localtime="/nichts", namensdatei="/nichts") == (None, None)
# Zeigt der Namenszweig auf eine Zonendatei statt auf Text, ist der Fehler ein UnicodeDecodeError und KEIN
# OSError; ohne den Fang stirbt der Start (gemessen 20.09.2026, Byte 0x80 an Stelle 44).
assert web.zonenquelle(tz="", localtime="/nichts", namensdatei="/etc/localtime") == (None, None), "Binärdatei bricht nicht ab"
# Ein absoluter Pfad in TZ wird direkt genommen, samt Name aus dem Pfad.
_p_kol = web.zonenquelle(tz="Asia/Kolkata")[0]
assert web.zonenquelle(tz=":" + _p_kol) == (_p_kol, "Asia/Kolkata"), web.zonenquelle(tz=":" + _p_kol)
# Codex-Befund 20.09.2026, zweiter Lauf: ZWEI STICHPROBEN PRÜFEN OFFSETS, KEINE ZONEN. Europe/Helsinki und
# Asia/Jerusalem haben im Januar und im Juli denselben Offset und trotzdem verschiedene Umstellungstermine; die
# alte Prüfung liess das Paar durch. Seither gibt es keine zwei Wege mehr — Python liest die Zonendatei, auf die
# libc per TZ=:<Pfad> gezwungen wird — und der Nachweis läuft über jeden Umstellungstermin BEIDER Seiten im
# ausgewerteten Zeitraum, je eine Sekunde davor und danach.
import sqlite3 as _sq  # noqa: E402
from zoneinfo import ZoneInfo as _ZI2  # noqa: E402
assert os.environ["TZ"].startswith(":/") and os.path.isfile(os.environ["TZ"][1:]), (
    "libc wird auf genau die Datei gezwungen, die Python gelesen hat", os.environ["TZ"])
_speicher = _sq.connect(":memory:")
assert web.zonendeckung(_speicher, web.TZ, date(2026, 1, 1), date(2026, 12, 31)) is None, "die geltende Zone deckt"
# Das reproduzierte Paar: SQLite auf Helsinki, Python auf Jerusalem. Muss eine Abweichung mit Zone, Zeitpunkt
# und beiden Werten melden — und zwar am 27.03.2026, dem Tag um 2026-03-27T21:30:00Z aus dem Befund.
_tz_vorher = os.environ["TZ"]
os.environ["TZ"] = "Europe/Helsinki"
_zeit.tzset()
_helsinki = _sq.connect(":memory:")
assert _helsinki.execute("SELECT datetime('2026-03-27T21:30:00Z', 'localtime')").fetchone()[0] == "2026-03-27 23:30:00"
assert datetime.fromisoformat("2026-03-27T21:30:00+00:00").astimezone(_ZI2("Asia/Jerusalem")).strftime(
    "%Y-%m-%d %H:%M:%S") == "2026-03-28 00:30:00", "Befund: dieselbe Sekunde, zwei Tage"
meldung = web.zonendeckung(_helsinki, _ZI2("Asia/Jerusalem"), date(2026, 1, 1), date(2026, 12, 31))
assert meldung and "Asia/Jerusalem" in meldung and "27.03.2026" in meldung, meldung
assert "Python" in meldung and "SQLite" in meldung, ("die Meldung nennt beide Werte", meldung)
# Gegenprobe grün: zwei Zonen mit gleichem Offset UND gleichen Umstellungsterminen laufen nicht auseinander.
assert web.zonendeckung(_helsinki, _ZI2("Europe/Kyiv"), date(2026, 1, 1), date(2026, 12, 31)) is None, "gleiche Regel deckt"
os.environ["TZ"] = _tz_vorher
_zeit.tzset()

# Der schärfere Fall, den nur eine Prüfung BEIDER Seiten und die Sekunde VOR dem Übergang fängt:
# America/Havana und America/New_York haben dieselben Offsets (UTC-5 und UTC-4) und stellen am selben TAG um,
# aber zu verschiedenen Stunden (Havana 00:00 lokal, New York 02:00). An jedem Tagesanfang stimmen beide
# überein; nur in der Stunde dazwischen laufen sie auseinander. Gemessen 2026-03-08T05:30:00Z: Havana 01:30,
# New York 00:30 — derselbe Zeitpunkt, verschiedene Stunde.
os.environ["TZ"] = "America/Havana"
_zeit.tzset()
_havana = _sq.connect(":memory:")
assert _havana.execute("SELECT datetime('2026-03-08T05:30:00Z', 'localtime')").fetchone()[0] == "2026-03-08 01:30:00"
assert datetime.fromisoformat("2026-03-08T05:30:00+00:00").astimezone(_ZI2("America/New_York")).strftime(
    "%H:%M") == "00:30", "dieselbe Sekunde, andere Stunde"
for _iso in ("2026-01-15T12:00:00Z", "2026-07-15T12:00:00Z"):
    assert _havana.execute("SELECT datetime(?, 'localtime')", (_iso,)).fetchone()[0] == \
        datetime.fromisoformat(_iso.replace("Z", "+00:00")).astimezone(_ZI2("America/New_York")).strftime("%Y-%m-%d %H:%M:%S"), \
        (_iso, "Januar und Juli decken sich auch hier")
meldung2 = web.zonendeckung(_havana, _ZI2("America/New_York"), date(2026, 1, 1), date(2026, 12, 31))
assert meldung2 and "America/New_York" in meldung2 and "Umstellungstermin" in meldung2, meldung2
assert "2026-03-08" in meldung2 or "2026-11-01" in meldung2, ("der Übergangstag steht im Text", meldung2)
assert "Python" in meldung2 and "SQLite" in meldung2, meldung2
# Und die alte Prüfung an zwei Stichproben hätte beide Paare durchgelassen — der Grund, warum sie ersetzt ist.
os.environ["TZ"] = "Europe/Helsinki"
_zeit.tzset()
for _iso in ("2026-01-15T12:00:00Z", "2026-07-15T12:00:00Z"):
    _sql_wert = _helsinki.execute("SELECT strftime('%Y-%m-%d %H:%M', ?, 'localtime')", (_iso,)).fetchone()[0]
    _py_wert = datetime.fromisoformat(_iso.replace("Z", "+00:00")).astimezone(_ZI2("Asia/Jerusalem")).strftime("%Y-%m-%d %H:%M")
    assert _sql_wert == _py_wert, (_iso, "Januar und Juli decken sich, genau das war die Falle")
os.environ["TZ"] = _tz_vorher
_zeit.tzset()
# Die Prüfung beim Start ist eine eigene Funktion und wird hier wirklich gefahren, nicht nur im __main__-Block
# vermutet: mit der geltenden Zone deckt sie, mit einer fremden meldet sie.
assert web.startpruefung() is None, "die geltende Zone deckt über den ausgewerteten Zeitraum"
_fremd = web.startpruefung(zone=_ZI2("America/New_York"))
assert _fremd and "America/New_York" in _fremd, _fremd
quelle_start = (web.WEB.parent / "web.py").read_text()
assert "abweichung = startpruefung()" in quelle_start, "der Start ruft sie"
assert 'if abweichung:\n        sys.exit("gitpuls: " + abweichung)' in quelle_start, "und bricht bei einer Abweichung ab"
# Die Bisektion findet den Übergang BEIDER Seiten sekundengenau; darauf ruhen die Prüfpunkte davor und danach.
# Havana stellt am 08.03.2026 um 00:00 lokal um (05:00Z), New York um 02:00 lokal (07:00Z).
_von, _bis = datetime(2026, 3, 7, tzinfo=timezone.utc), datetime(2026, 3, 9, tzinfo=timezone.utc)
os.environ["TZ"] = "America/Havana"
_zeit.tzset()
_g_sql = web._wechsel(lambda d: web.offset_sql(_havana, d), _von, _bis)
_g_py = web._wechsel(lambda d: int(d.astimezone(_ZI2("America/New_York")).utcoffset().total_seconds()), _von, _bis)
assert _g_sql == datetime(2026, 3, 8, 5, tzinfo=timezone.utc), _g_sql
assert _g_py == datetime(2026, 3, 8, 7, tzinfo=timezone.utc), _g_py
assert _g_sql != _g_py, "genau deshalb werden die Übergänge BEIDER Seiten gesucht, nicht nur die einer"
os.environ["TZ"] = _tz_vorher
_zeit.tzset()

# Die Zone ist keine fest verdrahtete mehr# Die Zone ist keine fest verdrahtete mehr, sondern die des Systems, und sie ist eine echte Zone mit
# Sommerzeitregel, kein fester Offset: nur eine echte Zone rechnet an den Wechseltagen richtig.
from zoneinfo import ZoneInfo as _ZI  # noqa: E402
assert isinstance(web.TZ, _ZI), ("feste Offsets rechnen an Wechseltagen falsch", repr(web.TZ))
quelle_web = (web.WEB.parent / "web.py").read_text()
assert 'ZoneInfo("Europe/Zurich")' not in quelle_web, "keine fest verdrahtete Zone im Code"
assert getattr(web.systemzone(), "key", None) == getattr(web.TZ, "key", None), "systemzone liefert reproduzierbar dieselbe Zone"
# Auch keine im Klartext: die Legende nannte «Zürcher Datum», was in einer fremden Fassung einen anderen Tag behauptet.
assert "Zürcher Datum" not in seite() and web.zonenname() == getattr(web.TZ, "key", None), web.zonenname()
# Seit 25.09.2026 steht die Zone im title der Legende, die Legende selbst bleibt einzeilig; «ab dem ersten Tag mit Daten» entfällt.
assert f'<p class="sub rlegende1" title="Zone {web.zonenname()}">' in seite() and "ab dem ersten Tag mit Daten" not in seite(), "die Legende nennt die geltende Zone im title"
# «Serie und Rekorde» oben ausgerichtet; der Bezugszeitraum steht seit 27.09.2026 gedämpft in normaler Schriftstärke im Titel.
assert "align-items: start" in " ".join(regeln[".rraster"]) and "white-space: nowrap" in " ".join(regeln[".rlegende1"]), "Serie und Rekorde oben, Legende einzeilig"
assert web.BALKEN_PX_H == {"gross": 13 * 16, "klein": 6 * 16, "mini": 6 * 16} and "height: 13rem" in " ".join(regeln[".chart.gross .plot svg"]) and "height: 6rem" in " ".join(regeln[".chart.mini .plot svg"]) and "height: 6rem" in " ".join(regeln[".chart.klein .plot svg"]), "Balkenhöhe folgt der CSS"
assert "fill: var(--lbl)" in " ".join(regeln[".plot .b"]) and "fill: none" in " ".join(regeln[".plot .b.teil:not(.leer)"]) and "var(--eis)" in " ".join(regeln[".plot .b.jetzt"]), "Farben der Balken über CSS"
# Die Klammer trägt .lbl (keine eigene Typografie), nie .fenster: das Reiterskript blendet jedes
# .fenster der Karte ohne passendes data-fenster aus. Im Titel ohne den Abstand aus .rraster .lbl.
_rek = seite()[seite().index('<div class="rraster">'):]
_klammer = re.search(r'<h3 class="titel">Serie und Rekorde <span class="([^"]+)">\(seit ', _rek).group(1)
assert _klammer == "lbl" and not any("rseit" in sel or "rrekorde" in sel for sel in regeln) and not re.search(r"\.rseit\b", css), (_klammer, "keine eigene Regel für die Klammer")
assert " ".join(regeln[".rraster .titel .lbl"]).strip() == "margin-bottom: 0;", regeln.get(".rraster .titel .lbl")
assert "justify-content: flex-start" in " ".join(regeln[".rraster .titel"]), "Klammer direkt hinter dem Titel, nicht am rechten Rand"
# Detailkurve (25.09.2026): 6rem hoch, das 4/3-Fache der 4.5rem davor; der Punkt der laufenden Woche steht in .zf, das nur das SVG
# umschliesst, nicht in .plot mit x-Beschriftung und padding-right. Seine Prozente treffen so das Ende der gestrichelten Linie.
assert "height: 6rem" in " ".join(regeln[".chart.mini .plot svg"]) and "height: 6rem" in " ".join(regeln[".chart.mini .ylab"]), regeln[".chart.mini .plot svg"]
assert "position: relative" in " ".join(regeln[".zf"]) and "position" not in " ".join(regeln.get(".pdetail .plot", [])), regeln[".zf"]
# Detailgrafik seit 27.09.2026: sieben Tagesplätze. Montag: ein Balken (heute, Akzent) auf dem ersten Platz, keine Wertmarke, sechs leere Plätze;
# Sonntag: sieben Balken, der letzte heute. Die Wertmarke einer einzigen Periode bleibt eine Eigenschaft von kurve() und steht in .zf.
_k = web.karte_kurve([4, 0, 0, 0, 0, 0, 0], date(2026, 9, 14))
assert "pjetzt" not in _k and "pmarke" not in _k and _k.count("<rect") == 1 and _k.count('<rect class="b jetzt"') == 1 and _k.count('class="platz"') == 7, _k
_k = web.karte_kurve([1, 0, 2, 0, 0, 3, 1], date(2026, 9, 20))
assert _k.count("<rect") == 7 and _k.count('<rect class="b jetzt"') == 1 and _k.count('<rect class="b leer"') == 3 and len(stellen_von(_k)) == 7, _k
# 30 und 90 Tage im Detail (03.10.2026): 30 Tagesbalken, der letzte heute in Akzentfarbe, Zeigerzeile mit Wochentag und Datum; 90 Tage als
# Kalenderwochen ab Montag wie im Rückblick, die vom Fensterrand geschnittene erste und die laufende als Umriss, Summen nur über die Tage im Fenster.
_t30 = [0] * 29 + [2]
_t30[0] = 1
_k = web.tage_kurve(_t30, date(2026, 10, 3))
assert _k.count("<rect") == 30 and _k.count('<rect class="b jetzt"') == 1 and len(stellen_von(_k)) == 30 and "Commits je Tag · Achse bis 2" in _k, _k
assert stellen_von(_k)[0][1:3] == ["Fr.,\u00a04.9.", "1"] and stellen_von(_k)[-1][1:3] == ["Sa.,\u00a03.10.", "2"], stellen_von(_k)
_t90 = [1] * 90   # 06.07.2026 (Mo) bis 03.10.2026 (Sa): zwölf volle Wochen, die laufende Mo bis Sa als Umriss
_k = web.tage_kurve(_t90, date(2026, 10, 3))
assert [f[2] for f in stellen_von(_k)] == ["7"] * 12 + ["6"] and _k.count('class="b teil') == 1 and "Commits je Woche" in _k, stellen_von(_k)
assert stellen_von(_k)[0][1] == "06.–12.07." and stellen_von(_k)[-1][1].endswith("03.10. · läuft noch"), stellen_von(_k)
_k = web.tage_kurve(_t90, date(2026, 10, 4))   # 07.07. (Di) bis 04.10. (So): die erste Woche vom Rand geschnitten, sechs Tage
assert [f[2] for f in stellen_von(_k)] == ["6"] + ["7"] * 12 and _k.count('class="b teil') == 2 and stellen_von(_k)[0][1] == "07.–12.07.", stellen_von(_k)
_k = web.kurve([4], "", "", klasse="mini", laufend=True, balken=True)
_zf = _k[_k.index('<div class="zf">'):_k.index('<div class="xlab">')]
assert _zf.index("</svg>") < _zf.index('class="pmarke"') < _zf.index("</div>", _zf.index("</svg>")), ("die Marke steht in .zf", _zf[-400:])
# Codex-Befund 20.09.2026: kein Autor-Default für die Filteradressen. Eine fremde Installation ohne gesetzte
# Variable sähe sonst eine leere Seite mit null Commits — nicht zu unterscheiden von einer jungen Datenlage.
quelle_web = (web.WEB.parent / "web.py").read_text()
# Keine Mailadresse im Code der Seite ausser unter .example (seit 10.10.2026 allgemein statt mit den Adressen des Autors im Test).
assert not [a for a in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", quelle_web) if not a.endswith(".example")], "keine Adresse im Code"
assert 'os.environ.get("GITPULS_ADRESSEN", "")' in quelle_web and "GITPULS_ADRESSEN ist nicht gesetzt" in quelle_web
assert web.ADRESSEN == os.environ["GITPULS_ADRESSEN"].split(","), web.ADRESSEN
# Und der Start bricht ohne Adressen laut ab, statt eine leere Seite zu zeigen. Als echter Unterprozess gefahren,
# weil der Abbruch im __main__-Block steht und kein Test ihn sonst je ausführt.
# Mit timeout: bricht der Start NICHT ab, geht er in serve_forever und liefe ohne Zeitgrenze für immer. Genau
# das ist der Fehlerfall, und ein Test, der dabei hängt statt rot zu werden, meldet nichts (gemessen 20.09.2026:
# die Gegenprobe lief mit diesem eingebauten Fehler zwanzig Minuten, statt ihn in Sekunden zu zeigen).
try:
    r_leer = _sp.run([sys.executable, "-m", "app.web"], capture_output=True, text=True, timeout=25,
                     cwd=str(web.WEB.parent.parent),
                     env={k: v for k, v in os.environ.items() if k != "GITPULS_ADRESSEN"}
                         | {"PYTHONPATH": str(web.WEB.parent.parent), "GITPULS_ADRESSEN": "", "GITPULS_PORT": "0"})
except _sp.TimeoutExpired:
    raise AssertionError("ohne Adressen startete der Dienst, statt laut abzubrechen")
assert r_leer.returncode != 0 and "GITPULS_ADRESSEN ist nicht gesetzt" in r_leer.stderr, (r_leer.returncode, r_leer.stderr[-300:])
assert "git config user.email" in r_leer.stderr, "die Meldung sagt, was zu setzen ist"
# Zone und Adressen gehören dem Betreiber: das Bild schreibt keine Zone vor, compose verlangt beide.
wurzel = web.WEB.parent.parent
assert "TZ=" not in (wurzel / "Dockerfile").read_text(), "keine Zone im Bild"
compose = (wurzel / "compose.example.yaml").read_text()
assert "TZ: ${TZ:?" in compose and "GITPULS_ADRESSEN: ${GITPULS_ADRESSEN:?" in compose, "beide als Pflichtangabe"
# Europe/Zurich darf als BEISPIEL in der Fehlermeldung stehen, nie als Wert oder Vorgabe.
assert "TZ: Europe/Zurich" not in compose and "TZ=Europe/Zurich" not in (wurzel / "Dockerfile").read_text()
assert ":-Europe/Zurich" not in compose, "kein Zürich als Default hinter :-"
assert (mitternacht_utc(date(2026, 3, 29)) - mitternacht_utc(date(2026, 3, 28))) == timedelta(hours=24)
assert (mitternacht_utc(date(2026, 3, 30)) - mitternacht_utc(date(2026, 3, 29))) == timedelta(hours=23), "der 29.03. hat 23 Stunden"
assert (mitternacht_utc(date(2026, 10, 26)) - mitternacht_utc(date(2026, 10, 25))) == timedelta(hours=25), "der 25.10. hat 25 Stunden"

# ---- Farblogik der Grafiken (20.09.2026) ---------------------------------------------------------
# Ausnahmslos: die Reihe des laufenden Zeitraums trägt die Akzentfarbe als volle Linie, jede Vergleichs- und
# Bezugsreihe ist gestrichelt in der Beschriftungsfarbe. Vorher war es umgekehrt und kehrte die Bedeutung um.
assert "stroke: var(--eis)" in " ".join(regeln[".plot .l"]) and "stroke-dasharray" not in " ".join(regeln[".plot .l"])
assert "stroke: var(--lbl)" in " ".join(regeln[".plot .hz"]) and "stroke-dasharray" in " ".join(regeln[".plot .hz"])
assert "stroke: var(--lbl)" in " ".join(regeln[".plot .l.teil"]), "die angebrochene Periode bleibt gedämpft gestrichelt"
# Die Legende nennt beide Reihen mit dem Fleck ihrer Farbe, die Zeigermarken tragen die Farbe ihrer Reihe.
assert "background: var(--eis)" in " ".join(regeln["i.sw"]) and "dashed var(--lbl)" in " ".join(regeln["i.sw.fein"])
assert "color: var(--eis)" in " ".join(regeln[".zmarke.voll"]) and "color: var(--lbl)" in " ".join(regeln[".zmarke.vgl"])
# Die Fläche unter der laufenden Linie bleibt gedämpft, kein blauer Verlauf; die Mikrokurve bleibt gedämpft,
# weil der Wert neben ihr in Textfarbe steht und nicht im Akzent.
assert "fill: var(--flaeche)" in " ".join(regeln[".plot .flaeche"]), regeln.get(".plot .flaeche")
assert "fill: var(--lbl)" in " ".join(regeln[".mikro rect"]), regeln.get(".mikro rect")
# Keine Regel der Grafik trägt den Akzent für eine Vergleichsreihe oder die Textfarbe für eine laufende.
for sel in (".plot .hz", ".plot .l.teil", "i.sw.fein", ".zmarke.vgl"):
    assert "--eis" not in " ".join(regeln[sel]), (sel, "Vergleichsreihe im Akzent")
for sel in (".plot .l", "i.sw", ".zmarke.voll"):
    assert "--txt" not in " ".join(regeln[sel]), (sel, "laufende Reihe in Textfarbe")

# Tag- und Nachtmodus: jede Farbe in tokens.css trägt beide Werte, style.css kein Literal daneben, und das
# Kopfskript steht VOR dem Stylesheet, sonst blitzt «Dunkel auf hellem Gerät» hell auf.
import re
tokens = (web.WEB / "tokens.css").read_text()
farben = re.findall(r"^\s*(--[a-z]+):\s*([^;]+);", tokens, re.M)
assert all(w.startswith("light-dark(") for n, w in farben if re.search(r"#|rgb", w)), [n for n, w in farben if re.search(r"#|rgb", w) and not w.startswith("light-dark(")]
assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", (web.WEB / "style.css").read_text()), "style.css: Farbliteral ausserhalb der Tokens"
assert not re.search(r'(stroke|fill)="(#|rgb)', seite()), "SVG-Präsentationsattribut mit Farbe: löst kein var() auf"
s = seite()
assert s.index("localStorage.getItem('gitpuls-modus')") < s.index("tokens.css") and 'content="light dark"' in s
assert s.count('data-wahl=') == 2 and 'data-wahl="system"' not in s and "'system'" not in s, "zwei Segmente, kein dritter Zustand"
assert "prefers-color-scheme" in s and "removeItem('gitpuls-modus')" in s, "Listener und Rückfall auf das System"

# Die Rangliste fremder Repos ist mit 1.1.0 entfallen, samt Reitern und ?sterne=.
s = seite()
assert 'class="card fremde"' not in s and 'data-param="sterne"' not in s and 'href="/?sterne=' not in s
# Die Fusszeile ist seit 26.09.2026 zurück (Copyright, Stand, Version), die Zeile zu Zeitzone und Wochenbeginn bleibt weg (Entscheid 11.09.2026).
assert s.count("<footer") == 1 and "Zeitzone Europe/Zurich" not in s and "Wochen ab Montag" not in s.split("<footer")[1], "Fusszeile ohne Zonenzeile"
import re as _re
_titel = _re.search(r"<header><h1>(.*?)</h1>", s).group(1)
assert _re.findall(r'<a [^>]*href="([^"]*)"', _titel) == ["/"] and _titel.count("<a ") == 1, _titel   # genau ein Link auf /
# Kalenderwochen-Bezeichner verschwinden aus der ganzen Oberfläche (Entscheid 20.09.2026), die Rechnung bleibt auf
# strftime('%G-W%V'): kein «2026-W38» und kein «KW 38» im HTML, aber die Schlüssel stehen weiter im Code.
s = seite()
assert not re.search(r"\d{4}-W\d{2}", s) and not re.search(r"\bKW\s*\d", s), re.findall(r".{40}(?:\d{4}-W\d{2}|KW\s*\d).{40}", s)[:3]
with db.verbinden() as c2:
    assert web.wochenreihen(c2, jetzt_zh.date(), jetzt_zh)[0][-1] == jetzt_zh.date().strftime("%G-W%V"), "die internen Schlüssel bleiben"
montag_s = jetzt_zh.date() - timedelta(days=jetzt_zh.weekday())
assert KOPF_DE in s and "Diese Woche aktiv" not in s and "Repos:" not in s
for r in gelistet_roh():
    kopf_r = summary_von(s, r)
    assert f'title="Woche ab {montag_s:%d.%m.}, Vorwoche zum selben Zeitpunkt' in kopf_r, (r, kopf_r)
    # Seit 26.09.2026 ein Raster statt Chips, ohne die Datumsspanne der laufenden Woche und ohne «letzter Commit» (beides stand doppelt).
    det_r = zeile_von(s, r)[zeile_von(s, r).index("</summary>"):]
    assert '<dl class="pmeta"><div class="ppaar"><dt>Woche</dt><dd>' in det_r and f"{montag_s:%d.%m.} bis" not in det_r and "letzter Commit" not in det_r \
        and 'class="chip"' not in det_r, (r, "Raster ohne Datumsspanne und ohne letzten Commit")
assert "bleibt bis 09.09.2026 leer" not in s and "Search-API" not in s and "Erstellt im gewählten Fenster" not in s, "Fusstext der Rangliste entfällt"

# ---- Auslastung als Füllbalken in der Zeile (20.09.2026, dritter Schritt) ------------------------
# Der 24-Stunden-Verlauf ist zurückgebaut, samt Ringpuffer, Schreiber und auslastung.json: bei zwei Werten, die
# sich über einen Tag kaum bewegen, beantwortet ein Verlauf die falsche Frage. Arbeitsspeicher und Platte behalten
# ihre Zeilen im Block Maschine und bekommen dahinter einen Füllbalken mit festem Massstab 0 bis 100.
web.SERVER.write_text(json.dumps(beispiel))
m_b, z_b = beispiel["maschine"], beispiel["zustand"]
ram_pz, platte_pz = z_b["ram_belegt_bytes"] * 100 / m_b["ram_bytes"], z_b["platte_belegt_bytes"] * 100 / m_b["platte_bytes"]
h = web.server_html(beispiel, jetzt)
assert 'class="tile auslastung"' not in h and "vzahlen" not in h and 'class="mikro"' not in h, "weder Kachel noch Sparkline"
assert not hasattr(web, "auslastung_daten") and not hasattr(web, "verlauf_balken") and not hasattr(web, "AUSLASTUNG"), "Ringpuffer-Leser ersatzlos weg"
maschine_blk = h[h.index("<b>Maschine</b>"):h.index("<b>Installiert</b>")]
assert re.findall(r'<span class="mk">([^<]*)</span>', maschine_blk) == \
       ["Produkt", "Host", "Last", "Arbeitsspeicher", "Platte", "Kerne", "Gestartet"], maschine_blk
# Breite gegen den Prozentwert: die Erwartung rechnet aus server.json, nicht aus dem, was die Seite ausgibt.
balken = re.findall(r'<span class="mk">(Arbeitsspeicher|Platte)</span><span>([^<]*)<span class="fbalken" aria-hidden="true"><i style="width:([\d.]+)%"></i></span>', maschine_blk)
assert [n for n, _, _ in balken] == ["Arbeitsspeicher", "Platte"], balken
for (name, text, breite), anteil, belegt, gesamt in zip(balken, (ram_pz, platte_pz),
                                                        (z_b["ram_belegt_bytes"], z_b["platte_belegt_bytes"]),
                                                        (m_b["ram_bytes"], m_b["platte_bytes"])):
    assert float(breite) == round(belegt * 100 / gesamt, 1) == round(anteil, 1), (name, breite, anteil)
    assert text == f'{anteil:.0f}%, {web.gb(belegt)} von {web.gb(gesamt)} GB', (name, text)
assert (round(ram_pz, 1), round(platte_pz, 1)) == (32.3, 4.3), (ram_pz, platte_pz)
# Last (10.10.2026, Audit S26, ersetzt die «Auslastung» als Prozent der Kerne vom 25.09.2026): das Load-Mittel über 5 Minuten mit der Kernzahl
# als Bezug, ohne Füllbalken und ohne Erklärung als Belegung der Kerne; die drei Rohwerte im title. Erwartung aus server.json.
last = re.search(r'<div class="mz" title="([^"]*)"><span class="mk">Last</span><span>([^<]*)</span></div>', maschine_blk)
assert last, maschine_blk
roh, kerne = z_b["load"], m_b["vcores"]
assert (roh, kerne) == ([0.07, 0.07, 0.08], 2), (roh, kerne)
assert last.group(2) == "0.07 bei 2 Kernen, Mittel 5 Min.", last.groups()
assert "Load-Mittel 0.07 / 0.07 / 0.08 über 1, 5 und 15 Minuten" in last.group(1) and "Kein Anteil an der Rechenleistung" in last.group(1), last.group(1)
hoch = json.loads(json.dumps(beispiel))
hoch["zustand"]["load"], hoch["maschine"]["vcores"] = [3.1, 3.0, 2.5], 2
mh = web.fuss_maschine(hoch)
assert ">Last</span><span>3.00 bei 2 Kernen, Mittel 5 Min.</span>" in mh and "fbalken" not in mh.split(">Last<")[1].split("</div>")[0], mh
eins = json.loads(json.dumps(beispiel))
eins["zustand"]["load"], eins["maschine"]["vcores"] = [0.5, 0.25, 0.1], 1
assert ">Last</span><span>0.25 bei 1 Kern, Mittel 5 Min.</span>" in web.fuss_maschine(eins)
from app.sprache import gilt  # noqa: E402
with gilt("en"):
    mh_en = web.fuss_maschine(hoch)
assert ">Load</span><span>3.00 on 2 cores, 5-min average</span>" in mh_en, mh_en
assert "Load average 3.10 / 3.00 / 2.50 over 1, 5 and 15 minutes" in mh_en
# Randfälle: 0% ist eine leere Spur, 100% eine volle, darüber wird gekappt statt über die Spur hinauszulaufen.
assert web.fuellbalken(0) == '<span class="fbalken" aria-hidden="true"><i style="width:0.0%"></i></span>', web.fuellbalken(0)
assert web.fuellbalken(100) == '<span class="fbalken" aria-hidden="true"><i style="width:100.0%"></i></span>'
assert web.fuellbalken(150).count('width:100.0%') == 1 and web.fuellbalken(-5).count('width:0.0%') == 1, "gekappt, nie über die Spur hinaus"
leer = json.loads(json.dumps(beispiel))
leer["zustand"]["ram_belegt_bytes"], leer["zustand"]["platte_belegt_bytes"] = 0, leer["maschine"]["platte_bytes"]
hl = web.server_html(leer, jetzt)
assert 'width:0.0%' in hl and 'width:100.0%' in hl and ">Arbeitsspeicher</span><span>0%," in hl and ">Platte</span><span>100%," in hl, hl[hl.index("Arbeitsspeicher"):][:200]
# Spur für beide Zeilen gleich breit, fester Massstab, 4 px hoch mit dem kleinsten Radius der Staffel, einfarbig.
spur = " ".join(regeln[".fbalken"])
fuell = " ".join(regeln[".fbalken i"])
assert "width: 5rem" in spur and "height: 4px" in spur and "border-radius: 2px" in spur, spur
assert "background: var(--track)" in spur and "background: var(--eis)" in fuell, (spur, fuell)
assert min(int(x) for x in re.findall(r"border-radius: (\d+)px", css)) == 2, "2px ist die kleinste Radius-Stufe"
# Seit 25.09.2026 (Entscheid Cédric) tragen alle Füllbalken den Akzent, auch Arbeitsspeicher und Platte, über EINE Regel;
# keine Sonderregel je Ort, nirgends Rot, Mint oder ein Wechsel nach Höhe.
akzent_sel = sorted(sel.strip() for sel, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S)) if "fbalken" in sel and "--eis" in decl for sel in sel.split(","))
assert akzent_sel == [".fbalken i"], akzent_sel
assert not re.search(r"\.fbalken[^{]*\{[^}]*(--glut|--mint|--lbl)", css), "kein Rot, kein Zustandsfarbwechsel, nicht mehr gedämpft"
# Unter 480 px fällt der Balken weg, die Zahlen bleiben.
schmal_480 = css[css.index("@media (max-width: 480px)"):]
assert re.search(r"\.fbalken[^{]*\{\s*display: none", schmal_480), schmal_480[:400]
# Das Aufklappdetail der Dienste trägt seine Werte in voller Textfarbe, nur die Beschriftungsspalte bleibt gedämpft
# (dieselbe Ordnung wie .mz in den Fussblöcken seit f9caaee). Kindkombinator, damit die Warnfarbe im Wert gewinnt.
# Seit 10.10.2026 mit :not(.glut): trägt der Wert selbst die Warnfarbe (fehlender Timer), bleibt sie stehen.
assert "color: var(--txt)" in " ".join(regeln[".dz > span:last-child:not(.glut)"]), regeln.get(".dz > span:last-child:not(.glut)")
assert "color: var(--lbl)" in " ".join(regeln[".dk"]) and ".dz span:last-child" not in regeln, "Nachfahre würde den Glut-Span schlagen"

# ---- Ablese-Zeiger -------------------------------------------------------------------------------
# Jede Grafik der Seite trägt ihn, keine zweimal, und nichts davon als Schrift im gestreckten SVG.
s = seite()
assert s.count('class="zbox"') == s.count('class="chart') == s.count('class="zl"') == s.count('class="zlab"') > 0, (
    s.count('class="zbox"'), s.count('class="chart'))
assert "<text" not in s, "Marken sind HTML neben dem Bild, nie <text> im gestreckten SVG"

# Die Marke kommt fertig aus dem Zeichner, der die Werte ohnehin in der Hand hat; das Skript liest sie
# nur ab. Je Stelle sechs Felder: x, Zeigerzeile, Marke und y der vollen Reihe, dasselbe der Vergleichsreihe.
# In der Reihenfolge des Browsers lesen: erst das Attribut auflösen, dann trennen. Der Apostroph der
# Tausendertrennung steht im Quelltext als &#x27; und trüge sonst ein Semikolon in die Trennung.
stellen_von = lambda g: [t.split("|") for t in web.html.unescape(re.search(r'data-stellen="([^"]+)"', g).group(1)).split(";")]
pfad_von = lambda g, cls: [p.split(",") for p in re.search(r'<path class="%s" d="M([^"]+)"' % cls, g).group(1).split(" L")]

werte = [0, 3, 17, 4, 129, 8]
g = web.kurve(werte, "Tag 1", "Tag 6", stellen=[f"Tag {i + 1}" for i in range(len(werte))])
punkte_l, felder = pfad_von(g, "l"), stellen_von(g)
assert [f[2] for f in felder] == [str(v) for v in werte], felder
assert [(f[0], f[3]) for f in felder] == [(px, py) for px, py in punkte_l], "Stelle und Marke sitzen auf dem gezeichneten Punkt"
assert [f[1] for f in felder] == [f"Tag {i + 1}" for i in range(6)]
assert [f[4] for f in felder] == [""] * 6 and [f[5] for f in felder] == [""] * 6, "ohne Vergleichsreihe bleiben deren Felder leer"
assert "data-oben" not in g, "die Achsenobergrenze wird nicht mehr gebraucht, es wird nichts zurückgerechnet"
assert '<div class="zzeile">' in g and 'class="zl"' in g and 'class="zlab"' in g and "<text" not in g, "Marken sind HTML neben dem Bild"
assert web.kurve(werte, "Tag 1", "Tag 6").count("zbox") == 0, "ohne Stellen bleibt die Grafik, wie sie war"

# Tausendertrennung wie überall auf der Seite, fertig gesetzt: das Skript formatiert nichts.
assert stellen_von(web.kurve([1234], "a", "a", stellen=["x"]))[0][2] == "1’234"

# Ein einziger Wert ist ein kurzer Strich, kein Pfad: seine Stelle liegt in dessen Mitte.
einer_z = web.kurve([5], "08.09.", "08.09.", stellen=["08.09.2026"])
assert 'data-stellen="500.0|08.09.2026|5|43.3||"' in einer_z and '<line class="l" x1="460.0" y1="43.3"' in einer_z, einer_z

# Wo eine Reihe keinen Wert hat, steht keine Marke, auch keine gerechnete: die Stelle 1000.0 trägt nur
# die Vergleichsreihe, ihre zwei Felder für die volle Reihe bleiben leer.
teil = web.kurve([(0, 0), (0.5, 4)], "00:00", "24:00", vergleich=[(0, 0), (0.5, 2), (1.0, 9)], bezug=(3, "Durchschnitt"),
                 stellen=["00:00", "12:00"], stellen_v=["00:00", "12:00", "24:00"])
assert [f[0] for f in stellen_von(teil)] == ["0.0", "500.0", "1000.0"], stellen_von(teil)
assert [(f[2], f[4]) for f in stellen_von(teil)] == [("0", "0"), ("4", "2"), ("", "9")], stellen_von(teil)
assert [x for x, _ in pfad_von(teil, "l")] == ["0.0", "500.0"], "die volle Reihe endet vorher, dort steht keine Marke"
assert [x for x, _ in pfad_von(teil, "hz")] == ["0.0", "500.0", "1000.0"] and '<line class="hz" x1="0"' in teil

# Je Grafik, was sie misst, und in der Kopfzeile Uhrzeit, Datum oder Kalenderwoche.
kw = web.karte_kurve([0, 2, 5, 0, 0, 0, 0], date(2026, 9, 16))
assert [f[1] for f in stellen_von(kw)] == ["Mo.,\u00a014.9.", "Di.,\u00a015.9.", "Mi.,\u00a016.9."], kw
assert re.findall(r'<span class="platz" style="left:[\d.]+%">([^<]+)</span>', kw) == ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."], "x-Achse: Wochentag je Platz"
assert web.wochenmontag("2026-W38") == date(2026, 9, 14) and web.wochenspanne("2026-W38") == "14.–20.09.2026"
# Jahresgrenze: die ISO-Woche 2026-W01 beginnt am 29.12.2025, die Datumsbeschriftung muss über das Jahr hinweg
# stimmen (gemessen im Bild-Python 3.13.15, das den Dienst ausführt).
assert web.wochenmontag("2026-W01") == date(2025, 12, 29) and web.wochenspanne("2026-W01") == "29.12.2025\u202f–\u202f04.01.2026"
assert web.wochenmontag("2026-W53") == date(2026, 12, 28), "2026 hat 53 ISO-Wochen"
# Jede gezeichnete Reihe bekommt auch Marken: zu jedem Punkt gehört ein Eintrag in der Stellenliste.
# Am 12.09.2026 war das auf der 7- und der 30-Tage-Kachel verloren, weil kacheldaten dort stellen_v=None
# übergab; seit die Werte aus den Stellenlisten kommen, blieben die zwei Felder der Vergleichsreihe leer
# und die gestrichelte Linie stand ohne Marke da. Gefunden erst durch eine Messung am laufenden Dienst.
with db.verbinden() as c3:
    k7_, k30_ = web.kacheldaten(c3, jetzt_zh.date(), jetzt_zh)[1:]
    assert len(k7_["stellen"]) == len(k7_["tage"]) == len(k7_["schnitt"]) == 7, "7 Tage: Tageswert und Durchschnitt je Stelle"
    assert len(k30_["stellen"]) == len(web.punkte(k30_["voll"])) and k30_["stellen_v"] and len(k30_["stellen_v"]) == len(web.punkte(k30_["vergleich"]))

assert re.search(r'data-stellen="0\.0\|\d\d\.\d\d\.\d{4}\|', s), "Tage und Rückblick: Datum"

# Maus und Stift, kein Finger: touch läuft nicht durch, kein touch-action, kein Fangen des Zeigers.
assert "pointerType==='touch')return" in s and "setPointerCapture" not in s
assert "touch-action" not in s + (web.WEB / "style.css").read_text()

# Blockisolation, serverseitig: bricht der Zeichner, stehen an den Stellen der Grafikblöcke (zwei Kacheln mit Kurve, die
# Tagesgrafik jedes Repos, Rückblick) die Fehlerzeilen, die Seite bleibt ganz und die
# übrigen Blöcke stehen, auch Zeilen, Mikrokurven und Stand der Projektliste. Die Zahl der Repos mit Kurve aus den Rohdaten.
mit_kurve = len(gelistet_roh())   # seit 27.09.2026 hat jedes Repo seine Tagesgrafik, auch ohne Commit
grafikbloecke = 4 + 3 * mit_kurve + 1   # seit 03.10.2026 je Projektdetail drei Grafiken (7, 30, 90 Tage)   # seit 04.10.2026 vier Kachelfenster mit Kurve (7 Tage, Woche, 30 Tage, Monat)
echt_kurve = web.kurve
def kaputt(*a, **k):
    raise ValueError("kaputter Zeichner")
web.kurve = kaputt
fehl = []
try:
    kaputte_seite = seite(fehler=fehl)
finally:
    web.kurve = echt_kurve
assert mit_kurve == 3 and kaputte_seite.count('class="blockfehler') == grafikbloecke and kaputte_seite.count("ValueError") == grafikbloecke, (mit_kurve, kaputte_seite.count('class="blockfehler'))
assert '<footer class="fusszeile"><span>© ' in kaputte_seite and "Beispiel-VPS" in kaputte_seite, "die Blöcke ohne Grafik bleiben stehen"
assert kaputte_seite.count('class="mikro"') == kaputte_seite.count('class="pmeta"') == len(gelistet_roh()), "Zeile, Mikrokurve und Stand stehen ohne Wochenkurve"
assert 'class="zbox"' not in kaputte_seite, "ohne Grafik kein Zeiger"
# Die Liste, die /gesund liest: je gebrochener Block ein Eintrag, je Aufbau eine eigene (seit 10.10.2026, Audit S27).
assert len(fehl) == grafikbloecke, fehl
fehl = []
seite(fehler=fehl)
assert fehl == [], "ein sauberer Seitenaufbau trägt keinen Eintrag"
# Clientseitig: eine kaputte Grafik nimmt weder die anderen Grafiken mit noch den Rest des Skripts.
assert "catch(e){console.error('Zeiger:'" in s and "catch(e){aus();}" in s

# Kriterien 5 und 6 an einem festen Donnerstag in eigener Datenbank. Woche ab Mo 21.09.: a 5, b 3, c 2 und 2 Commits in «weg», das
# GitHub nicht mehr listet (zählt in der Wochenkachel, nicht in Projektliste und Rangliste). Dazu 6 Commits in c am Wochenende
# davor: über sieben rollende Tage stünde c mit 8 vorn. Zweige: work/x in a, b und c mit gleichem Stand, feature/y in d.
haupt_k, db.DB = db.DB, Path(tempfile.mkdtemp()) / "kopf.db"
c = db.verbinden(schreibend=True)
jk = datetime(2026, 9, 24, 12, 0, tzinfo=web.TZ)
for r in ("a", "b", "c", "d"):
    c.execute("INSERT INTO repos (repo, default_branch, gesehen) VALUES (?, 'main', ?)", (f"fernwerk-studio/{r}", web.iso_utc(jk.astimezone(timezone.utc))))
roh_k = [("a", 22, i) for i in range(5)] + [("b", 23, i) for i in range(3)] + [("c", 21, i) for i in range(2)] + [("weg", 22, i) for i in range(2)] \
        + [("c", 19, i) for i in range(3)] + [("c", 20, i) for i in range(3)]
for i, (r, tag, m) in enumerate(roh_k):
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, ?, ?, 'dev@fernwerk-studio.example', 'k')",
              (f"k{i}", f"fernwerk-studio/{r}", datetime(2026, 9, tag, 10, m, tzinfo=web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
for r, name in (("a", "work/x"), ("b", "work/x"), ("c", "work/x"), ("d", "feature/y")):
    c.execute("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc) VALUES (?, ?, 2, 0, '2026-09-20T10:00:00Z')", (f"fernwerk-studio/{r}", name))   # 4 Tage vor jk: älter als die Zweigfrist
c.commit(); c.close()
sk = web.seite(jetzt=jk)
with db.verbinden() as c2:
    summe_sql = c2.execute("SELECT COUNT(*) FROM commits WHERE zeitstempel_utc >= ? AND zeitstempel_utc <= ?",
                           (datetime(2026, 9, 21, tzinfo=web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), jk.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))).fetchone()[0]
kacheln_k = re.findall(r'<span class="big mid">([^<]*)</span>(?:<nav class="seg klein umschalter".*?</nav>)?<span class="lbl">([^<]*)</span>', sk)
sieben_kachel = int(dict((l, n) for n, l in kacheln_k)["Commits, letzte 7 Tage"])
with db.verbinden() as c2:
    summe7_sql = c2.execute("SELECT COUNT(*) FROM commits WHERE zeitstempel_utc >= ? AND zeitstempel_utc <= ?",
                            (datetime.combine(jk.date() - timedelta(days=6), time(0), web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), jk.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))).fetchone()[0]
assert sieben_kachel == summe7_sql, (sieben_kachel, summe7_sql)
legende_von = lambda h: re.findall(r'<li><i class="sw (s1|s2|s3|srest)" aria-hidden="true"></i><span class="rname" title="[^"]*">([^<]*)</span><span class="rzahl">([^<]*)</span><span class="ranteil">([^<]*)</span></li>', h)
segmente_von = lambda h: [(k, float(l), float(o)) for k, l, o in re.findall(r'<circle class="rseg (s1|s2|s3|srest)" cx="21" cy="21" r="15.9155" pathLength="100" stroke-dasharray="([\d.]+) [\d.]+" stroke-dashoffset="(-?[\d.]+)"', h)]
rang_k = [(n, z, a_) for _, n, z, a_ in legende_von(fassung(sk, "woche"))]
# Fassung 7 Tage (30.09.2026, Vorgabe): c hat am 19. und 20.09. je drei Commits dazu, die Summe ist 18.
assert [(n, z, a_) for _, n, z, a_ in legende_von(fassung(sk, "7"))] == [("c", "8", "44%"), ("a", "5", "28%"), ("b", "3", "17%"), ("1 weiteres", "2", "11%")]
assert nb("In den letzten 7 Tagen am meisten c, 44% der Commits.") in sk and sk.count("<span>in den letzten 7 Tagen nach Repo</span>") == 1
pzahl = {r: int(z) for r, z in re.findall(r'data-repo="fernwerk-studio/([^"]*)" id="[^"]*"><summary>.*?<span class="pzahl">([^<]*)</span>', sk)}
# Ring (Kriterium b): Werte der drei stärksten gleich den Wochenwerten der Projektzeilen, der Rest («1 weiteres», das nicht mehr
# gelistete Repo) aus der Summe der Wochenkachel; Segmentlängen gleich den Anteilen aus eigenem SQL, Summe 100, lückenlos.
with db.verbinden() as c2:
    je_sql = dict(c2.execute("SELECT repo, COUNT(*) FROM commits WHERE zeitstempel_utc >= ? AND zeitstempel_utc <= ? GROUP BY repo",
                             (datetime(2026, 9, 21, tzinfo=web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), jk.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))))
assert summe_sql == sum(je_sql.values()) == 12, (summe_sql, je_sql)
assert rang_k == [("a", "5", "42%"), ("b", "3", "25%"), ("c", "2", "17%"), ("1 weiteres", "2", "17%")] and all(pzahl[n] == int(z) for n, z, _ in rang_k[:3]), (rang_k, pzahl)
seg_k = segmente_von(fassung(sk, "woche"))
erwartet_k = [je_sql["fernwerk-studio/a"], je_sql["fernwerk-studio/b"], je_sql["fernwerk-studio/c"], je_sql["fernwerk-studio/weg"]]
assert [k for k, _, _ in seg_k] == ["s1", "s2", "s3", "srest"] and all(abs(l - 100 * e / 12) < .001 for (_, l, _), e in zip(seg_k, erwartet_k)), seg_k
assert abs(sum(l for _, l, _ in seg_k) - 100) < .01 and all(abs(-o - sum(l for _, l, _ in seg_k[:i])) < .002 for i, (_, _, o) in enumerate(seg_k)), seg_k
# Ringmitte leer, keine Überschrift. Kopfspalte 3 rechts «M Repos, N diese Woche aktiv» (25.09.2026) zählt N aus derselben Menge wie M und
# die Projektliste: gelistete Repos mit Commits in der Woche, im eigenen SQL 3, obwohl die Woche Commits aus 4 Repos hat (weg ist nicht
# mehr gelistet). Ring, Legende und Wochenkachel oben rechnen weiter über alle 12 Commits; weg steckt in «1 weiteres».
with db.verbinden() as c2:
    letzter_k = c2.execute("SELECT MAX(gestartet_utc) FROM laeufe").fetchone()[0]
    grenze_k = (datetime.fromisoformat(letzter_k.replace("Z", "+00:00")) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ") if letzter_k else ""   # ohne Lauf: alle gelistet
    n_akt = c2.execute("SELECT COUNT(DISTINCT c.repo) FROM commits c JOIN repos r ON r.repo = c.repo WHERE c.zeitstempel_utc >= ? AND c.zeitstempel_utc <= ? AND r.gesehen >= ?",
                       (datetime(2026, 9, 21, tzinfo=web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), jk.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), grenze_k)).fetchone()[0]
g_k = pruefe_gesamt(sk)
aktiv_titel = projekt_meta(sk)[1]
assert (n_akt, len(je_sql)) == (3, 4) and n_akt == aktiv_titel <= g_k and f'<div class="n repos-spalte"><b>{g_k}</b><span>Repos, {n_akt} diese Woche aktiv</span></div>' in sk, (n_akt, aktiv_titel, g_k)
assert "ringmitte" not in sk and sk.count("<span>diese Woche nach Repo</span>") == 1 and nb("Diese Woche am meisten a, 42% der Commits.") in sk
# Kriterium d: vier Zweige. Der Kopf nennt nur die Zahl; im Block Handlungsbedarf steht je Repo und Zweig eine Zeile, work/x also dreimal.
bk = sk[sk.index('<div class="n befunde">'):sk.index('<div class="n"><b>', sk.index('<div class="n befunde">'))]
# Seit 04.10.2026 je Art eine Zeile mit Zahl als Sprunglink auf ihre Spalte (vorher je Repo eine Zeile «Repo, Art»).
assert bk.startswith('<div class="n befunde"><b>4</b><div class="bunter"><ul class="barten">'
                     '<li><a href="#handlung-zweige">4 Zweige warten auf main</a></li></ul>'), bk[:900]
assert nb("Vier Zweige warten auf main.") in sk
hk = sk[sk.index('id="handlung-zweige"'):sk.index('<section class="card projekte">')]
hb_li = lambda sx: sx[sx.index('<section class="card handlung"'):sx.index('<section class="card projekte">')].count('<li id="hb-') if 'class="card handlung"' in sx else 0
zeilen_x = re.findall(r'<li id="hb-[^"]*" title="([^"]*)"><a class="hbrepo" href="https://github.com/fernwerk-studio/(\w)" target="_blank" rel="noopener">\w</a><span class="hbz"><code translate="no">work/x</code> '
                      r'([^<]*) <span class="hbrest">([^<]*)</span></span></li>', hk)
# Reihenfolge der Repos wie in der Projektliste, nach letztem Commit: b (23.09.), a (22.09.), c (21.09.).
assert [r for _, r, *_ in zeilen_x] == ["b", "a", "c"] and all(st == "2 Commits vor main" and al == "seit 4 Tagen" for _, _, st, al in zeilen_x), zeilen_x
assert zeilen_x[0][0] == "Zweig work/x in b: 2 Commits vor main, seit 4 Tagen" and hb_li(sk) == 4, zeilen_x
# Befundspalte mit 0, 1 und 3 Gruppen, jeweils ohne Blockfehler.
def befundspalte(zweige_):
    c = db.verbinden(schreibend=True); c.execute("DELETE FROM zweige")
    # Gewöhnliche Zweige 4 Tage vor jk (älter als die Zweigfrist), Dependabot-Zweige 1 Tag: die Frist gilt für sie nicht, sie bleiben Befund.
    c.executemany("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc) VALUES (?, ?, ?, 0, ?)", [(r_, n_, a_, "2026-09-23T10:00:00Z" if n_.startswith(("dependabot/", "renovate/")) else "2026-09-20T10:00:00Z")
                                                              for r_, n_, a_ in zweige_]); c.commit(); c.close()
    sx = web.seite(jetzt=jk)
    return sx.count("blockfehler"), sx[sx.index('<div class="n befunde">'):sx.index('<div class="n"><b>', sx.index('<div class="n befunde">'))], sx
f0, b0, s0 = befundspalte([])
assert f0 == 0 and b0.startswith('<div class="n befunde"><b>0</b><div class="bunter"><span>Kein Handlungsbedarf</span></div></div>') and '<h3 class="titel">Handlungsbedarf</h3><p class="hbrest"><span id="handlung-warn">0 Repos mit Sicherheitswarnungen</span> · ' in s0 and s0.count('<li id="hb-') == 0, b0[:200]
f1, b1, s1_ = befundspalte([("fernwerk-studio/a", "work/x", 2), ("fernwerk-studio/b", "work/x", 2)])
assert f1 == 0 and "<b>2</b><div class=\"bunter\"><ul class=\"barten\"><li><a href=\"#handlung-zweige\">2 Zweige warten auf main</a>" in b1 and hb_li(s1_) == 2, b1[:300]
f1e, b1e, s1e = befundspalte([("fernwerk-studio/a", "work/x", 2)])
assert f1e == 0 and b1e.startswith('<div class="n befunde"><b>1</b><div class="bunter"><ul class="barten"><li><a href="#handlung-zweige">1 Zweig wartet auf main</a>') and hb_li(s1e) == 1, b1e[:300]
f3, b3, s3 = befundspalte([("fernwerk-studio/a", "work/x", 2), ("fernwerk-studio/b", "work/x", 2), ("fernwerk-studio/c", "fix/z", 1), ("fernwerk-studio/d", "feature/y", 2)])
assert f3 == 0 and hb_li(s3) == 4 and "<b>4</b>" in b3 and '<code translate="no">fix/z</code> 1 Commit vor main <span class="hbrest">seit 4 Tagen</span></span></li>' in s3, b3[:500]
# Zweiter Umbau 09.10.2026: bis HB_SPALTE Einträge steht eine Art in einer Spalte des Rasters, mit mehr nimmt sie die volle Breite (eigenes Raster).
assert '<ul class="hbliste">' in s1_ and '<ul class="hbliste hraster">' in s3 and s3.count('<div class="hraster"><div><p class="hbart"') == 1, s3[:300]
# Dependabot-Zweige (25.09.2026): zählen nicht einzeln als Zweig, je Repo eine Zeile «N Dependabot-Updates offen» mit Link auf die
# offenen Pull Requests von Dependabot, eigene Art nach den Zweigen; im Kopf ein Punkt je Repo, in der Projektzeile «N Dependabot-Updates».
fd, bd, sd = befundspalte([("fernwerk-studio/a", "dependabot/npm_and_yarn/astro-7.3.3", 1), ("fernwerk-studio/a", "dependabot/npm_and_yarn/zod-4.6.5", 1),
                           ("fernwerk-studio/a", "dependabot/npm_and_yarn/dev-5cbd20cfa4", 1), ("fernwerk-studio/b", "work/x", 2)])
assert fd == 0 and bd.startswith('<div class="n befunde"><b>2</b><div class="bunter"><ul class="barten">'
                                 '<li><a href="#handlung-zweige">1 Zweig wartet auf main</a></li><li><a href="#handlung-dependabot">1 Repo mit Dependabot-Updates</a></li></ul>'), bd[:600]
assert nb("Zwei Punkte mit Handlungsbedarf.") in sd and "Vier Zweige" not in sd, "Lagesatz: ein Zweig und ein Repo mit Updates, nicht vier Zweige"
hd = sd[sd.index('id="handlung-dependabot"'):sd.index('<section class="card projekte">')]
# Seit 02.10.2026 trägt der Eintrag je Update Paket, Zielversion (aus dem Zweignamen), Alter und Link auf die gefilterte PR-Liste. Seit
# 03.10.2026 je Update eine Unterzeile, und ein Gruppenupdate (dev-5cbd20cfa4) steht als «Gruppe dev (npm)» am Ende des Eintrags.
eintrag_d = re.search(r'<li id="hb-dependabot-fernwerk-studio_2f_a" title="([^"]*)">(.*?)</li>', hd)
assert eintrag_d and hd.startswith('id="handlung-dependabot">1 Repo mit Dependabot-Updates</p>') and eintrag_d[2].startswith(
    '<a class="hbrepo" href="https://github.com/fernwerk-studio/a/pulls?q=is:pr+is:open+author:app/dependabot" target="_blank" rel="noopener">a</a><span class="hbz">'), hd[:700]
upd = re.findall(r'<span class="hbz"><a href="([^"]*)" target="_blank" rel="noopener">(?:<span translate="no">)?([^<]*)(?:</span>)?( [^<]*)?</a> <span class="hbrest">([^<]*)</span></span>', eintrag_d[2])
assert eintrag_d[2].count('<span class="hbz">') == len(upd) == 3, eintrag_d[2]
assert sorted((p_, v_.strip(), al_) for _, p_, v_, al_ in upd[:2]) == [("astro", "7.3.3", "seit 1 Tag"), ("zod", "4.6.5", "seit 1 Tag")] \
    and upd[2][1:] == ("Gruppe dev (npm)", "", "seit 1 Tag"), upd
zweig_von = lambda p_, v_: "dependabot/npm_and_yarn/dev-5cbd20cfa4" if p_.startswith("Gruppe") else f"dependabot/npm_and_yarn/{p_}-{v_.strip()}"
assert all(u_ == "https://github.com/fernwerk-studio/a/pulls?q=is%3Apr%20is%3Aopen%20head%3A" + web.quote(zweig_von(p_, v_)) for u_, p_, v_, _ in upd), upd
assert eintrag_d[1] == "a: astro 7.3.3, zod 4.6.5, Gruppe dev (npm)", eintrag_d[1]
# Gruppenzweige (03.10.2026): Ökosystem lesbar, Gruppenname ohne Hash, mit Verzeichnis, fremdes Ökosystem mit Leerzeichen; ein Einzelupdate
# bleibt Paket und Version, auch mit Bindestrich im Paket; ohne Muster der Zweigname. Deutsch und englisch.
gruppen = {"dependabot/npm_and_yarn/dev-c9b9e20c55": ("Gruppe dev (npm)", "Group dev (npm)"),
           "dependabot/github_actions/actions-0123456789": ("Gruppe actions (GitHub Actions)", "Group actions (GitHub Actions)"),
           "dependabot/pip/web/prod-deps-abcdef0123": ("Gruppe prod-deps (pip)", "Group prod-deps (pip)"),
           "dependabot/docker_compose/basis-abcdef0123": ("Gruppe basis (docker compose)", "Group basis (docker compose)")}
for z_, (de_, en_) in gruppen.items():
    for sprache_, erwartet_ in (("de", de_), ("en", en_)):
        with sprache.gilt(sprache_):
            u_ = web.dependabot_update("https://github.com/fernwerk-studio/r", z_, 2)
        assert u_["gruppe"] and u_["version"] == "" and u_["paket"] == erwartet_, (z_, sprache_, u_)
for z_, (p_, v_) in {"dependabot/npm_and_yarn/maplibre-gl-6.11.2": ("maplibre-gl", "6.11.2"), "dependabot/npm_and_yarn/astrojs/react-7.0.0": ("astrojs/react", "7.0.0"),
                     "dependabot/pip/x": ("dependabot/pip/x", "")}.items():
    u_ = web.dependabot_update("https://github.com/fernwerk-studio/r", z_, 2)
    assert not u_["gruppe"] and (u_["paket"], u_["version"]) == (p_, v_), (z_, u_)
assert sd.index('id="handlung-zweige"') < sd.index('id="handlung-dependabot"')
assert '3 Dependabot-Updates' in zeile_von(sd, "fernwerk-studio/a") and "Zweig dependabot/" not in sd, "Projektzeile: Zahl statt Zweigbefund"
fd1, bd1, sd1 = befundspalte([("fernwerk-studio/a", "dependabot/pip/x", 1)])
assert bd1.startswith('<div class="n befunde"><b>1</b><div class="bunter"><ul class="barten"><li><a href="#handlung-dependabot">1 Repo mit Dependabot-Updates</a></li></ul>') and nb("Ein Repo hat offene Dependabot-Updates.") in sd1, bd1[:300]
assert '<span translate="no">dependabot/pip/x</span></a> <span class="hbrest">seit 1 Tag</span>' in sd1 \
    and '<span class="pbefund"><a href="#hb-dependabot-fernwerk-studio_2f_a">1 Dependabot-Update</a></span>' in summary_von(sd1, "fernwerk-studio/a"), ("Singular", summary_von(sd1, "fernwerk-studio/a")[-300:])
# Renovate-Zweige (27.10.2026) gleichwertig: eigene Art nach Dependabot, Link auf die offenen Pull Requests von renovate[bot], je Update
# Paket und Zielversion aus dem Zweignamen, ohne Muster der Zweigname; im Kopf ein Punkt je Repo, in der Projektzeile «N Renovate-Updates».
fr, br, sr = befundspalte([("fernwerk-studio/a", "renovate/astro-5.x", 1), ("fernwerk-studio/a", "renovate/lock-file-maintenance", 1),
                           ("fernwerk-studio/b", "dependabot/pip/x", 1)])
assert fr == 0 and br.startswith('<div class="n befunde"><b>2</b><div class="bunter"><ul class="barten">'
                                 '<li><a href="#handlung-dependabot">1 Repo mit Dependabot-Updates</a></li><li><a href="#handlung-renovate">1 Repo mit Renovate-Updates</a></li></ul>'), br[:600]
assert nb("Zwei Punkte mit Handlungsbedarf.") in sr and sr.index('id="handlung-dependabot"') < sr.index('id="handlung-renovate"')
hr = sr[sr.index('id="handlung-renovate"'):sr.index('<section class="card projekte">')]
eintrag_r = re.search(r'<li id="hb-renovate-fernwerk-studio_2f_a" title="([^"]*)">(.*?)</li>', hr)
assert eintrag_r and hr.startswith('id="handlung-renovate">1 Repo mit Renovate-Updates</p>') and eintrag_r[2].startswith(
    '<a class="hbrepo" href="https://github.com/fernwerk-studio/a/pulls?q=is:pr+is:open+author:app/renovate" target="_blank" rel="noopener">a</a><span class="hbz">'), hr[:700]
assert eintrag_r[1] == "a: astro 5.x, renovate/lock-file-maintenance", eintrag_r[1]
assert "https://github.com/fernwerk-studio/a/pulls?q=is%3Apr%20is%3Aopen%20head%3A" + web.quote("renovate/astro-5.x") in eintrag_r[2], eintrag_r[2]
assert '2 Renovate-Updates' in zeile_von(sr, "fernwerk-studio/a") and "Zweig renovate/" not in sr, "Projektzeile: Zahl statt Zweigbefund"
for z_, (p_, v_) in {"renovate/maplibre-gl-6.x": ("maplibre-gl", "6.x"), "renovate/astrojs-react-4.2.1": ("astrojs-react", "4.2.1"),
                     "renovate/all-minor-patch": ("renovate/all-minor-patch", "")}.items():
    u_ = web.dependabot_update("https://github.com/fernwerk-studio/r", z_, 2)
    assert not u_["gruppe"] and (u_["paket"], u_["version"]) == (p_, v_), (z_, u_)
fr1, br1, sr1 = befundspalte([("fernwerk-studio/a", "renovate/zod-4.x", 1)])
assert br1.startswith('<div class="n befunde"><b>1</b><div class="bunter"><ul class="barten"><li><a href="#handlung-renovate">1 Repo mit Renovate-Updates</a></li></ul>') and nb("Ein Repo hat offene Renovate-Updates.") in sr1, br1[:300]
assert '<span class="pbefund"><a href="#hb-renovate-fernwerk-studio_2f_a">1 Renovate-Update</a></span>' in summary_von(sr1, "fernwerk-studio/a"), ("Singular", summary_von(sr1, "fernwerk-studio/a")[-300:])
# Ohne Obergrenze (04.10.2026, vorher 3 je Art und «alle N anzeigen»): alle sieben Einträge stehen ohne Klick da, kein Aufklapper;
# die alten Zeichen «zweige-alle» und «zweige» in ?handlung= werden übergangen.
f7, b7, s7 = befundspalte([("fernwerk-studio/a", f"feature/z{i}", 1) for i in range(7)])
hz = s7[s7.index('id="handlung-zweige"'):s7.index('<section class="card projekte">')]
assert hb_li(s7) == 7 and "<details" not in hz and "anzeigen" not in hz and "anzeigen" not in b7, hz[:300]
assert b7.count('<li>') == 1 and '<a href="#handlung-zweige">7 Zweige warten auf main</a>' in b7, ("Kopf: eine Zeile je Art", b7[:300])
with db.verbinden() as c2:
    s7o = web.seite(jetzt=jk, handlung=web.handlung_zeichen("zweige-alle,zweige"))
assert web.handlung_zeichen("zweige-alle,zweige") == frozenset() and s7o.count('<li id="hb-zweige-') == 7, "alte Zeichen ohne Wirkung"
# Ring, Randfälle (Kriterium c): eine Woche ohne Commit (ein gedämpftes Segment über 100 und die 0, seit 26.10.2026), ein einziges Repo (ein Segment über 100),
# jeweils ohne Blockfehler. Vier Repos stehen oben.
s_leer = web.seite(jetzt=datetime(2026, 10, 7, 12, 0, tzinfo=web.TZ))
ring_leer = s_leer[s_leer.index('<div class="n ring-spalte">'):s_leer.index('<div class="n befunde">')]
leer_ring = lambda ab, aria, unter: ('<div class="n ring-spalte"><div class="ringbox"><svg class="ring" viewBox="0 0 42 42" width="112" height="112" role="img" '
    f'aria-label="Commits {aria} ab {ab} nach Repo, 0"><circle class="rseg srest" cx="21" cy="21" r="15.9155" pathLength="100" stroke-dasharray="100.000 0.000" '
    'stroke-dashoffset="-0.000" transform="rotate(-90 21 21)"/></svg><ul class="rlegende"><li><i class="sw srest" aria-hidden="true"></i><span class="rzahl">0</span>'
    f'</li></ul></div><span>{unter}</span></div>')
assert ring_leer == (leer_ring("01.10.", "der letzten 7 Tage", "in den letzten 7 Tagen nach Repo") + '</div><div class="fenster" data-gruppe="k2" data-fenster="woche" hidden>'
                     + leer_ring("05.10.", "der Woche", "diese Woche nach Repo") + '</div></div>') and s_leer.count("blockfehler") == 0, ring_leer
# Der Lagesatz behält den Leersatz (lage.leer7, lage.woche_leer), nur die Ringspalte zeigt den Ring mit 0.
assert nb("In den letzten 7 Tagen kein Commit.") in s_leer and nb("Diese Woche noch kein Commit.") in s_leer, "Lagesatz unverändert"
solo = web.ring_html([("solo", 7, 100.0)], 7, 1, date(2026, 9, 21))
assert solo.count('class="rseg') == 1 and 'stroke-dasharray="100.000 0.000"' in solo and solo.endswith('<span>diese Woche nach Repo</span></div>'), solo
# Sechs Repos in der Woche, die Legende hat vier Zeilen (drei und «3 weitere»); die Unterzeile nennt seit dem Umbau (25.09.2026)
# keine Zahl mehr, die Repos stehen in Kopfspalte 3 neben der Serie.
sechs = web.ring_html([("a", 5, 5 * 100 / 14), ("b", 3, 3 * 100 / 14), ("c", 2, 2 * 100 / 14)], 14, 6, date(2026, 9, 21))
assert '<span>diese Woche nach Repo</span>' in sechs and sechs.count("<li>") == 4 and '<span class="rname" title="3 weitere">3 weitere</span>' in sechs, sechs[-400:]
# Ein Name mit 40 Zeichen bleibt in seiner Spalte: einzeilig, abgeschnitten mit Auslassungszeichen, der volle Name im title.
lang_n = "fernwerk-studio/" + "x" * 24   # 40 Zeichen wie vorher
lang_r = web.ring_html([(lang_n, 7, 100.0)], 7, 1, date(2026, 9, 21))
assert f'<span class="rname" title="{lang_n}">{lang_n}</span>' in lang_r and len(lang_n) == 40
assert all(d in " ".join(regeln[".rname"]) for d in ("overflow: hidden", "text-overflow: ellipsis", "white-space: nowrap")), regeln[".rname"]
haupt_1, db.DB = db.DB, Path(tempfile.mkdtemp()) / "ein-repo.db"
c = db.verbinden(schreibend=True)
c.execute("INSERT INTO repos (repo, default_branch, gesehen) VALUES ('fernwerk-studio/solo', 'main', ?)", (web.iso_utc(jk.astimezone(timezone.utc)),))
c.executemany("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, 'fernwerk-studio/solo', ?, 'dev@fernwerk-studio.example', 's')",
              [(f"so{i}", datetime(2026, 9, 22, 9, i, tzinfo=web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")) for i in range(3)])
c.commit(); c.close()
s_1 = web.seite(jetzt=jk)
assert [(k, round(l, 3)) for k, l, _ in segmente_von(s_1)] == [("s1", 100.0)] * 2 and legende_von(s_1) == [("s1", "solo", "3", "100%")] * 2 and s_1.count("blockfehler") == 0, segmente_von(s_1)
# Metazeile im Singular (26.09.2026): «1 Repo · 1 diese Woche aktiv», «1 repo · 1 active this week».
assert '<p class="delta bezug pkopf"><span class="kwert">1</span> Repo · <span class="kwert">1</span> diese Woche aktiv</p>' in s_1, "Singular DE"
assert '<p class="delta bezug pkopf"><span class="kwert">1</span> repo · <span class="kwert">1</span> active this week</p>' in web.seite(jetzt=jk, sprache="en"), "Singular EN"
db.DB = haupt_1
# Blockisolation der neuen Blöcke: bricht einer, steht seine Fehlerzeile genau einmal, alles andere bleibt.
MARKER_K = {"Wochenring": 'class="n ring-spalte"', "Befunde": 'class="n befunde"', "Heute-Aufteilung": 'class="hrepo"', "Kopf": 'class="nums"', "Projekte": 'class="pzahl"',
            "Handlungsbedarf": 'class="card handlung"'}
c = db.verbinden(schreibend=True)
c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES ('kh', 'fernwerk-studio/a', ?, 'dev@fernwerk-studio.example', 'heute')", ((jk - timedelta(hours=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),))
c.commit(); c.close()
assert all(m in web.seite(jetzt=jk) for m in MARKER_K.values())
for fn, name in (("ring_html", "Wochenring"), ("befunde_html", "Befunde"), ("heute_liste", "Heute-Aufteilung"), ("handlung_block", "Handlungsbedarf")):
    echt = getattr(web, fn)
    setattr(web, fn, kaputt_fn)
    fehl = []
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            sx = web.seite(jetzt=jk, fehler=fehl)
    finally:
        setattr(web, fn, echt)
    # Seit 30.09.2026 zeichnet ring_html zwei Blöcke: die Fassung 7 Tage und die Woche (Umschalter der Kachel 2).
    namen_ = ["Ring 7 Tage", name] if fn == "ring_html" else [name]
    assert sx.count(FEHLER) == len(namen_) and f'data-block="{name}"' in sx and fehl == namen_, (name, sx.count(FEHLER), fehl)
    assert all(m in sx for k_, m in MARKER_K.items() if k_ != name) and MARKER_K[name] not in sx, name
db.DB = haupt_k

# Kriterium 3 (Nachtrag 25.09.2026): Projektblock mit fünf aktiven Repos und acht ohne Commits in der Woche, eigene Datenbank.
haupt_g, db.DB = db.DB, Path(tempfile.mkdtemp()) / "gruppen.db"
c = db.verbinden(schreibend=True)
jg = datetime(2026, 9, 24, 12, 0, tzinfo=web.TZ)
for i in range(13):
    c.execute("INSERT INTO repos (repo, default_branch, gesehen) VALUES (?, 'main', ?)", (f"fernwerk-studio/g{i:02d}", web.iso_utc(jg.astimezone(timezone.utc))))
    tag = datetime(2026, 9, 22 if i < 5 else 17 - i % 7, 9, i, tzinfo=web.TZ)   # g00 bis g04 in dieser Woche, der Rest davor
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff) VALUES (?, ?, ?, 'dev@fernwerk-studio.example', 'g')",
              (f"g{i}", f"fernwerk-studio/g{i:02d}", tag.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
c.commit(); c.close()
gruppen_seite = lambda jz_: gruppen_von(web.seite(jetzt=jz_))
g2 = gruppen_seite(jg)
assert [(t, n, lin) for t, n, lin, _ in g2] == [("aktiv", 5, False), ("Ohne Commits diese Woche", 8, True)], g2
assert sorted(g2[0][3]) == [f"fernwerk-studio/g{i:02d}" for i in range(5)] and "Repos:" not in web.seite(jetzt=jg), g2
# Randfälle: eine Woche ganz ohne Commit ergibt nur die zweite Gruppe und keine Linie; alle aktiv nur die erste.
g_leer = gruppen_seite(datetime(2026, 10, 7, 12, 0, tzinfo=web.TZ))
assert [(t, n, lin) for t, n, lin, _ in g_leer] == [("Ohne Commits diese Woche", 13, False)], g_leer
c = db.verbinden(schreibend=True); c.execute("DELETE FROM repos WHERE repo >= 'fernwerk-studio/g05'"); c.commit(); c.close()
g_alle = gruppen_seite(jg)
assert [(t, n, lin) for t, n, lin, _ in g_alle] == [("aktiv", 5, False)], g_alle
# Teil A (25.09.2026): die ruhende Gruppe ist ein Aufklapper, ohne Parameter zu, mit ?ruhend=offen offen, beides schon im
# HTML des Servers; die aktive Gruppe bleibt immer offen. Über den echten Handler, damit das Lesen des Parameters mitgeprüft ist.
c = db.verbinden(schreibend=True)
c.execute("INSERT INTO repos (repo, default_branch, gesehen) VALUES ('fernwerk-studio/g09', 'main', ?)", (web.iso_utc(jg.astimezone(timezone.utc)),)); c.commit(); c.close()
def seite_ueber(pfad):
    h_ = web.Handler.__new__(web.Handler); h_.wfile = io.BytesIO(); h_.kopf_nur = False; h_.headers = {}
    h_.send_response = lambda code: setattr(h_, "code", code); h_.send_header = lambda *a: None; h_.end_headers = lambda: None
    echt_seite = web.seite
    web.seite = lambda *a, **k: echt_seite(*a, **({"jetzt": jg} | k))
    try:
        h_.bedienen(urlparse(pfad))
    finally:
        web.seite = echt_seite
    return h_.code, h_.wfile.getvalue().decode()
for pfad, offen in (("/", False), ("/?ruhend=offen", True), ("/?ruhend=zu", False), ("/?rueckblick=90&ruhend=offen", True)):
    code, sx = seite_ueber(pfad)
    g_ = gruppen_von(sx, mit_zustand=True)
    assert code == 200 and [(t, n, o) for t, n, _, _, o in g_] == [("aktiv", 5, True), ("Ohne Commits diese Woche", 1, offen)], (pfad, g_)
    assert '<summary class="pgruppe getrennt">Ohne Commits diese Woche<span class="panz kwert">1</span><span class="chev" aria-hidden="true"></span></summary>' in sx, pfad
# Zahlen im Projektblock (26.09.2026), beide Sprachen, Wortlaut fest: keine Zahl im Titel, links darunter die Metazeile als zwei Paare
# (Wert in .kwert), die aktive Gruppe seit 27.09.2026 ohne Titel mit der Kopfzeile aller Spalten, die zweite Gruppe mit ihrer Zahl in
# .kwert und aufgeklappt derselben Kopfzeile. 6 Repos, davon 5 diese Woche aktiv, aus den Commits oben (g00 bis g04 und g09).
for pfad, titel_, meta_, spalten_, ohne_ in (
        ("/?ruhend=offen", "Projekte", '6</span> Repos · <span class="kwert">5</span> diese Woche aktiv', ("Repo", "30 Tage", "Zuletzt", "Betreff"), "Ohne Commits diese Woche"),
        ("/?ruhend=offen&lang=en", "Projects", '6</span> repos · <span class="kwert">5</span> active this week', ("Repo", "30 days", "Last", "Subject"), "No commits this week")):
    code, sx = seite_ueber(pfad)
    assert code == 200 and f'<h3 class="titel">{titel_}</h3>\n  <p class="delta bezug pkopf"><span class="kwert">{meta_}</p>' in sx, (pfad, sx[sx.index('card projekte'):][:400])
    kopf_ = (f'<p class="pgruppe spalten"><span class="pkname">{spalten_[0]}</span><span class="pwert pkw"><span class="pspalte">Commits</span></span>'
             f'<span class="pkmikro">{spalten_[1]}</span><span class="pkalter">{spalten_[2]}</span><span class="pkbetreff">{spalten_[3]}</span></p>')
    assert f'<h3 class="titel">{titel_}</h3>\n  <p class="delta bezug pkopf"><span class="kwert">{meta_}</p>{kopf_}' in sx and sx.count(kopf_) == 2 and sx.count('class="pspalte"') == 2, pfad
    assert f'<summary class="pgruppe getrennt">{ohne_}<span class="panz kwert">1</span><span class="chev" aria-hidden="true"></span></summary>{kopf_}' in sx and sx.count('class="panz') == 1, pfad
    assert "gtitel" not in sx and "Diese Woche aktiv" not in sx and "Active this week" not in sx, pfad
# Der Spaltenkopf steht auf dem Raster der Zeilen (dieselben Spalten in allen drei Breitenregeln) und seit der Korrektur vom 26.09.2026
# rechtsbündig auf der rechten Kante der Zahlen: er sitzt in einem .pwert in der Schrift der Zeile, dessen 5ch- bzw. 4ch-Spur die Zahlenspur
# ist, und endet darin. Die Kopfzeile ist seit 27.09.2026 einzeilig in allen Breiten; unter 700 px fallen die Köpfe von Mikrokurve und Betreff
# mit ihren Spalten weg.
css_p = (Path(web.__file__).parent / "web" / "style.css").read_text()
assert css_p.count("details.prow > summary, .pgruppe.spalten {") == 3, "Spaltenkopf nicht auf dem Zeilenraster"
assert ".pgruppe .pspalte { grid-column: 1; justify-self: end;" in css_p and "details.prow > summary, .pgruppe .pkw { font-size: .8rem; }" in css_p, "Spaltenkopf nicht rechtsbündig auf der Zahlenkante"
m980 = css_p[css_p.index("@media (max-width: 980px) {"):css_p.index("@media", css_p.index("@media (max-width: 980px) {") + 1)]
m700 = css_p[css_p.index("@media (max-width: 700px) {"):css_p.index("@media", css_p.index("@media (max-width: 700px) {") + 1)]
assert "titel" not in m980 and "titel" not in m700 and "gtitel" not in css_p, "Regel des entfallenen Gruppentitels steht noch"
assert ".prow .mikro, .pbetreff, .pbefund, .pgruppe .pkname, .pgruppe .pkmikro, .pgruppe .pkbetreff { display: none; }" in m700, "Kopf fällt schmal nicht mit seiner Spalte"
# Das Skript führt ?ruhend nach und lässt die übrigen Parameter und den Anker stehen; der Sprung öffnet Gruppe und Zeile.
# Seit 04.10.2026 mit derselben Mechanik «und N weitere Repos» der Heute-Kachel als ?heute=offen.
assert "[['ruhend','details.gruppe.ruhend'],['heute','details.gruppe.heute-rest']]" in sx and "if(g.open)q.set(p[0],'offen');else q.delete(p[0]);history.replaceState(null,'',(q.toString()?'/?'+q:'/')+location.hash)" in sx
assert "var q=new URLSearchParams(location.search);\n  if(g.open)" in sx
assert "var g=d.parentNode.closest('details.gruppe');if(g)g.open=true;d.open=true;" in sx
db.DB = haupt_g

# Merge-Commits zählen nirgends (25.09.2026), eigene Datenbank: am Mittwoch 16.09. drei Commits in fernwerk-studio/m, dazu zwei Merges
# (eltern 2) heute und ein Merge allein am Dienstag. Ein Squash (eltern 1) zählt, ein unbekannter Altbestand (NULL) auch.
haupt_mg, db.DB = db.DB, Path(tempfile.mkdtemp()) / "merges.db"
c = db.verbinden(schreibend=True)
jm_ = datetime(2026, 9, 16, 20, 0, tzinfo=web.TZ)
c.execute("INSERT INTO repos (repo, default_branch, gesehen) VALUES ('fernwerk-studio/m', 'main', ?)", (web.iso_utc(jm_.astimezone(timezone.utc)),))
roh_mg = [("m1", 16, 9, 1, "Arbeit eins"), ("m2", 16, 10, None, "Arbeit zwei, Altbestand"), ("m3", 16, 11, 1, "Squash einer PR (#4)"),
          ("x1", 16, 12, 2, "Merge branch feature"), ("x2", 16, 19, 2, "Merge pull request #5"), ("x3", 15, 9, 2, "Merge am Dienstag")]
for sha, tag, h, el, b in roh_mg:
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff, eltern) VALUES (?, 'fernwerk-studio/m', ?, 'dev@fernwerk-studio.example', ?, ?)",
              (sha, datetime(2026, 9, tag, h, 0, tzinfo=web.TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), b, el))
c.commit(); c.close()
with db.verbinden() as c2:
    km = web.kacheldaten(c2, jm_.date(), jm_)
    tage_m = web.tageszaehlung(c2)
sm = web.seite(jetzt=jm_)
assert (km[0]["n"], km[0]["vor"], km[0]["aufteilung"]) == (3, 0, [("fernwerk-studio/m", 3, "2026-09-16T09:00:00Z")]), km[0]
assert [z[0] for z in km[0]["commits"]] == ["m3", "m2", "m1"] or [z[2] for z in km[0]["commits"]] == ["Squash einer PR (#4)", "Arbeit zwei, Altbestand", "Arbeit eins"], km[0]["commits"]
assert km[1]["n"] == 3 and km[1]["zeile2"] == "an 1 von 7 Tagen, in 1 Repo" and km[2]["n"] == 3, (km[1], km[2]["n"])
assert tage_m == {date(2026, 9, 16): 3}, ("der Dienstag mit nur einem Merge ist kein Tag mit Commit", tage_m)
assert "Merge" not in sm[sm.index('<div class="card tile kpi">'):sm.index('<section class="card projekte">')], "kein Merge in Aufteilung und «Zuletzt»"
assert '<span class="pzahl">3</span>' in sm and "Merge pull request" not in sm, "Projektzeile ohne Merge, auch nicht als letzter Betreff"
assert '<span class="rzahl">3</span>' in sm and nb("Diese Woche am meisten m, 100% der Commits. Heute wieder aktiv.") in sm, "Ring und Lagesatz ohne Merge"
assert "Summe 3" in sm[sm.index('<section class="card rueckblick">'):], "Rückblick ohne Merge"
db.DB = haupt_mg
# Die laufende Woche der Detailkurve trägt die Akzentfarbe (25.09.2026), die übrigen angebrochenen Segmente bleiben gedämpft.
assert regeln[".plot .l.teil.jetzt"] == [" stroke: var(--eis); "] and "var(--lbl)" in " ".join(regeln[".plot .l.teil"]), regeln.get(".plot .l.teil.jetzt")
assert "background: var(--eis)" in " ".join(regeln[".pjetzt"]), regeln.get(".pjetzt")
# Die Codezeile der Wochenkachel ist einzeilig (25.09.2026, vorher fest zwei Zeilen hoch), sonst hinge der Platz der Heute-Kachel an einer Zahl.
assert regeln[".tile.kpi .delta.zeilen"] == [" white-space: nowrap; overflow: hidden; text-overflow: ellipsis; "], regeln.get(".tile.kpi .delta.zeilen")
# Seit 25.09.2026 (Teil B): alle Blöcke der GitHub-Zone mit demselben Abstand, der Projektblock ohne eigenen Aufschlag.
assert ".projekte" not in regeln or not any("margin-top" in d for d in regeln[".projekte"]), regeln.get(".projekte")
# Abstand Lagesatz zu Reihe: 25.09.2026 von --s3 (12 px) auf --s6 (24 px), seit 26.09.2026 im GitHub-Kopf --s8 (32 px); die Serverkachel bleibt bei 24 px.
# Seit dem zweiten Lauf vom 26.09.2026 beide Zonen 32 px aus EINER Variable (--lage-abstand), vorher die Serverkachel 24 px.
assert "gap: var(--lage-abstand)" in " ".join(regeln[".lage"]) and "gap: var(--lage-abstand)" in " ".join(regeln[".tile.lage"]), (regeln[".lage"], regeln[".tile.lage"])
assert "--lage-abstand: var(--s8);" in (web.WEB / "tokens.css").read_text() and "--s8: 2rem" in (web.WEB / "tokens.css").read_text()
# Delta der Projektzeilen (25.09.2026): unter 10 Commits in der Vorwoche bis zum selben Wochentag «Vorwoche N» statt Prozent,
# ab 10 der Prozentwert; die Schwelle steht hier als Zahl, nicht aus web.
assert [web.delta_html(a_, b_, "Vorwoche zum selben Zeitpunkt")[0] for a_, b_ in ((9, 1), (0, 0), (5, 9), (12, 10), (5, 10), (10, 10))] == [
    '<span class="delta">Vorwoche 1</span>', '<span class="delta">Vorwoche 0</span>', '<span class="delta">Vorwoche 9</span>',
    '<span class="delta">↑ +20%</span>', '<span class="delta">↓ \u221250%</span>', '<span class="delta">→ 0%</span>']
# Geschützte Leerzeichen (Nachbesserung 25.09.2026): in beiden Lagesätzen, beiden Sprachen, Ziffern und Zahlwörter; nie ein normales
# Leerzeichen zwischen einer Zahl und ihrem Wort.
from app.sprache import gilt as gilt_nb  # noqa: E402
for sprache_nb in ("de", "en"):
    with gilt_nb(sprache_nb):
        sv_nb = web.server_lage_block(beispiel | {"erhoben_utc": web.iso_utc(datetime.now(timezone.utc))}, datetime.now(timezone.utc))
    sv_satz = sv_nb[:sv_nb.index("</p>")]
    assert ("vier\u00a0Dienste" if sprache_nb == "de" else "four\u00a0services") in sv_satz, sv_satz
    assert not re.search(r"(?<![\w'.,-])(\d[\d'.,]*|ein|zwei|drei|vier|fünf|sieben|one|two|three|four|five|seven) (?=\w)", sv_satz, re.I), sv_satz
stress_nb = web.geschuetzt("Seit 12 Tagen ohne Unterbruch. Sieben Punkte mit Handlungsbedarf. Most commits: 1’234 lines, 34% of 87 items, one timer.")
# Seit 26.09.2026 zusätzlich das erste Wort jedes Satzes an das folgende gebunden: kein Satzanfang am Zeilenende.
# Eine Zahl am Satzende nimmt den Punkt nicht mit: «Exit 1. gitpuls: Timer» bindet «gitpuls:» an «Timer» (im Browser als Satzanfang am
# Zeilenende gefunden, 26.09.2026, Stresszustand der Serverzone).
assert web.geschuetzt("Lauf gescheitert, Exit 1. gitpuls: Timer fehlt.") == '<span class="satz">Lauf\u00a0gescheitert, Exit 1.</span> <span class="satz">gitpuls:\u00a0Timer fehlt.</span>', web.geschuetzt("Lauf gescheitert, Exit 1. gitpuls: Timer fehlt.")
assert stress_nb == ('<span class="satz">Seit\u00a012\u00a0Tagen ohne Unterbruch.</span> <span class="satz">Sieben\u00a0Punkte mit Handlungsbedarf.</span> '
                     '<span class="satz">Most\u00a0commits: 1’234\u00a0lines, 34%\u00a0of 87\u00a0items, one\u00a0timer.</span>'), stress_nb
# (26.09.2026, zweiter Lauf) Jeder Satz ein unteilbares Stück, nur ein überlanger bricht in sich um.
assert regeln[".f .satz"] == [" display: inline-block; max-width: 100%; "], regeln.get(".f .satz")

# Englischer Durchgang (25.09.2026): die Seite in en, jeder Block einzeln beim Zeichnen mitgeschnitten, ohne Umlaut und ohne deutsches
# Wort ausserhalb der Daten (translate="no": Betreffzeilen, Zweck der Dienste, README-Sätze, Topics, Zweignamen). Mehrere Serverzustände,
# damit auch die Zeilen der Abweichungen vorkommen, und ein Blockfehler, der seine Zeile ebenfalls englisch trägt.
from tests.sprachpruefung import deutsch, text_ohne_daten  # noqa: E402
with db.verbinden() as c_:
    namen = {r[0] for r in c_.execute("SELECT repo FROM repos UNION SELECT repo FROM commits")}
    namen |= {n.split("/")[1] for n in namen} | {r[0] for r in c_.execute("SELECT name FROM zweige")}
en_sv = json.loads(json.dumps(beispiel))
en_sv["erhoben_utc"] = web.iso_utc(datetime.now(timezone.utc))
en_sv["dienste"][3]["container"][0]["status"] = "exited"
en_sv["dienste"][1]["timer"][0].update(status="inactive", ergebnis="exit-code", exit=1, letzter_utc=web.iso_utc(datetime.now(timezone.utc)))
en_sv["dienste"][2]["timer"].append({"unit": "fehlt-probe", "status": "fehlt"})
en_sv["dienste"][0]["container"][0]["neustarts"] = 2
en_sv["zustand"]["neustart_noetig"] = True
alt_zust = json.loads(json.dumps(en_sv)); alt_zust["erhoben_utc"] = web.iso_utc(datetime.now(timezone.utc) - timedelta(minutes=30))
erfasst, echt_block = [], web.block
def mit_erfassung(name, fn, *a):
    h = echt_block(name, fn, *a)
    erfasst.append((name, h))
    return h
web.block = mit_erfassung
try:
    for zustand in (beispiel | {"erhoben_utc": web.iso_utc(datetime.now(timezone.utc))}, en_sv, alt_zust, None):
        if zustand is None:
            web.SERVER.unlink()
        else:
            web.SERVER.write_text(json.dumps(zustand))
        for ruhend_ in (True, False):
            s_en = web.seite(sprache="en", ruhend=ruhend_)
            assert s_en.startswith('<!doctype html><html lang="en">'), s_en[:60]
            funde = [(n, deutsch(text_ohne_daten(h), namen)) for n, h in erfasst]
            assert not [f for f in funde if f[1]], [f for f in funde if f[1]][:5]
            assert not deutsch(text_ohne_daten(s_en), namen), deutsch(text_ohne_daten(s_en), namen)[:5]
    arten = {n.split(" ")[0] for n, _ in erfasst}
    assert {"Server", "Serverstand", "Server-Lage", "Dienste", "Maschine", "Installiert", "Firewall", "Updates", "Sammelstand", "GitHub-Lage",
            "Wochenring", "Befunde", "Kennzahlen", "Kachel", "Heute-Aufteilung", "Projekte", "Projektzeile", "Mikrokurve", "Wochenkurve",
            "Projektstand", "Rückblick", "Fusszeile"} <= arten, arten
    # Blockfehler auf Englisch: die Fehlerzeile kommt aus der Tabelle.
    echt_ring = web.ring_html
    web.ring_html = lambda *a: 1 / 0
    try:
        s_en, s_de = web.seite(sprache="en"), web.seite(sprache="de")
    finally:
        web.ring_html = echt_ring
    assert 'data-block="Wochenring">Block could not be drawn: ZeroDivisionError</p>' in s_en and not deutsch(text_ohne_daten(s_en), namen)
    assert 'data-block="Wochenring">Block konnte nicht gezeichnet werden: ZeroDivisionError</p>' in s_de
finally:
    web.block = echt_block
    web.SERVER.write_text(json.dumps(beispiel))
print("ok: englischer Durchgang, " + str(len({n for n, _ in erfasst})) + " Blöcke ohne Deutsch ausserhalb der Daten, Blockfehler englisch")
print("ok: Serverstand fehlt, frisch, alt; Lagesatz mit Abweichungen zuerst; Warnfarbe nur bei Abweichung; Repo-Regel;"
      " Zeiger: Marke fertig im Zeichner gesetzt, Stellen auf den Punkten, Vergleichsreihe ohne Wert ohne Marke,"
      " Kopfzeile je Grafik, Maus und Stift, Blockisolation;"
      " Rückblick ab Datenbeginn; zweite Reihe vorhanden/leer/ohne Persistenz/abweichend/keine; Updates null/Debian/Docker/beide; Achse bei leerer Reihe, einem Punkt, Höchstwert null")

# ---- Lesbarkeit Kopf, Kacheln, Projekte, Handlungsbedarf, Fusszeile (26.09.2026) ---------------------------------------------
# Eigene Datenbank mit fester Uhr: zwei Repos mit Commits dieser Woche und Codezeilen, dazu ein Zweig, ein roter Lauf und Warnungen.
haupt_l, db.DB = db.DB, Path(tempfile.mkdtemp()) / "lesbarkeit.db"
jl = datetime(2026, 9, 26, 15, 0, tzinfo=web.TZ)
iso_l = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
c = db.verbinden(schreibend=True)
c.execute("INSERT INTO laeufe (gestartet_utc, beendet_utc, neu) VALUES (?, ?, 1)", (iso_l(jl - timedelta(minutes=9)), iso_l(jl - timedelta(minutes=8))))
for r_, sp_ in (("fernwerk-studio/alpha", "Python"), ("fernwerk-studio/beta", "HTML")):
    c.execute("INSERT INTO repos (repo, default_branch, sprache, pushed_at, gesehen, id, html_url) VALUES (?, 'main', ?, ?, ?, ?, ?)",
              (r_, sp_, iso_l(jl), iso_l(jl - timedelta(minutes=9)), hash(r_) % 10**6, f"https://github.com/{r_}"))
for i, (r_, tage_, plus_, minus_) in enumerate((("fernwerk-studio/alpha", 0, 45000, 11000), ("fernwerk-studio/alpha", 1, 997, 300), ("fernwerk-studio/beta", 2, 8254, 2059), ("fernwerk-studio/beta", 9, 10, 1))):
    sha = f"l{i}"
    c.execute("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff, eltern, dateien_geladen) VALUES (?, ?, ?, 'dev@fernwerk-studio.example', ?, 1, 1)",
              (sha, r_, iso_l(jl - timedelta(days=tage_, hours=1)), f"Betreff {i}"))
    # Einzeldateien mit höchstens 3000 geänderten Zeilen, damit sie als Code zählen (db.EINZEL_MAX).
    j = 0
    while plus_ or minus_:
        p_, m_ = min(1500, plus_), min(1500, minus_)
        c.execute("INSERT INTO commit_dateien (sha, pfad, plus, minus) VALUES (?, ?, ?, ?)", (sha, f"src/d{j}.py", p_, m_))
        plus_, minus_, j = plus_ - p_, minus_ - m_, j + 1
c.execute("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc) VALUES ('fernwerk-studio/beta', 'feature/lang', 2, 0, ?)", (iso_l(jl - timedelta(days=4)),))
c.commit(); c.close()
with db.verbinden() as c2:
    plus_w, minus_w = c2.execute("SELECT SUM(plus), SUM(minus) FROM commit_dateien d JOIN commits m ON m.sha = d.sha WHERE m.zeitstempel_utc >= ?",
                                 (iso_l(datetime(2026, 9, 20, tzinfo=web.TZ)),)).fetchone()   # letzte 7 Tage ab Sonntag 20.09. (30.09.2026)
assert (plus_w, minus_w) == (45000 + 997 + 8254, 11000 + 300 + 2059), (plus_w, minus_w)
sl = web.seite(jetzt=jl)
assert sl.count("blockfehler") == 0, re.findall(r'data-block="[^"]*"', sl)

# (1) Ring um ein Drittel grösser (84 auf 112 px) im HTML und im Stylesheet, die Legende zieht mit; Lagesatz 32 px über der Reihe.
assert 'class="ring" viewBox="0 0 42 42" width="112" height="112"' in sl and round(112 / 84, 3) == 1.333
assert "width: 112px; height: 112px;" in " ".join(regeln[".ring"]) and "font-size: .9rem" in " ".join(regeln[".rlegende"]), (regeln[".ring"], regeln[".rlegende"])
assert "gap: var(--lage-abstand)" in " ".join(regeln[".lage"]) and "--lage-abstand: var(--s8);" in (web.WEB / "tokens.css").read_text()

# (2) Vergleichswert in Akzentfarbe, Beschriftung und Prozent gedämpft: der Wert im eigenen Element, das Prozent ausserhalb.
vgl_l = re.findall(r'<span class="delta">([^<]*): <span class="vwert">([^<]*)</span>([^<]*)</span>', sl)
assert [b for b, _, _ in vgl_l] == ["gestern", "7 Tage davor", "Vorwoche zum selben Zeitpunkt", "30 Tage davor", "Vormonat zum selben Zeitpunkt"] and all(p == "" or p.startswith(", ") for _, _, p in vgl_l), vgl_l
assert regeln[".tile.kpi .delta .vwert"] == [" color: var(--eis); "] and "color: var(--lbl)" in " ".join(regeln[".tile.kpi .delta"])

# (3) Serie und Repos untereinander in Kopfspalte 3: eine Spalte, zuerst die Serie, dann die Repos, jede gebaut wie Spalte 2 (.n mit b und span).
paar_l = sl[sl.index('<div class="npaar">'):sl.index('<div class="tiles3">')]
assert re.match(r'<div class="npaar"><div class="n"><b>[^<]+</b><span>[^<]+</span></div><div class="n repos-spalte"><b>2</b><span>Repos, 2 diese Woche aktiv</span></div></div>', paar_l), paar_l[:300]
assert regeln[".npaar"] == [" display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s5); align-content: start; min-width: 0; ", " padding-inline-start: var(--s5); "], regeln[".npaar"]

# (4) Jede Kachel in zwei Gruppen: .wert mit Zahl, Titel, Vergleichszeile, dann .kmeta mit den Metazeilen; gleicher Aufbau in allen drei.
# Seit 30.09.2026 je Fenster der Umschalter eine Gruppe: Heute, 7 Tage, Woche, 30 Tage, Monat (Vormonat seit 04.10.2026 entfallen); der Umschalter seit 01.10.2026 direkt nach der Zahl.
kacheln_l = re.findall(r'<div class="wert(?: mit-nav)?"><span class="big mid">[^<]*</span>(?:<nav class="seg klein umschalter".*?</nav>)?<span class="lbl">[^<]*</span>'
                       r'<span class="delta">(?:(?!</div>).)*</div><div class="kmeta">(.*?)</div>', sl, re.S)
assert len(kacheln_l) == 5, kacheln_l
assert kacheln_l[0] == '<span class="delta bezug heute-kopf">Heute in 1 Repo</span>', kacheln_l[0]
assert kacheln_l[1].startswith('<span class="delta bezug paare" title="an 3 von 7 Tagen, in 2 Repos">') and '<span class="delta bezug zeilen" title="7 Tage davor: +10 / −1 Zeilen' in kacheln_l[1] \
    and kacheln_l[3].startswith('<span class="delta bezug paare" title="an '), kacheln_l
# Codezeile «+N / −M Zeilen»: Plus im Akzent, Minus gedämpft, echtes Minuszeichen, die Summe aus eigenem SQL.
zl_l = re.search(r'<span class="delta bezug zeilen" title="[^"]*"><span class="zplus">\+([\d’]+)</span> / <span class="zminus">−([\d’]+)</span> Zeilen</span>', sl)
assert zl_l and (int(zl_l.group(1).replace("’", "")), int(zl_l.group(2).replace("’", ""))) == (plus_w, minus_w), (zl_l and zl_l.groups(), plus_w, minus_w)
assert regeln[".zplus"] == [" color: var(--eis); "] and regeln[".zminus"] == [" color: var(--lbl); "] and "--glut" not in " ".join(regeln[".zplus"] + regeln[".zminus"])
assert "min-height: calc(2 * .75rem * 1.45)" in " ".join(regeln[".kmeta"]) and "margin-top: var(--s2)" in " ".join(regeln[".kmeta"]), regeln[".kmeta"]
assert web.zeilen_text(1234, 56) == "+1’234 / −56 Zeilen" and "dazu" not in sl and "entfernt" not in sl
with gilt_nb("en"), web.fmt.gilt(web.fmt.waehlen("en")):   # Oberflächensprache und Format-Locale sind getrennt (26.09.2026)
    assert web.zeilen_text(1234, 56) == "+1,234 / −56 lines"
# Die zwei Platzwerte der Heute-Kachel sind am 26.09.2026 neu gemessen; der Kommentar an der Stelle nennt das Datum.
assert "26.09.2026" in quelle_web[quelle_web.index("# Platz der Heute-Kachel"):quelle_web.index("HEUTE_PLATZ_PX, HEUTE_ZEILE_PX")]

# (5) Aufklappdetail: ein Raster aus Beschriftung und Wert, keine Aufzählungspunkte, nichts doppelt (keine Datumsspanne, kein «letzter
# Commit»), Datum der Commitliste kurz; der Serverstand als eigene Zeile nur bei einem Dienst.
det_l = zeile_von(sl, "fernwerk-studio/beta")[zeile_von(sl, "fernwerk-studio/beta").index("</summary>"):]
felder_l = re.findall(r"<dt>([^<]*)</dt><dd>(.*?)</dd>", det_l)
# Seit 02.10.2026 steht jeder Befund als eigenes Paar, hier der wartende Zweig mit Stand und Alter, vor dem Standardzweig.
assert [f for f, _ in felder_l] == ["Woche", "Zeilen", "Sprache", "Zweige", "Zweig"] and felder_l[2][1] == "HTML" and felder_l[4][1] == '<span translate="no" title="main">main</span>', felder_l
assert felder_l[3][1] == '<span translate="no">feature/lang</span> 2 Commits vor main <span class="hbrest">seit 4 Tagen</span>', felder_l[3]
assert re.fullmatch(r'<span title="Vorwoche zum selben Zeitpunkt: [^"]*"><span class="zplus">\+8’254</span> / <span class="zminus">−2’059</span></span>', felder_l[1][1]), felder_l[1]
assert 'class="chip' not in det_l and "letzter Commit" not in det_l and " bis " not in det_l[det_l.index("<dl"):det_l.index("</dl>")], det_l[:500]
assert re.findall(r'<li><span class="datum">([^<]*)</span>', det_l)[:1] == [f"{(jl - timedelta(days=2, hours=1)):%d.%m.}"], det_l
assert "color: var(--lbl)" in " ".join(regeln[".pmeta dt"]) and "color: var(--txt)" in " ".join(regeln[".pmeta dd"])
mit_server = web.projekt_stand({"sprache": "Go", "branch": "main", "server": {"tag": "64f2784"}, "letzte": [], "warnliste": []}, 87, 9)
# (26.09.2026) Paare nebeneinander, je ein .ppaar; die Vorwoche kurz wie in der Projektzeile, die volle Beschriftung im title.
assert '<div class="ppaar"><dt>Server</dt><dd><span translate="no">64f2784</span></dd></div><div class="ppaar zweig"><dt>Zweig</dt>' in mit_server, mit_server
assert ('<div class="ppaar"><dt>Woche</dt><dd><span title="Vorwoche zum selben Zeitpunkt 9">87 (Vorwoche 9)</span></dd></div>') in mit_server, mit_server
assert mit_server.count('<div class="ppaar') == 5 and mit_server.rindex('class="ppaar zweig"') > mit_server.rindex('<dt>Server</dt>') and "zum selben Zeitpunkt" not in re.sub(r'title="[^"]*"', "", mit_server), mit_server
ohne_server = web.projekt_stand({"sprache": "Go", "branch": "main", "server": None, "letzte": [], "warnliste": []}, 87, 9)
assert ohne_server.count('<div class="ppaar') == 4 and "Server" not in ohne_server, "Server nur bei Diensten"
assert regeln[".pmeta"][0].count("display: flex; flex-wrap: wrap;") == 1 and "white-space: nowrap" in " ".join(regeln[".ppaar"]) and "grid" not in " ".join(regeln[".pmeta"])
# Metazeile der Wochen- und der 30-Tage-Kachel (26.09.2026): zwei kurze Paare, Werte in Textfarbe, der volle Satz im title, einzeilig.
paare_l = re.findall(r'<span class="delta bezug paare" title="([^"]*)">(.*?)</span>(?:<span class="delta bezug zeilen"|</div>)', sl)
assert paare_l == [("an 3 von 7 Tagen, in 2 Repos", '<span class="kwert">3/7</span> aktive Tage · <span class="kwert">2</span> Repos'),
                   ("an 3 von 6 Tagen, in 2 Repos", '<span class="kwert">3/6</span> aktive Tage · <span class="kwert">2</span> Repos'),
                   ("an 4 von 30 Tagen, in 2 Repos", '<span class="kwert">4/30</span> aktive Tage · <span class="kwert">2</span> Repos'),
                   ("an 4 von 26 Tagen, in 2 Repos", '<span class="kwert">4/26</span> aktive Tage · <span class="kwert">2</span> Repos')], paare_l   # Sa 26.09.: Woche 6 Tage, Monat 26
assert "white-space: nowrap" in " ".join(regeln[".tile.kpi .delta.paare"]) and regeln[".tile.kpi .delta .kwert"] == [" color: var(--txt); "]
with gilt_nb("en"), web.fmt.gilt(web.fmt.waehlen("en")):
    assert web.tage_paare(3, 6, 9) == '<span class="kwert">3/6</span> active days · <span class="kwert">9</span> repos' and web.tage_paare(1, 1, 1).endswith("</span> repo")
    assert web.tage_paare(1234, 30, 1) == '<span class="kwert">1,234/30</span> active days · <span class="kwert">1</span> repo', "Zahlen aus dem Formatmodul"

# (6) Handlungsbedarf: seit 09.10.2026 die Arten mit 0 als eine Zeile, je Art mit Befund Titel und Einträge auf dem Kachelraster,
# offen ohne details, jede Zeile mit vollem Text im title.
hb_l = sl[sl.index('<section class="card handlung"'):sl.index('<section class="card projekte">')]
assert hb_l.count('<p class="hbart"') == 1 and hb_l.count('<ul class="hbliste">') == 1 and hb_l.count('<span id="handlung-') == 3 and "<details" not in hb_l, hb_l[:400]
assert ' title="Zweig feature/lang in beta: 2 Commits vor main, seit 4 Tagen">' in hb_l, hb_l
# Die Befundangabe der Projektzeile zielt auf den Eintrag selbst, die Kopfzeile seit 04.10.2026 auf die Titelzeile der Art.
assert sl.count('<a href="#hb-zweige-fernwerk-studio_2f_beta~feature_2f_lang">') == 1 and '<a href="#handlung-zweige">1 Zweig wartet auf main</a>' in sl and '<li id="hb-zweige-fernwerk-studio_2f_beta~feature_2f_lang" title=' in hb_l, "der Sprunglink trifft seinen Eintrag"
assert '<span class="pbefund"><a href="#hb-zweige-fernwerk-studio_2f_beta~feature_2f_lang">Zweig feature/lang +2</a></span>' in summary_von(sl, "fernwerk-studio/beta"), summary_von(sl, "fernwerk-studio/beta")[-300:]

# (6b) Server hinter main (02.10.2026): eigene Art im Handlungsbedarf, aus derselben Kartenmenge wie Kopfzahl, Kopfzeilen und Lagesatz; der
# Eintrag und das Paar «Server» im Aufklappdetail tragen laufenden Stand, Zahl hinter main und die Betreffe aus deploy.json. Nur bildrelevante
# Rückstände sind ein Befund.
# deploy.json je Dienst mit Repo und Zeitpunkt seit 11.10.2026 (Codex-Audit, Befunde 7 und 9)
web.DEPLOY.write_text(json.dumps({"beta": {"repo": "fernwerk-studio/beta", "gemessen": web.iso_utc(jl), "tag": "aaa1111", "origin_main": "bbb2222", "hinter": 2, "bildrelevant": 1,
                                                 "commits": [{"sha": "bbb2222", "betreff": "Zweiter"}, {"sha": "ccc3333", "betreff": "Erster <b>"}]}}))
ss = web.seite(jetzt=jl)
srv = '<span translate="no">aaa1111</span>, 2 hinter main: <span translate="no">bbb2222 Zweiter</span>; <span translate="no">ccc3333 Erster &lt;b&gt;</span>'
hb_s = ss[ss.index('<section class="card handlung"'):ss.index('<section class="card projekte">')]
bk_s = ss[ss.index('<div class="n befunde">'):ss.index('<div class="n"><b>', ss.index('<div class="n befunde">'))]
assert ss.count("blockfehler") == 0 and nb("Zwei Punkte mit Handlungsbedarf.") in ss, ss.count("blockfehler")
assert bk_s.startswith('<div class="n befunde"><b>2</b><div class="bunter"><ul class="barten"><li><a href="#handlung-server">1 Dienst hinter main</a></li>'
                       '<li><a href="#handlung-zweige">1 Zweig wartet auf main</a></li></ul>'), bk_s[:400]
# Seit 03.10.2026 als Unterzeilen: gedämpft «läuft <hash> · N hinter main», darunter je Commit Hash gedämpft und Betreff (höchstens zwei Zeilen,
# voller Text im title); das Aufklappdetail behält die Kette.
assert ('<p class="hbart" id="handlung-server">1 Dienst hinter main</p><ul class="hbliste"><li id="hb-server-fernwerk-studio_2f_beta" title="beta: Server 2 hinter main">'
        '<a class="hbrepo" href="https://github.com/fernwerk-studio/beta" target="_blank" rel="noopener">beta</a>'
        '<span class="hbz"><span class="hbrest">läuft <span class="hzeit" translate="no">aaa1111</span> · 2 hinter main</span></span>'
        '<span class="hbz hbzwei"><span class="hzeit" translate="no">bbb2222</span> <span translate="no" title="Zweiter">Zweiter</span></span>'
        '<span class="hbz hbzwei"><span class="hzeit" translate="no">ccc3333</span> <span translate="no" title="Erster &lt;b&gt;">Erster &lt;b&gt;</span></span></li></ul>') in hb_s, hb_s[:900]
assert hb_s.index('id="handlung-server"') < hb_s.index('id="handlung-zweige"'), "Server vor den Zweigen, Reihenfolge der Arten"
assert '<a href="#hb-server-fernwerk-studio_2f_beta">Server 2 hinter main</a>' in summary_von(ss, "fernwerk-studio/beta") and f"<dt>Server</dt><dd>{srv}</dd>" in zeile_von(ss, "fernwerk-studio/beta")
# (6b') Release-Bild mit neuerem Tag v* (14.10.2026): «neuer» aus deploy.json (tools/deploy-stand.sh seit e74e281). Kurzform, Überschrift,
# Eintrag und Aufklappdetail nennen die neuere Version statt «hinter main», in de und en; die Zeile «v1.0.4 neuere Version verfügbar» aus
# "commits" entfällt, sie wiederholte den Text. Ein Commit-Bild bleibt «hinter main» (oben).
web.DEPLOY.write_text(json.dumps({"beta": {"repo": "fernwerk-studio/beta", "gemessen": web.iso_utc(jl), "tag": "1.0.3", "origin_main": "ddd4444", "branch": "v1.0.4",
                                           "hinter": 1, "bildrelevant": 1, "gleich": False, "neuer": "1.0.4", "commits": [{"sha": "v1.0.4", "betreff": "neuere Version verfügbar"}]}}))
for sprache_, kurz_, art_, ps_ in (("de", "Server 1 mit neuerer Version v1.0.4", "1 Dienst mit neuerer Version v1.0.4", "neuere Version v1.0.4 verfügbar"),
                                   ("en", "Server 1 with newer version v1.0.4", "1 service with newer version v1.0.4", "newer version v1.0.4 available")):
    sr = web.seite(jetzt=jl, sprache=sprache_)
    hb_r = sr[sr.index('<section class="card handlung"'):sr.index('<section class="card projekte">')]
    assert sr.count("blockfehler") == 0 and "hinter main" not in hb_r and "behind main" not in hb_r and "neuere Version verfügbar</span>" not in sr, (sprache_, hb_r[:900])
    assert f'<li><a href="#handlung-server">{art_}</a></li>' in sr and f'<p class="hbart" id="handlung-server">{art_}</p>' in hb_r, (sprache_, hb_r[:900])
    assert (f'<li id="hb-server-fernwerk-studio_2f_beta" title="beta: {kurz_}">' in hb_r and f'<span class="hzeit" translate="no">1.0.3</span> · {ps_}</span></span></li>' in hb_r
            and "hbzwei" not in hb_r.split('id="hb-server-')[1].split("</li>")[0]), (sprache_, hb_r[:900])
    assert f'<a href="#hb-server-fernwerk-studio_2f_beta">{kurz_}</a>' in summary_von(sr, "fernwerk-studio/beta"), summary_von(sr, "fernwerk-studio/beta")[-300:]
    assert f'<dd><span translate="no">1.0.3</span>, {ps_}</dd>' in zeile_von(sr, "fernwerk-studio/beta"), zeile_von(sr, "fernwerk-studio/beta")[-600:]
# Lagesatz und Überschrift je Fall aus art_satz: nur Release, Release und Commit-Bild gemischt, nur Commit-Bild (bleibt «hinter main»).
rel_k, com_k = {"server_befund": True, "server": {"neuer": "1.0.4"}, "branch": "main"}, {"server_befund": True, "server": {}, "branch": "main"}
for sprache_, erwartet in (("de", ("Für einen Dienst ist die neuere Version v1.0.4 verfügbar.", "Zwei Dienste laufen hinter main oder haben eine neuere Version (v1.0.4).",
                                   "Ein Dienst läuft hinter main.", "Dienste hinter main oder mit neuerer Version v1.0.4")),
                           ("en", ("Newer version v1.0.4 is available for one service.", "Two services are behind main or have a newer version (v1.0.4).",
                                   "One service is behind main.", "services behind main or with newer version v1.0.4"))):
    with sprache.gilt(sprache_):
        assert (web.art_satz("server", "lage.server", 1, [rel_k]), web.art_satz("server", "lage.server", 2, [rel_k, com_k]), web.art_satz("server", "lage.server", 1, [com_k]),
                web.art_satz("server", "bf.server", 2, [rel_k, com_k])) == erwartet, sprache_
web.DEPLOY.write_text(json.dumps({"beta": {"repo": "fernwerk-studio/beta", "gemessen": web.iso_utc(jl), "tag": "aaa1111", "origin_main": "bbb2222", "hinter": 2, "bildrelevant": 0, "commits": []}}))
ss0 = web.seite(jetzt=jl)
assert '<span id="handlung-server">0 Dienste hinter main</span></p>' in ss0 and "hb-server" not in ss0 and '<dt>Server</dt><dd><span translate="no">aaa1111</span></dd>' in zeile_von(ss0, "fernwerk-studio/beta"), "ohne bildrelevanten Rückstand kein Befund"
web.DEPLOY.unlink()

# (6c) Messvertrag (03.10.2026, Codex-Sammellauf): für ein Repo mit Dienst ist ein fehlender, unlesbarer oder gescheiterter Deploy-Stand
# «nicht gemessen», ein Befund der Art «messung», nie «kein Befund». Ein gescheiterter Messlauf behält den letzten Stand samt Befund.
server_vorher = web.SERVER.read_text() if web.SERVER.exists() else None
mit_beta = json.loads(json.dumps(beispiel)); mit_beta["dienste"][2]["repo"] = "fernwerk-studio/beta"; mit_beta["erhoben_utc"] = web.iso_utc(jl)
web.SERVER.write_text(json.dumps(mit_beta))
for inhalt, grund in (("{", "deploy.json unlesbar"), ("[1, 2]", "deploy.json unlesbar"), (None, "nicht gemessen"), ("{}", "nicht gemessen")):
    web.DEPLOY.unlink(missing_ok=True)
    if inhalt is not None:
        web.DEPLOY.write_text(inhalt)
    sd = web.seite(jetzt=jl)
    kopf_d = sd[sd.index('<div class="n befunde">'):sd.index('<div class="tiles3">')]
    assert sd.count("blockfehler") == 0 and "Kein Handlungsbedarf" not in kopf_d and 'href="#handlung-messung"' in kopf_d and 'id="hb-messung-fernwerk-studio_2f_beta"' in sd, (inhalt, kopf_d[:400])
    assert f'<span class="hbz hbzwei">Stand des Servers: <span translate="no">{grund}</span> <span class="hbrest">noch nie gemessen</span></span>' in sd, (inhalt, sd[sd.index('id="hb-messung-fernwerk-studio_2f_beta"'):][:500])
web.DEPLOY.write_text(json.dumps({"beta": {"repo": "fernwerk-studio/beta", "tag": "aaa1111", "origin_main": "bbb2222", "hinter": 2, "bildrelevant": 1, "commits": [],
                                                 "gemessen": "2026-09-23T10:00:00+02:00", "fehler": "git fetch in /srv/gitpuls gescheitert"}}))
sd = web.seite(jetzt=jl)
eintrag_d = sd[sd.index('id="hb-messung-fernwerk-studio_2f_beta"'):].split("</li>")[0]
assert 'id="hb-server-fernwerk-studio_2f_beta"' in sd and "Stand des Servers: <span translate=\"no\">git fetch in /srv/gitpuls gescheitert</span> <span class=\"hbrest\">letzter Erfolg " in eintrag_d, eintrag_d
assert '<span class="glut">nicht gemessen (git fetch in /srv/gitpuls gescheitert), zuletzt: Container auf aaa1111, 2 Commits hinter Default-Branch' in web.server_dienste_block(mit_beta | {"dienste": [mit_beta["dienste"][2] | {"deploy": json.loads(web.DEPLOY.read_text())["beta"]}]}, jl), "Serverzone"
web.DEPLOY.write_text(json.dumps({"beta": {"repo": "fernwerk-studio/beta", "fehler": "Container gitpuls-gitpuls-1 nicht gefunden"}}))
sd = web.seite(jetzt=jl)
assert sd.count("blockfehler") == 0 and 'id="hb-server-fernwerk-studio_2f_beta"' not in sd and "Container gitpuls-gitpuls-1 nicht gefunden" in sd, "Eintrag nur mit Fehler: kein Stand, kein KeyError"
web.DEPLOY.unlink()
web.SERVER.write_text(server_vorher) if server_vorher is not None else web.SERVER.unlink()

# (6d) Gestoppter Timer (03.10.2026, Codex-Audit): timer_aktiv aus der Timer-Unit, getrennt vom Service. Ein nicht aktiver Timer ist eine
# Abweichung im Lagesatz und in der Dienstzeile in Warnfarbe, auch wenn sein letzter Lauf sauber war; ohne den Schlüssel (ältere Datei) aktiv.
st = json.loads(json.dumps(beispiel)); st["erhoben_utc"] = web.iso_utc(jl)
unit_ = st["dienste"][2]["timer"][0]["unit"]
assert not [a_ for a_ in web.server_lage(st)[1] if "nicht aktiv" in a_], "ohne Schlüssel gilt der Timer als aktiv"
st["dienste"][2]["timer"][0]["timer_aktiv"] = "inactive"
satz_t, ab_t = web.server_lage(st)
assert f"gitpuls: Timer {unit_} nicht aktiv" in ab_t and satz_t.startswith(f"gitpuls: Timer {unit_} nicht aktiv."), (satz_t, ab_t)
zeile_t = web.server_dienste_block(st, jl).split('<details class="dienst">')[3]
assert '<i class="punkt glut"' in zeile_t and f'<span class="aussage"><span class="glut">Timer {unit_} nicht aktiv</span></span>' in zeile_t, zeile_t[:600]
assert zeile_t.count(f'<span class="glut">Timer {unit_} nicht aktiv</span>') == 2, "auch im Detail anstelle des nächsten Termins"
with sprache.gilt("en"):
    assert f"gitpuls: timer {unit_} not active" in web.server_lage(st)[1]

# (6e) Firewall-Familie nicht gemessen (03.10.2026): eine Abweichung in Lagesatz und Fussblock, nie still weg.
fw_n = json.loads(json.dumps(beispiel))
fw_n["firewall"]["nicht_gemessen"] = ["IPv6"]; del fw_n["firewall"]["familien"]["IPv6"]
assert web.firewall_befunde(fw_n["firewall"]) == ["Firewall IPv6: nicht gemessen"] and "Firewall IPv6: nicht gemessen" in web.server_lage(fw_n)[0]
assert '<div class="mz"><span class="mk">IPv6</span><span><span class="glut">nicht gemessen</span></span></div>' in web.fuss_firewall(fw_n), web.fuss_firewall(fw_n)

# (7) Fusszeile: Copyright mit Jahr und Inhaber, Stand der GitHub-Daten, Serverstand nur mit server.json, Version nur mit GITPULS_TAG;
# in beiden Sprachen, ein eigener Block.
web.SERVER.write_text(json.dumps(beispiel | {"erhoben_utc": "2026-09-26T12:55:00Z"}))
alt_version, web.VERSION = web.VERSION, "401f3a0"
try:
    fuss_de, fuss_en = (re.search(r'<footer class="fusszeile">(.*?)</footer>', web.seite(jetzt=jl, sprache=s_)).group(1) for s_ in ("de", "en"))
    web.SERVER.unlink()
    fuss_ohne = re.search(r'<footer class="fusszeile">(.*?)</footer>', web.seite(jetzt=jl)).group(1)
finally:
    web.VERSION = alt_version
# (1) Copyright seit 26.09.2026 «© <Jahr> gitpuls.com», seit 14.10.2026 mit Link auf https://gitpuls.com, in beiden Sprachen; Zeitpunkte nach der Format-Locale: de-CH (GITPULS_FORMAT
# der Tests) mit dem CLDR-Muster «Datum, Zeit», Englisch ohne Accept-Language en-US mit 12-Stunden-Zeit.
assert fuss_de == ('<span>© 2026 <a href="https://gitpuls.com" target="_blank" rel="noopener">gitpuls.com</a></span>'
                   "<span>GitHub-Daten vom 26.09.2026,\u00a014:52 Uhr</span><span>Serverstand vom 26.09.2026,\u00a014:55 Uhr</span>"
                   "<span>Version 401f3a0</span>"), fuss_de
assert fuss_en == ('<span>© 2026 <a href="https://gitpuls.com" target="_blank" rel="noopener">gitpuls.com</a></span>'
                   "<span>GitHub data from Sep\u00a026,\u00a02026,\u00a02:52\u202fPM</span>"
                   "<span>Server status from Sep\u00a026,\u00a02026,\u00a02:55\u202fPM</span><span>Version 401f3a0</span>"), fuss_en
assert all(f_.count("<a ") == 1 and f_.count('<a href="https://gitpuls.com" target="_blank" rel="noopener">gitpuls.com</a>') == 1 for f_ in (fuss_de, fuss_en)), "genau ein Link, der auf gitpuls.com"
assert "Serverstand" not in fuss_ohne and fuss_ohne.endswith("<span>Version 401f3a0</span>") and fuss_ohne.startswith('<span>© 2026 <a href="https://gitpuls.com" target="_blank" rel="noopener">gitpuls.com</a></span>'), fuss_ohne
assert alt_version == "" and "Version" not in re.search(r'<footer class="fusszeile">(.*?)</footer>', web.seite(jetzt=jl)).group(1), "ohne GITPULS_TAG keine Version"
assert "GITPULS_TAG: ${GITPULS_TAG:-unbekannt}" in (web.WEB.parent.parent / "compose.example.yaml").read_text(), "compose.yaml reicht die Version durch"
assert all(ch not in fuss_de + fuss_en for ch in "–—"), "keine Gedankenstriche"
db.DB = haupt_l
web.SERVER.write_text(json.dumps(beispiel))
print("ok: Ring 112 px, Lagesatz 32 px, Vergleichswert im Akzent, Serie und Repos untereinander, Kacheln in zwei Gruppen mit Codezeile +/−,"
      " Raster im Aufklappdetail, Handlungsbedarf in Spalten, Fusszeile de und en")

# Ringlegende (26.09.2026, Korrektur): Ring und Legende als Gruppe links, so breit wie ihr Inhalt. Die Gruppe endet mindestens --s4 vor der
# Spaltenkante, dazu der Spaltenabstand --s4 der Kopfreihe: mindestens 32 px vom Ende der Prozentspalte bis zur Spalte Handlungsbedarf
# (gemessen in Chrome bei 1280 px: 120.20 px de, 130.08 px en, mit einem Namen von 40 Zeichen 32.00 px). Die Namensspalte nimmt nur die
# Breite ihres längsten Namens, gestreckt wird nichts.
regeln_rl = {}
for sel, inhalt in re.findall(r"([^{}]+)\{([^}]*)\}", (web.WEB / "style.css").read_text()):
    for s_ in sel.split(","):
        regeln_rl.setdefault(s_.strip().split("*/")[-1].strip(), []).append(inhalt)
rb_ = " ".join(regeln_rl[".ringbox"])
rl_ = " ".join(regeln_rl[".rlegende"])
assert "justify-self: start" in rb_ and "max-width: 100%" in rb_, rb_
# Seit 02.10.2026 mittig in der Spalte: Spur so breit wie die Gruppe, zentriert, Innenrand --s4 beidseits (16 + 16 px Spaltenabstand = 32 px).
assert regeln_rl[".n.ring-spalte"] == [" grid-template-columns: minmax(0, max-content); justify-content: center; padding-inline: var(--s4); ", " justify-items: center; "], regeln_rl[".n.ring-spalte"]
# Zwischen 821 und 1099 px (02.10.2026) die Legende unter dem Ring, beide mittig, mit dem Abstand aus .n.
css_rl = (web.WEB / "style.css").read_text()
assert "@media (min-width: 821px) and (max-width: 1099px) {\n  .ringbox { flex-direction: column; gap: .75rem; justify-self: center; }\n  .n.ring-spalte { justify-items: center; }\n}" in css_rl
assert "grid-template-columns: .8rem minmax(0, max-content) auto auto" in rl_ and "flex: 0 1 auto" in rl_ and "1fr" not in rl_ and "18rem" not in rl_, rl_
assert ".tiles3, .nums, .hraster { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--s4); }" in (web.WEB / "style.css").read_text()
assert "--s4: 1rem" in (web.WEB / "tokens.css").read_text(), "16 px Rand in der Spalte plus 16 px Spaltenabstand sind 32 px"
# Umschalter und Zahl (02.10.2026): 1100 bis 1199 px schmale Knöpfe wie unter 480 px, 821 bis 1099 px der Umschalter auf eigener Zeile
# unter der Vergleichszeile (seit dem Nachtrag); gemessen mit «8’888» in Chrome, Luft mindestens --s3.
assert "@media (max-width: 480px), (min-width: 1100px) and (max-width: 1199px) { .seg.klein button { padding: .15rem .3rem; } }" in css_rl
# Seit dem Nachtrag zu 3822015: dort unter der Vergleichszeile, linksbündig, damit die Zahlen der drei Kacheln auf einer Höhe stehen.
assert ("@media (min-width: 821px) and (max-width: 1099px) {\n  .tile .wert.mit-nav > .umschalter { grid-column: 1 / -1; grid-row: 4; justify-self: start; margin-top: var(--s3); }\n}") in css_rl
print("ok: Ringlegende so breit wie ihr Inhalt, mindestens 32 px Luft zur Spalte Handlungsbedarf")

# Ring und Legende (26.09.2026, zweiter Lauf): Ringkante zum ersten Farbfeld 40 px (2.5rem, vorher 20 px), Name zu Zahl 16 px (Spaltenabstand
# --s2 plus 8 px am Namen, vorher 8 px). Gemessen in Chrome, siehe README.
assert "gap: 2.5rem" in rb_ and "gap: var(--s5)" not in rb_, rb_
assert regeln_rl[".rlegende .rname"] == [" margin-right: var(--s2); "] and "column-gap: var(--s2)" in rl_ and "--s2: .5rem" in (web.WEB / "tokens.css").read_text(), regeln_rl.get(".rlegende .rname")
# Warnungseinträge im Block Handlungsbedarf aufklappbar, der Zustand in ?handlung= (warn:<repo>), das Skript schreibt ihn mit.
assert "document.querySelectorAll('#handlung details.hbeintrag').forEach(function(e){if(e.open)z.push(e.dataset.zeichen);})" in web.SKRIPT
assert web.handlung_zeichen("warn:fernwerk-studio/a,ci-alle,warn:x,<b>") == {"warn:fernwerk-studio/a"}
print("ok: Ring 40 px zur Legende, Name zu Zahl 16 px, Lagesatz in beiden Zonen aus einer Variable, Warnungseinträge aufklappbar")

# Kästen der Serverzone (v1.0.3): eine gemeinsame Beschriftungsspalte für Maschine, Installiert, Firewall und Updates, damit die Werte in allen
# vier Kästen an derselben Stelle beginnen (v1.0.1 hatte eine Spalte je Kasten). 9rem = 144 px; das längste Label der Texttabellen in de und
# en, «Automatische Updates», misst in Chrome 138.3 px in San Francisco und 131.3 px in Helvetica und Arial. Kein Label ist breiter als die
# Spalte: eines aus den Texttabellen ist höchstens so lang wie «Automatische Updates», eines aus den Messdaten bricht in der Spalte um. Die
# Regel trägt nur, wenn jede .mz direktes Kind der .meta ist.
from html.parser import HTMLParser  # noqa: E402
assert regeln_rl.get(".meta > .mz") == [" grid-template-columns: 9rem minmax(0, 1fr); "], regeln_rl.get(".meta > .mz")
assert regeln_rl.get(".meta > .mz > .mk") == [" white-space: normal; overflow-wrap: anywhere; "], regeln_rl.get(".meta > .mz > .mk")
assert "subgrid" not in " ".join(regeln_rl[".meta"]) and ".meta > *" not in regeln_rl, "keine Spalte je Kasten mehr"
assert "--s2: .5rem" in (web.WEB / "tokens.css").read_text() and "gap: var(--s2)" in " ".join(regeln_rl[".mz"]), "Abstand zum Wert wie vor v1.0.1"


class Eltern(HTMLParser):
    """Sammelt zu jeder .mz der Fusszeile die Klasse ihres Elternelements und den Text ihres Labels."""
    LEER = {"br", "img", "input", "meta", "link", "hr", "source", "wbr"}

    def __init__(self):
        super().__init__()
        self.stapel, self.eltern, self.labels = [], [], []

    def handle_starttag(self, tag, attrs):
        klasse = dict(attrs).get("class") or ""
        if tag == "div" and klasse.split()[:1] == ["mz"] and any(k.startswith("fuss") for k in self.stapel):
            self.eltern.append(self.stapel[-1])
            self.labels.append("")
        if tag not in self.LEER:
            self.stapel.append(klasse)

    def handle_data(self, data):
        if self.stapel and self.stapel[-1] == "mk" and self.labels and any(k.startswith("fuss") for k in self.stapel):
            self.labels[-1] += data

    def handle_endtag(self, tag):
        if tag not in self.LEER and self.stapel:
            self.stapel.pop()


beispiel["erhoben_utc"] = web.iso_utc(jetzt)
web.SERVER.write_text(json.dumps(beispiel))
quellen_ = set((beispiel["zustand"].get("pakete_quellen") or {}))   # Labels aus den Messdaten
for sp_ in ("de", "en"):
    ep_ = Eltern()
    ep_.feed(seite(sprache=sp_))
    assert len(ep_.eltern) >= 20 and set(ep_.eltern) == {"meta"}, ep_.eltern
    texte_ = [l for l in ep_.labels if l not in quellen_]
    assert max(map(len, texte_)) <= len("Automatische Updates"), max(texte_, key=len)
print(f"ok: gemeinsame Labelspalte 9rem für alle vier Serverkästen, {len(ep_.eltern)} Zeilen direkt in .meta, kein Label breiter")

# Version aus dem Bild (11.10.2026, v1.0.2): mit .env aus .env.example ist GITPULS_TAG leer, compose.yaml reicht dann «unbekannt» herein, und
# die Fusszeile des Schnellstarts zeigte «Version unbekannt». Ein Release-Bild trägt die Version selbst (GITPULS_VERSION aus dem
# Build-Argument von release.yml); ein Bau ohne Argument zeigt wie bisher GITPULS_TAG, den Commit.
import subprocess  # noqa: E402
import sys  # noqa: E402


def version_mit(**umgebung):
    env = {k: v for k, v in os.environ.items() if k not in ("GITPULS_VERSION", "GITPULS_TAG")} | umgebung
    return subprocess.run([sys.executable, "-c", "from app import web; print(web.VERSION)"], cwd=web.WEB.parent.parent, env=env,
                          capture_output=True, text=True, check=True).stdout.strip()


assert version_mit(GITPULS_VERSION="1.0.2", GITPULS_TAG="") == "1.0.2", "leerer GITPULS_TAG: die Version aus dem Bild"
assert version_mit(GITPULS_VERSION="1.0.2", GITPULS_TAG="unbekannt") == "1.0.2", "compose.yaml reicht bei leerer .env «unbekannt» herein"
assert version_mit(GITPULS_VERSION="", GITPULS_TAG="cd075e4") == "cd075e4", "Bau aus dem Checkout ohne Argument: der Commit wie bisher"
assert version_mit() == "", "ohne beides keine Version"
dockerfile_ = (web.WEB.parent.parent / "Dockerfile").read_text()
assert 'ARG GITPULS_VERSION=""' in dockerfile_ and "ENV GITPULS_VERSION=$GITPULS_VERSION" in dockerfile_ \
    and 'org.opencontainers.image.version="$GITPULS_VERSION"' in dockerfile_, "Dockerfile: Build-Argument, Umgebung und Label"
assert "build-args: GITPULS_VERSION=${{ steps.meta.outputs.version }}" in (web.WEB.parent.parent / ".github" / "workflows" / "release.yml").read_text()
print("ok: Version aus dem Bild vor GITPULS_TAG, ohne Build-Argument der Commit")
