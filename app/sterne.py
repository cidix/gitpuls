"""Bonusteil: junge, stark besternte fremde Repos über die offizielle Search-API.

Täglich drei Fenster: erstellt in den letzten 7, 30 und 90 Tagen, je stars:>500,
sort=stars. Je Repo und Tag ein Sternstand in `sterne`, Metadaten in `fremde`
(Sprache, Beschreibung, Topics, Issues, Forks, Lizenz kommen schon im Suchtreffer mit).
Für die Repos, die auf der Seite erscheinen können (je Fenster die 15 mit den meisten
Sternen je Tag), zusätzlich `fremde_detail`: letzter Release, erster README-Absatz und
Hacker-News-Diskussionen über die Algolia-API (offiziell, ohne Schlüssel). Alles im
Sternlauf, nichts beim Seitenaufruf.
Die Ergebnisgrenze von 1000 gilt je Abfrage; die kleinen Fenster bleiben darunter,
auch wenn das 90-Tage-Fenster sie einmal reisst. Ratenlimit der Ressource search:
30 je Minute, darum wird bei Remaining=0 bis zum Reset gewartet.
Aufruf: GITHUB_TOKEN=... python -m app.sterne
"""
import base64
import json
import re
import time
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import quote

from app import db, github
from app.web import TZ, bewertet   # dieselbe Systemzone und dieselbe Rangfolge wie die Seite, keine zweite Quelle
FENSTER = (7, 30, 90)
DETAIL_JE_FENSTER = 15


def fenster(c, heute, tage):
    q = f"created:>{heute - timedelta(days=tage)} stars:>500"   # roh, github.holen kodiert (10.10.2026, Audit S5)
    seite, n, kopf, gesamt, grenze = 1, 0, {}, None, "nicht erreicht"
    while True:
        try:
            daten, kopf = github.objekt("search/repositories", {"q": q, "sort": "stars", "order": "desc", "per_page": 100, "page": seite},
                                        total_count=int, items=list)
        except github.GitHubFehler as e:
            if "422" in str(e):
                grenze = f"422 auf Seite {seite}, {(seite - 1) * 100} erreichbar"
                break
            raise
        gesamt = daten["total_count"]
        for r in daten["items"]:
            if not (isinstance(r, dict) and isinstance(r.get("full_name"), str) and isinstance(r.get("stargazers_count"), int)):
                raise github.ungueltig("search/repositories", "eine Liste von Repos mit full_name und stargazers_count", r)
            c.execute("INSERT OR REPLACE INTO sterne VALUES (?,?,?)", (r["full_name"], heute.isoformat(), r["stargazers_count"]))
            c.execute("INSERT OR REPLACE INTO fremde (repo, erstellt, sprache, beschreibung, gesehen, topics, issues, forks, lizenz) VALUES (?,?,?,?,?,?,?,?,?)",
                      (r["full_name"], r["created_at"], r["language"], r["description"], heute.isoformat(),
                       json.dumps(r.get("topics") or []), r.get("open_issues_count"), r.get("forks_count"),
                       (r.get("license") or {}).get("spdx_id") if r.get("license") else None))
            n += 1
        if int(kopf.get("X-RateLimit-Remaining", 1)) == 0:
            wartezeit = max(0, int(kopf.get("X-RateLimit-Reset", 0)) - time.time()) + 1
            print(f"Ratenlimit erschöpft, warte {wartezeit:.0f}s")
            time.sleep(wartezeit)
        if 'rel="next"' not in (kopf.get("Link") or ""):
            break
        seite += 1
    print(f"{tage:3d} Tage: total_count={gesamt}, abgerufen={n}, Seiten={seite}, Ergebnisgrenze {grenze}, "
          + ", ".join(f"{k}={kopf.get(k)}" for k in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Resource")))


def absatz_aus(text):
    """Erster Textabsatz eines README, 300 Zeichen. Überschriften, Bilder, Badges, Blockzitate, Callouts, reine
    Linkzeilen und HTML fallen weg. Das «>» deckt Blockzitat und Callout (`> [!NOTE]`) in einem Zeichen ab; eine
    Zeile, die zur Hälfte aus Linkmarkup besteht, ist eine Badge- oder Menüzeile und kein Satz (Entscheid
    20.09.2026: die Was-Zeile der Rangliste entsteht aus diesem Satz, nicht aus dem Marketingabsatz).
    Getrennt vom Abruf, damit der Filter ohne GitHub prüfbar ist (tests/test_sterne.py)."""
    for absatz in re.split(r"\n\s*\n", text):
        a = absatz.strip()
        if not a or a.startswith(("#", "<", "!", "[!", "[![", "|", "```", "---", ">")):
            continue
        if len(re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", a)) < len(a) / 2:
            continue                                        # Badge- oder Linkzeile, kein Satz
        a = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", a)          # Bilder
        a = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", a)      # Links auf ihren Text
        a = re.sub(r"<[^>]+>", "", a)
        a = re.sub(r"\s+", " ", a).strip(" *_`")
        if len(a) >= 20:
            return a if len(a) <= 300 else a[:299].rstrip() + "…"
    return None


class Unbekannt(Exception):
    """Ein Abruf ist gescheitert: der Wert ist unbekannt, nicht leer (03.10.2026, Codex-Audit)."""


def gemessen(pfad):
    """Ein Detailabruf: 404 heisst gemessen leer (None), jeder andere Fehler Unbekannt. Ratenlimit und Netz beenden den Lauf. Eine Antwort, die
    kein Objekt ist (null, "", 0), ist seit 11.10.2026 Unbekannt (Codex-Audit, Klasse A), vorher hiess sie «kein Release» oder «kein README»."""
    try:
        d, _ = github.objekt(pfad)
        return d
    except github.GitHubFehler as e:
        if e.global_:
            raise
        if e.status == 404:
            return None
        raise Unbekannt(str(e)) from e


def readme_absatz(repo):
    d = gemessen(f"repos/{repo}/readme")
    if d is not None and not isinstance(d.get("content"), str):
        raise Unbekannt(f"repos/{repo}/readme ohne content")
    return absatz_aus(base64.b64decode(d.get("content") or "").decode("utf-8", "replace")) if d else None


def release(repo):
    d = gemessen(f"repos/{repo}/releases/latest")
    return (d.get("tag_name") or d.get("name"), (d.get("published_at") or "")[:10] or None) if d else (None, None)


def hacker_news(repo):
    """Diskussionen zu diesem Repo: Algolia-Suche auf die URL, nur Stories, die drei besten nach Punkten. Ein Fehler ist Unbekannt."""
    url = ("https://hn.algolia.com/api/v1/search?tags=story&hitsPerPage=3&restrictSearchableAttributes=url&query="
           + quote(f"github.com/{repo}"))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "gitpuls"}), timeout=15) as r:
            hits = json.load(r).get("hits", [])
    except Exception as e:
        raise Unbekannt(f"{type(e).__name__}: {e}") from e
    if not isinstance(hits, list) or not all(isinstance(h, dict) for h in hits):
        raise Unbekannt("Algolia-Antwort ohne Liste von Treffern")
    return [{"titel": h.get("title"), "punkte": h.get("points") or 0, "kommentare": h.get("num_comments") or 0,
             "link": f"https://news.ycombinator.com/item?id={h.get('objectID')}"} for h in hits]


