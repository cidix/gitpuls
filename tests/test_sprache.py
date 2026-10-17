"""Zweisprachige Oberfläche (25.09.2026): gleiche Schlüssel in beiden Tabellen, jeder benutzte Schlüssel vorhanden, Rückfall auf Englisch
mit Logzeile, Singular und Plural für 0, 1 und 2, Formate je Sprache gegen feste Erwartungen, Vorgabe en, «fr» bricht den Start ab,
?lang übersteuert und behält die übrigen Parameter in beide Richtungen, die Laufphase im Knopf folgt der Sprache.
python3 -m tests.test_sprache"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

os.environ["GITPULS_DB"] = os.path.join(tempfile.mkdtemp(), "sprache.db")
os.environ.setdefault("GITPULS_ADRESSEN", "dev@fernwerk-studio.example")
os.environ.setdefault("GITPULS_SPRACHE", "de")
os.environ["GITPULS_FORMAT"] = "de-CH"   # Schreibweisen wie die eigene Instanz (26.09.2026); die übrigen Locales prüft tests/test_format.py
os.environ["TZ"] = "Europe/Zurich"   # die Zone setzt der Test selbst (10.10.2026, Audit S28): grün unter jeder TZ der Shell
from app import db, sprache, web  # noqa: E402
from app.sprache import TABELLEN, gilt, t  # noqa: E402
from app.texte import de, en  # noqa: E402

WURZEL = Path(__file__).resolve().parent.parent

# Gleiche Schlüssel, und jeder Schlüssel, den web.py nennt, steht in beiden Tabellen; keiner steht ungenutzt da.
assert set(de.T) == set(en.T), sorted(set(de.T) ^ set(en.T))
quelle = (WURZEL / "app" / "web.py").read_text()
# Genannt ist ein Schlüssel als Zeichenkette in web.py (direkt in t() oder über eine Variable), dazu die zusammengesetzten rb.* und js.*.
genannt = {k for k in de.T if f'"{k}"' in quelle or f"'{k}'" in quelle} | set(re.findall(r'\bt\(\s*["\']([\w.]+)["\']', quelle)) \
    | {f"rb.{s}" for s in web.RUECKBLICK} | {f"js.{k}" for k in ("laeuft", "sperre", "fehler", "startet", "kein_lauf", "gescheitert")}
assert genannt <= set(de.T), sorted(genannt - set(de.T))
assert set(de.T) <= genannt, ("ungenutzte Schlüssel", sorted(set(de.T) - genannt))

# Rückfall: fehlt ein Schlüssel in de, gilt der englische Text, und eine Zeile geht ins Log.
weg = TABELLEN["de"].pop("heute.zuletzt")
try:
    log = io.StringIO()
    with contextlib.redirect_stdout(log), gilt("de"):
        assert t("heute.zuletzt") == "Latest"
    assert "'heute.zuletzt' fehlt in der Tabelle de" in log.getvalue(), log.getvalue()
finally:
    TABELLEN["de"]["heute.zuletzt"] = weg


def beide(schluessel, *args):
    aus = []
    for s in ("de", "en"):
        with gilt(s):
            aus.append(t(schluessel, *args))
    return tuple(aus)


# (c) Singular und Plural für 0, 1 und 2: Serie, Zweige, Repos, Tage, Commits. Erwartungen fest, nicht aus den Tabellen.
erwartet = {
    ("rek.serie_wert", None, None): [("0 Tage", "0 days"), ("1 Tag", "1 day"), ("2 Tage", "2 days")],
    ("serie.pause",): [("Tage ohne Commit", "days without a commit"), ("Tag ohne Commit", "day without a commit"), ("Tage ohne Commit", "days without a commit")],
    ("bf.zweige", "main"): [("Zweige warten auf main", "branches waiting on main"), ("Zweig wartet auf main", "branch waiting on main"),
                     ("Zweige warten auf main", "branches waiting on main")],
    ("zweig.stand", 0, "main"): [("0 Commits vor main", "0 commits ahead of main"), ("1 Commit vor main", "1 commit ahead of main"),
                                 ("2 Commits vor main", "2 commits ahead of main")],

    ("rb.tage",): [("0 Tage", "0 days"), ("1 Tag", "1 day"), ("2 Tage", "2 days")],
    ("seit",): [("seit heute", "since today"), ("seit 1 Tag", "for 1 day"), ("seit 2 Tagen", "for 2 days")],
    ("rb.ohne_eltern",): [("0 Commits ohne Elternzahl, als Einzelcommit gezählt", "0 commits without a parent count, counted as single commits"),
                          ("1 Commit ohne Elternzahl, als Einzelcommit gezählt", "1 commit without a parent count, counted as single commits"),
                          ("2 Commits ohne Elternzahl, als Einzelcommit gezählt", "2 commits without a parent count, counted as single commits")],
    ("kachel.an_tagen", 0, 0): [("an 0 von 0 Tagen, in 0 Repos", "on 0 of 0 days, in 0 repos")],
}
for (schluessel, *rest), soll in erwartet.items():
    for n, s in enumerate(soll):
        args = (n, *rest) if schluessel != "kachel.an_tagen" else (0, 0, 0)
        assert beide(schluessel, *args) == s, (schluessel, n, beide(schluessel, *args), s)
assert [beide("kachel.an_tagen", 1, n, n) for n in (1, 2)] == [("an 1 von 1 Tag, in 1 Repo", "on 1 of 1 day, in 1 repo"),
                                                               ("an 1 von 2 Tagen, in 2 Repos", "on 1 of 2 days, in 2 repos")]
assert [beide("lage.zweige", n, "main") for n in (1, 2)] == [("Ein Zweig wartet auf main.", "One branch is waiting on main."),
                                                     ("Zwei Zweige warten auf main.", "Two branches are waiting on main.")]
assert [beide("lage.pause", n) for n in (1, 2)] == [("Seit 1 Tag kein Commit.", "No commit for 1 day."), ("Seit 2 Tagen kein Commit.", "No commit for 2 days.")]
assert beide("ring.unter") == ("diese Woche nach Repo", "this week by repo")
assert [beide("kopf.repos", n, a) for n, a in ((1, 0), (1, 1), (14, 7))] == [("Repo, 0 diese Woche aktiv", "repo, 0 active this week"),
    ("Repo, 1 diese Woche aktiv", "repo, 1 active this week"), ("Repos, 7 diese Woche aktiv", "repos, 7 active this week")]
assert [beide("heute.rest", n) for n in (1, 2)] == [("und 1 weiteres Repo", "and 1 more repo"), ("und 2 weitere Repos", "and 2 more repos")]
assert [beide("lage.serie", n) for n in (2, 3)] == [("Seit 2 Tagen ohne Unterbruch.", "Active 2 days in a row."), ("Seit 3 Tagen ohne Unterbruch.", "Active 3 days in a row.")]
with gilt("de"):
    rang_de = t("rek.rang", 2, 1)
with gilt("en"):
    rang_en = t("rek.rang", 2, 1)
assert (rang_de, rang_en) == ("zum selben Zeitpunkt Rang 2 von 1 Woche", "at this point, rank 2 of 1 week"), (rang_de, rang_en)
assert beide("alter.tage", 4) == ("vor 4 Tagen", "4 days ago") and beide("alter.std", 3) == ("vor 3 Std.", "3 h ago")

# (d) Formate (seit 26.09.2026): Zahl, Datum, Prozent und Uhrzeit folgen nicht mehr der Oberflächensprache, sondern der Format-Locale
# (app/format.py, geprüft in tests/test_format.py). Hier nur die Trennung: die Texttabellen formatieren nichts mehr selbst, und die
# Uhrzeit folgt der Locale, nicht der Sprache.
from app import format as fmt  # noqa: E402
assert not {"zahl", "zahl1", "zahl_vz", "prozent", "tm", "tmj", "wt", "spanne", "js.locale"} & (set(de.T) | set(en.T))
assert beide("uhrzeit", "17:03") == ("17:03 Uhr", "17:03")
beispiel = json.loads((WURZEL / "tests" / "server.beispiel.json").read_text())
beispiel["zustand"]["boot_utc"] = "2026-09-08T15:03:00Z"
for s, loc, gestartet in (("de", "de_CH", "08.09.2026,\u00a017:03 Uhr"), ("en", "en_GB", "8\u00a0Sept\u00a02026,\u00a017:03"), ("en", "en_US", "Sep\u00a08,\u00a02026,\u00a05:03\u202fPM")):
    with gilt(s), fmt.gilt(fmt.locale_von(loc)):
        m = web.fuss_maschine(beispiel)
    assert gestartet in m, (s, loc, m)
for s, loc in (("en", "en_GB"), ("en", "en_US"), ("de", "de_CH")):
    with gilt(s), fmt.gilt(fmt.locale_von(loc)):
        assert f'"locale": "{loc.replace("_", "-")}"' in web.skript() and "hourCycle" not in web.skript() and "timeStyle:'short'" in web.skript()
with gilt("en"):
    assert '"laeuft": "running since {uhr}"' in web.skript()
with gilt("de"):
    assert '"laeuft": "läuft seit {uhr} Uhr"' in web.skript()

# (e) Einstellung: ohne Variable en, leer en, «fr» bricht den Start mit Meldung ab.
ohne = {k: v for k, v in os.environ.items() if k != "GITPULS_SPRACHE"}
std = lambda env: subprocess.run([sys.executable, "-c", "from app import sprache; print(sprache.STANDARD)"], cwd=WURZEL, env=env,
                                 capture_output=True, text=True)
assert std(ohne).stdout.strip() == "en" and std(ohne | {"GITPULS_SPRACHE": ""}).stdout.strip() == "en"
fr = subprocess.run([sys.executable, "-m", "app.web"], cwd=WURZEL, env=ohne | {"GITPULS_SPRACHE": "fr", "GITPULS_PORT": "0"},
                    capture_output=True, text=True, timeout=30)
assert fr.returncode == 1 and "GITPULS_SPRACHE='fr' wird nicht unterstützt" in fr.stderr, (fr.returncode, fr.stderr[-300:])
compose = (WURZEL / "compose.example.yaml").read_text()
assert "GITPULS_SPRACHE: ${GITPULS_SPRACHE:-en}" in compose, "compose.yaml reicht die Sprache durch"

# ?lang übersteuert für einen Aufruf und behält ?ruhend und ?rueckblick, in beide Richtungen.
c = db.verbinden(schreibend=True)
jetzt = datetime.now(timezone.utc)
iso = lambda x: x.strftime("%Y-%m-%dT%H:%M:%SZ")
c.execute("INSERT INTO laeufe (gestartet_utc, beendet_utc, neu) VALUES (?,?,?)", (iso(jetzt), iso(jetzt), 2))
c.executemany("INSERT INTO repos (repo, default_branch, sprache, pushed_at, gesehen, id) VALUES (?,?,?,?,?,?)",
              [("fernwerk-studio/eins", "main", "Python", iso(jetzt), iso(jetzt), 1), ("fernwerk-studio/zwei", "main", "Go", iso(jetzt), iso(jetzt), 2)])
c.executemany("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, betreff, eltern) VALUES (?,?,?,?,?,1)",
              [("e1", "fernwerk-studio/eins", iso(jetzt - timedelta(minutes=5)), "dev@fernwerk-studio.example", "Arbeit"),
               ("z1", "fernwerk-studio/zwei", iso(jetzt - timedelta(days=20)), "dev@fernwerk-studio.example", "Alt")])
c.commit(); c.close()


def hole(pfad, cookie=""):
    h = web.Handler.__new__(web.Handler)
    h.wfile, h.kopf_nur, h.headers = io.BytesIO(), False, {"Cookie": cookie} if cookie else {}
    h.send_response = lambda code: setattr(h, "code", code)
    h.send_header = lambda *a: None
    h.end_headers = lambda: None
    with contextlib.redirect_stdout(io.StringIO()):
        h.bedienen(urlparse(pfad))
    assert h.code == 200, (pfad, h.code)
    return h.wfile.getvalue().decode()


def stand(s):
    return {"lang": re.search(r'<html lang="(\w+)">', s).group(1), "ruhend_offen": '<details class="gruppe ruhend" open>' in s,
            "rueckblick_90": '<div class="fenster" data-fenster="90">' in s[s.index('class="card rueckblick"'):s.index('</section>', s.index('class="card rueckblick"'))],
            "start": re.search(r'<h1><a href="([^"]*)">', s).group(1)}


# Vorgabe hier de (GITPULS_SPRACHE der Tests): ?lang=en schaltet um, die übrigen Parameter wirken weiter.
assert stand(hole("/?ruhend=offen&rueckblick=90")) == {"lang": "de", "ruhend_offen": True, "rueckblick_90": True, "start": "/"}
assert stand(hole("/?lang=en&ruhend=offen&rueckblick=90")) == {"lang": "en", "ruhend_offen": True, "rueckblick_90": True, "start": "/?lang=en"}
assert stand(hole("/?ruhend=offen&lang=en")) == {"lang": "en", "ruhend_offen": True, "rueckblick_90": False, "start": "/?lang=en"}, stand(hole("/?ruhend=offen&lang=en"))
assert stand(hole("/?lang=fr&ruhend=offen"))["lang"] == "de", "ein fremder Wert wird übergangen"
# Umgekehrt: Vorgabe en, ?lang=de schaltet auf Deutsch, die Parameter bleiben.
alt_std, web.STANDARD = web.STANDARD, "en"
try:
    assert stand(hole("/?ruhend=offen"))["lang"] == "en"
    assert stand(hole("/?lang=de&ruhend=offen&rueckblick=90")) == {"lang": "de", "ruhend_offen": True, "rueckblick_90": True, "start": "/?lang=de"}
finally:
    web.STANDARD = alt_std
# Das Skript baut jede neue Adresse aus location.search: ein Reiter oder die Gruppe ändert ihren Parameter, lang bleibt stehen.
assert web.SKRIPT.count("new URLSearchParams(location.search)") == 4 and "q.delete('lang')" not in web.SKRIPT

# Ohne Sprachschalter (02.10.2026): Vorrang ?lang vor GITPULS_SPRACHE; ein Cookie lang wird weder gelesen noch gesetzt.
assert stand(hole("/?ruhend=offen&rueckblick=90", "lang=en")) == {"lang": "de", "ruhend_offen": True, "rueckblick_90": True, "start": "/"}, "Cookie wirkt nicht"
assert stand(hole("/?lang=en", "lang=de"))["lang"] == "en" and stand(hole("/?lang=fr", "lang=en"))["lang"] == "de", "fremder ?lang-Wert: die Umgebung gilt, nicht das Cookie"
alt_std, web.STANDARD = web.STANDARD, "en"
try:
    assert stand(hole("/", "lang=de"))["lang"] == "en" and stand(hole("/?lang=de", "lang=en"))["lang"] == "de"
finally:
    web.STANDARD = alt_std
assert json.loads(hole("/lauf/status", "lang=en")).get("zustand")
# Kein «bis <Wochentag>» als Vergleichsbeschriftung mehr (25.09.2026), in beiden Sprachen, auf der ganzen Seite samt Detail und title;
# die Wochentage aus eigenen Listen, nicht aus den Tabellen. Die Seite trägt dabei Wochenkachel, Projektzeilen, Detail und Rekorde.
for lang_, muster in (("de", r"\bbis (Mo|Di|Mi|Do|Fr|Sa|So)\b"), ("en", r"\b(to|through|until) (Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b")):
    s_ = hole(f"/?lang={lang_}")
    assert not re.search(muster, s_), (lang_, re.findall(r".{60}" + muster + r".{20}", s_))
    assert s_.count({"de": "Vorwoche zum selben Zeitpunkt", "en": "last week at this point"}[lang_]) >= 2, (lang_, "Projektzeile, Detail")
    assert {"de": "zum selben Zeitpunkt Rang", "en": "at this point, rank"}[lang_] in s_
# Vergleichszeilen der drei Kacheln (25.09.2026): Beschriftung, Doppelpunkt, Wert in eigenem Element (volle Textfarbe), Prozent gedämpft
# dahinter; das Minus ist ein echtes Minuszeichen. Erwartete Beschriftungen fest, nicht aus den Tabellen.
# Seit 30.09.2026 mit den Fenstern der Umschalter: Kachel 2 7 Tage und Woche, Kachel 3 30 Tage und Monat
# (Vormonat seit 04.10.2026 entfallen).
for lang_, beschriftungen in (("de", ("gestern", "7 Tage davor", "Vorwoche zum selben Zeitpunkt", "30 Tage davor", "Vormonat zum selben Zeitpunkt")),
                              ("en", ("yesterday", "previous 7 days", "last week at this point", "previous 30 days", "last month at this point"))):
    s_ = hole(f"/?lang={lang_}")
    zeilen_ = re.findall(r'(?:<nav class="seg klein umschalter".*?</nav>)?<span class="lbl">[^<]*</span><span class="delta">(.*?)</span>(?:<span class="delta bezug|</div>)', s_)
    assert len(zeilen_) == 5, (lang_, zeilen_)
    for b_, z_ in zip(beschriftungen, zeilen_):
        assert re.fullmatch(re.escape(b_) + r': <span class="vwert">[\d\',]+</span>(, [+\u2212]\d+ ?%)?', z_), (lang_, z_)
        assert "-" not in z_.split("</span>")[-1], ("Prozent mit echtem Minus", z_)
css_ = (WURZEL / "app" / "web" / "style.css").read_text()
assert ".tile.kpi .delta .vwert { color: var(--eis); }" in css_ and ".tile.kpi .delta, .tile.kpi .delta.bezug, .projekte .pkopf { font-size: .75rem; line-height: 1.45; color: var(--lbl); }" in css_
# Kein Umschalter mehr: weder Knöpfe noch Skript noch Stil noch Texte.
for s_ in (hole("/?lang=de"), hole("/?lang=en")):
    assert "data-lang=" not in s_ and "sprache" not in re.sub(r'class="[^"]*"', "", s_[:s_.index("<main>")]).lower() and 'class="seg small sprache"' not in s_
assert "cookie" not in web.SKRIPT.lower() and ".sprache" not in web.SKRIPT and ".sprache" not in css_ and "sprache.aria" not in de.T
# ?handlung= (seit 04.10.2026): alle Einträge stehen offen, ohne Grenze je Art und ohne «alle N anzeigen»; das entfallene zweige-alle
# und fremde Zeichen fallen weg, lang, ruhend und rueckblick bleiben dabei wirksam.
c = db.verbinden(schreibend=True)
c.executemany("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc) VALUES ('fernwerk-studio/eins', ?, 1, 0, ?)", [(f"f{i}", iso(jetzt - timedelta(days=4))) for i in range(6)])   # älter als die Zweigfrist von 3 Tagen
c.commit(); c.close()
art = lambda s: re.search(r'<p class="hbart" id="handlung-zweige">([^<]*)</p>', s).group(1)
alle6 = lambda s: s[s.index('id="handlung-zweige"'):].split("</ul>")[0].count('<li id="hb-zweige-')
s_h = hole("/?lang=en&handlung=zweige-alle&ruhend=offen&rueckblick=90")
assert art(s_h) == "6 branches waiting on main" and alle6(s_h) == 6 and "hbmehr" not in s_h and "show all" not in s_h, art(s_h)
assert stand(s_h)["lang"] == "en" and stand(s_h)["ruhend_offen"] and stand(s_h)["rueckblick_90"]
s_h = hole("/?handlung=zweige&lang=de")
assert art(s_h) == "6 Zweige warten auf main" and alle6(s_h) == 6 and "anzeigen" not in s_h and stand(s_h)["start"] == "/?lang=de"
s_h = hole("/?lang=en&handlung=offen,<x>")
assert "&lt;x&gt;" not in s_h and "<x>" not in s_h, "fremde Zeichen fallen weg"
assert "q.set('handlung',z)" in web.SKRIPT and "q.delete('handlung')" in web.SKRIPT and "-alle" not in web.SKRIPT and "hbmehr" not in web.SKRIPT
# Die Sprunglinks des Kopfs zielen seit 04.10.2026 auf die Spalte ihrer Art, jeder Eintrag steht dort ohne Klick.
s_k = hole("/?lang=de")
assert 'id="handlung-zweige"' in s_k and '<a href="#handlung-zweige">6 Zweige warten auf main</a>' in s_k and '<li id="hb-zweige-fernwerk-studio_2f_eins~f0" ' in s_k
# Die Laufphase im Knopf folgt ?lang der Seite (das Skript fragt /lauf/status?lang=…).
web.LAUF.write_text(json.dumps({"start": iso(datetime.now(timezone.utc)), "phase": "holt fernwerk-studio/eins"}))
assert json.loads(hole("/lauf/status?lang=en"))["phase"] == "fetching fernwerk-studio/eins" and json.loads(hole("/lauf/status?lang=de"))["phase"] == "holt fernwerk-studio/eins"
web.LAUF.write_text(json.dumps({"start": iso(datetime.now(timezone.utc)), "phase": "prüft CI und Sicherheit"}))
assert json.loads(hole("/lauf/status?lang=en"))["phase"] == "checking CI and security"
assert "fetch('/lauf/status?lang='+wurzel.lang" in web.SKRIPT
print("ok: gleiche Schlüssel, alle benutzt; Rückfall mit Logzeile; Plural 0, 1, 2; Formate de und en; Vorgabe en, fr bricht ab;"
      " ?lang in beide Richtungen mit ruhend, rueckblick; Vorrang ?lang, Umgebung, kein Cookie, kein Umschalter; Laufphase übersetzt")
