"""Der README-Filter des Sternlaufs. python3 -m tests.test_sterne

Er entscheidet, welcher Absatz als «Was es ist» in der Rangliste landet (Entscheid 20.09.2026). Geprüft wird
`absatz_aus` ohne GitHub; die Vorlagen sind echte README-Formen: Titel, Badgezeile, Callout, Blockzitat,
Linkmenü, HTML-Kopf, Tabelle, Codeblock. Bis zum 20.09.2026 liess der Filter Blockzitate und Callouts durch,
und der Absatz von browser-use/jev-ultrafast stand als Waitlist-Aufruf auf der Seite.
"""
import os
os.environ["TZ"] = "Europe/Zurich"   # die Zone setzt der Test selbst (10.10.2026, Audit S28): grün unter jeder TZ der Shell
from app.sterne import absatz_aus

# 1: die übliche Reihenfolge — Titel, Badges, Callout, dann der Satz.
readme = """# jev-ultrafast

[![build](https://img.shields.io/x.svg)](https://ci) [![npm](https://img.shields.io/n.svg)](https://npm)

> [!IMPORTANT]
> **Die Cloud-Warteliste ist offen.** Jetzt eintragen.

Das Werkzeug baut Projekte schneller als alles andere.
"""
assert absatz_aus(readme) == "Das Werkzeug baut Projekte schneller als alles andere.", absatz_aus(readme)

# 2: jede Form einzeln, davor kein Satz — der Filter muss über sie hinweggehen.
SATZ = "Ein Werkzeug, das Projekte aus einer Beschreibung baut."
for name, vorspann in (
    ("Ueberschrift", "## Installation"),
    ("Badgezeile", "[![a](i.svg)](x) [![b](j.svg)](y) [![c](k.svg)](z)"),
    ("Bildzeile", "![Screenshot](bild.png)"),
    ("Callout", "> [!NOTE]\n> Dies ist ein Hinweis, der keine Beschreibung ist."),
    ("Blockzitat", "> Ein Zitat, das den Autor lobt und nichts erklaert."),
    ("Linkmenue", "[Doku](https://a.example) · [Discord](https://b.example) · [Blog](https://c.example)"),
    ("HTML", "<p align=\"center\">Ein zentrierter Absatz mit Markup</p>"),
    ("Tabelle", "| Spalte | Wert |\n| --- | --- |"),
    ("Codeblock", "```bash\nnpm install jev\n```"),
    ("Trennlinie", "---"),
):
    assert absatz_aus(f"{vorspann}\n\n{SATZ}\n") == SATZ, (name, absatz_aus(f"{vorspann}\n\n{SATZ}\n"))

# 3: Links und Bilder INNERHALB des Satzes bleiben als Text stehen, solange der Absatz nicht überwiegend Markup ist.
mit_link = "Ein [schneller](https://x.example) Paketmanager fuer Python, geschrieben in Rust."
assert absatz_aus(mit_link) == "Ein schneller Paketmanager fuer Python, geschrieben in Rust.", absatz_aus(mit_link)

# 4: zu kurz ist kein Absatz, gar kein Text ist None; über 300 Zeichen wird gekürzt.
assert absatz_aus("Kurz.\n\n" + SATZ) == SATZ, "unter 20 Zeichen zaehlt nicht"
assert absatz_aus("# nur ein Titel\n") is None and absatz_aus("") is None
lang = absatz_aus("x" * 500)
assert len(lang) == 300 and lang.endswith("…"), len(lang)

print("ok: Titel, Badges, Bilder, Callout, Blockzitat, Linkmenue, HTML, Tabelle, Codeblock und Trennlinie"
      " fallen weg; Links im Satz bleiben als Text, zu kurz zaehlt nicht, ueber 300 Zeichen wird gekuerzt")

# Messvertrag der Details (03.10.2026, Codex-Audit): ein gescheiterter Abruf ersetzt keinen bekannten Wert und stempelt gesehen nicht;
# ohne früheren Wert zeigt die Seite «nicht gemessen» statt «kein Release» oder «keine Diskussion». Ein 404 ist gemessen leer.
import json  # noqa: E402
import os  # noqa: E402
import tempfile  # noqa: E402
import urllib.request  # noqa: E402
from datetime import date, timedelta  # noqa: E402

from app import db, github, sterne, web  # noqa: E402