def details(c, heute):
    """Kandidaten: je Fenster die 15 nach Sternen je Tag. Nur nachholen, was heute noch nicht geholt ist.
    Messvertrag seit 03.10.2026 (Codex-Audit): ein gescheiterter Abruf ersetzt keinen bekannten Wert und stempelt gesehen nicht, das
    Repo ist am nächsten Lauf wieder dran. Ein Feld ohne früheren Wert steht dann in ungemessen (release, readme) oder als hn NULL;
    die Seite zeigt «nicht gemessen» statt «kein Release» oder «keine Diskussion». Vorher machte INSERT OR REPLACE aus jedem Fehler
    einen gemessen leeren Wert mit dem Stempel von heute."""
    # Dieselbe Rangfolge wie die Seite (app/web.py::bewertet, 10.10.2026, Audit S21): vorher rechnete die Auswahl das Alter in Bruchteilen
    # über julianday in UTC und die Anzeige in Kalendertagen der Systemzone, ein angezeigtes Repo konnte so ohne Details bleiben.
    kandidaten = {r["repo"] for tage in FENSTER for _, _, r in bewertet(c, heute, tage)[:DETAIL_JE_FENSTER]}
    frisch = {r[0] for r in c.execute("SELECT repo FROM fremde_detail WHERE gesehen = ?", (heute.isoformat(),))}
    n = fehl = 0
    for repo in sorted(kandidaten - frisch):
        alt = c.execute("SELECT release, release_datum, readme, hn, gesehen, ungemessen FROM fremde_detail WHERE repo = ?", (repo,)).fetchone()
        alt_offen = set((alt[5] or "").split(",")) - {""} if alt else {"release", "readme"}
        werte, offen = {}, set()
        for feld, holen in (("release", lambda: release(repo)), ("readme", lambda: readme_absatz(repo)), ("hn", lambda: json.dumps(hacker_news(repo), ensure_ascii=False))):
            try:
                werte[feld] = holen()
            except Unbekannt as e:
                print(f"{repo}: {feld} nicht lesbar, {e}; der letzte Stand bleibt")
                offen.add(feld)
        if offen:
            fehl += 1
        rel = werte["release"] if "release" in werte else (alt[0], alt[1]) if alt else (None, None)
        readme = werte["readme"] if "readme" in werte else alt[2] if alt else None
        hn = werte["hn"] if "hn" in werte else alt[3] if alt else None
        ungemessen = sorted(f for f in ("release", "readme") if f in offen and f in alt_offen)
        c.execute("INSERT OR REPLACE INTO fremde_detail (repo, release, release_datum, readme, hn, gesehen, ungemessen) VALUES (?,?,?,?,?,?,?)",
                  (repo, *rel, readme, hn, (alt[4] if alt else "") if offen else heute.isoformat(), ",".join(ungemessen) or None))
        n += 1
    print(f"Details: {len(kandidaten)} Kandidaten, {n} geholt, davon {fehl} mit Fehler (Release, README, Hacker News)")


def main():
    heute = db.jetzt(TZ).date()
    with db.schreibsperre():
        _main(heute)


def _main(heute):
    c = db.verbinden(schreibend=True)
    for tage in FENSTER:
        fenster(c, heute, tage)
    c.commit()
    details(c, heute)
    c.commit()
    print("fremde gesamt:", c.execute("SELECT COUNT(*) FROM fremde").fetchone()[0], "| Sternstände:", c.execute("SELECT COUNT(*) FROM sterne").fetchone()[0],
          "| Details:", c.execute("SELECT COUNT(*) FROM fremde_detail").fetchone()[0])


if __name__ == "__main__":
    main()