db.DB = __import__("pathlib").Path(tempfile.mkdtemp()) / "sterne.db"
c = db.verbinden(schreibend=True)
tag1, tag2 = date(2026, 10, 2), date(2026, 10, 3)
for repo in ("x/bekannt", "x/neu"):
    c.execute("INSERT INTO fremde (repo, erstellt, sprache, beschreibung, gesehen) VALUES (?, '2026-09-30T00:00:00Z', 'Python', '', ?)", (repo, tag1.isoformat()))
    for t_ in (tag1, tag2):
        c.execute("INSERT INTO sterne VALUES (?, ?, 900)", (repo, t_.isoformat()))
c.commit()
ANTWORT = {}


def fehler_(status):
    e = github.GitHubFehler(f"nachgebaut {status}")
    e.status, e.meldung, e.global_ = status, "", False
    return e


def holen_(pfad, params=None, leer_bei_409=False):
    a = ANTWORT[pfad]
    if isinstance(a, BaseException):
        raise a
    return a, {}


def hn_(req, timeout=None):
    a = ANTWORT["hn"]
    if isinstance(a, BaseException):
        raise a
    return __import__("io").BytesIO(json.dumps({"hits": a}).encode())


def details(heute):
    alt = github.holen, urllib.request.urlopen
    github.holen, urllib.request.urlopen = holen_, hn_
    try:
        sterne.details(c, heute)
        c.commit()
    finally:
        github.holen, urllib.request.urlopen = alt
    return {r[0]: tuple(r[1:]) for r in c.execute("SELECT repo, release, readme, hn, gesehen, ungemessen FROM fremde_detail")}


readme = {"content": __import__("base64").b64encode("Ein Werkzeug, das genau eine Sache gut macht.".encode()).decode()}
ANTWORT.update({"repos/x/bekannt/releases/latest": {"tag_name": "v1", "published_at": "2026-10-01T00:00:00Z"}, "repos/x/bekannt/readme": readme,
                "repos/x/neu/releases/latest": fehler_(404), "repos/x/neu/readme": fehler_(404), "hn": []})
d = details(tag1)
assert d["x/bekannt"] == ("v1", "Ein Werkzeug, das genau eine Sache gut macht.", "[]", "2026-10-02", None), d
assert d["x/neu"] == (None, None, "[]", "2026-10-02", None), ("404 ist gemessen leer", d)
# Tag 2: alles scheitert ausser dem README von bekannt (404, gemessen kein README mehr).
ANTWORT.update({"repos/x/bekannt/releases/latest": fehler_(502), "repos/x/bekannt/readme": fehler_(404), "hn": OSError("Netz weg"),
                "repos/x/neu/releases/latest": fehler_(502), "repos/x/neu/readme": fehler_(502)})
c.execute("DELETE FROM fremde_detail WHERE repo = 'x/neu'"); c.commit()   # neu hat noch keine Zeile: es gibt keinen früheren Wert
d = details(tag2)
assert d["x/bekannt"] == ("v1", None, "[]", "2026-10-02", None), ("bekannte Werte bleiben, gesehen bleibt beim letzten Erfolg", d)
assert d["x/neu"] == (None, None, None, "", "readme,release"), ("ohne früheren Wert: ungemessen, gesehen leer", d)
_, ranglisten = web.ranglisten(c)
zeilen = {r["repo"]: (tempo, alter, r) for tempo, alter, r in ranglisten[7]}
with __import__("app.sprache", fromlist=["gilt"]).gilt("de"):
    html_neu = web.tabelle_fremde([zeilen["x/neu"]], tag2)
    html_bekannt = web.tabelle_fremde([zeilen["x/bekannt"]], tag2)
assert "Release nicht gemessen" in html_neu and "Hacker News nicht gemessen." in html_neu and "kein Release" not in html_neu \
    and "Keine Diskussion" not in html_neu and "nicht gemessen</span>" not in html_neu.split("Was es ist")[0], html_neu[-900:]
assert "letzter Release v1" in html_bekannt and "Keine Diskussion auf Hacker News gefunden." in html_bekannt, html_bekannt[-900:]
# Am nächsten Lauf ist neu wieder dran (gesehen ist nicht heute), und ein gelungener Abruf löscht ungemessen.
ANTWORT.update({"repos/x/neu/releases/latest": {"tag_name": "v2"}, "repos/x/neu/readme": fehler_(404), "hn": [],
                "repos/x/bekannt/releases/latest": {"tag_name": "v1", "published_at": "2026-10-01T00:00:00Z"}})
d = details(tag2)
assert d["x/neu"] == ("v2", None, "[]", "2026-10-03", None) and d["x/bekannt"][4] is None and d["x/bekannt"][3] == "2026-10-03", d
c.close()
print("ok: README-Filter; Details nach Messvertrag (Fehler ersetzt nichts, stempelt nicht, «nicht gemessen» statt «kein Release»)")
