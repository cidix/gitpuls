"""Sammler: alle Commits der eigenen Repos in die Tabelle commits, idempotent über sha.

Erstbefüllung mit --alles, sonst je Repo inkrementell ab dessen Abrufstand
(repos.abgerufen_utc) minus sieben Tage; ein Repo ohne Abrufstand wird ganz geholt. Bots und fremde Adressen werden mitgespeichert, gefiltert
wird bei der Abfrage. Aufruf: GITHUB_TOKEN=... python -m app.sammler [--alles]
"""
import json
import re
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote

from app import db, github

LAUF = db.DB.parent / "lauf.json"   # liest app/web.py für den Knopf "Aktualisieren" und GET /lauf/status
TOKEN = db.DB.parent / "token.json"   # Ablaufdatum des Tokens, liest app/web.py für die Warnung im Handlungsbedarf
_lauf = {}


def merken(**k):
    """Zustand dieses Laufs (start, phase, fertig, exit, fehler, neu) neben die Datenbank. Der Sammler
    schreibt ihn selbst, damit auch die Läufe des Timers dort erscheinen, nicht nur die per Knopf."""
    _lauf.update(k)
    LAUF.write_text(json.dumps(_lauf))


def jetzt():
    return db.jetzt(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def laufzeile(c, start, neu, teilweise=0):
    """Buchführung, vom Werk getrennt: die Commits sind schon festgeschrieben, ein Fehler hier wird
    protokolliert und reisst den Lauf nicht mit (Exit 0). Spaltenliste ausdrücklich: bestehende
    Datenbanken haben aus 4b3d201 (03.09.2026) zwei weitere Spalten zustand und token_bis, die der
    Reset auf 0a457fc am 04.09.2026 aus dem Code nahm; VALUES (?,?,?) scheiterte seither stündlich
    mit "table laeufe has 5 columns but 3 values were supplied" und riss alles Geholte mit."""
    try:
        # teilweise (26.09.2026): Zahl der Repos, die GitHub in diesem Lauf abwies; 0 ist ein voller Erfolg, mehr ein Teilerfolg.
        c.execute("INSERT INTO laeufe (gestartet_utc, beendet_utc, neu, teilweise) VALUES (?,?,?,?)", (start, jetzt(), neu, teilweise))
        c.commit()
    except sqlite3.Error as e:
        c.rollback()
        print(f"Laufzeile nicht geschrieben, {type(e).__name__}: {e}. Die Commits sind gespeichert.")


NAMENSTABELLEN = ("commits", "zweige", "repos", "pruefungen", "warnungen")


def umbenennen(c, alt, neu):
    umziehen(c, [(alt, neu)])


def umziehen(c, paare):
    """Repos unter neuem Namen, alle Paare (alt, neu) eines Laufs in EINER Transaktion: commits, zweige, repos, pruefungen und warnungen
    ziehen mit (seit 10.10.2026, Audit S3, auch die zwei letzten). Erst wandert jedes alte Repo auf einen Zwischennamen, dann auf den neuen
    (Codex-Audit 11.10.2026, Befund 2): bei getauschten Namen ging die Historie sonst Schritt für Schritt auf denselben Namen, und zwei
    gleichnamige Zweige scheiterten an UNIQUE(repo, name). Eine Zeile unter dem neuen Namen ohne Historie (aus einem Lauf vor dieser
    Erkennung) räumt der Umzug weg."""
    for alt, _ in paare:
        for tabelle in NAMENSTABELLEN:
            c.execute(f"UPDATE {tabelle} SET repo = ? WHERE repo = ?", ("\0umzug\0" + alt, alt))
    for alt, neu in paare:
        zwischen = "\0umzug\0" + alt
        n = c.execute("SELECT COUNT(*) FROM commits WHERE repo = ?", (zwischen,)).fetchone()[0]
        for tabelle in ("zweige", "repos", "pruefungen", "warnungen"):
            c.execute(f"DELETE FROM {tabelle} WHERE repo = ?", (neu,))
        for tabelle in NAMENSTABELLEN:
            c.execute(f"UPDATE {tabelle} SET repo = ? WHERE repo = ?", (neu, zwischen))
        print(f"umbenannt: {alt} -> {neu}, {n} Commits mitgenommen")
    c.commit()


FEHLGESCHLAGEN = {"failure", "timed_out", "startup_failure"}
STUFEN = ("low", "medium", "high", "critical")
# Codezeilen für den Altbestand der letzten 90 Tage, in Portionen: festgeschrieben je ZEILEN_PORTION Commits, und der Lauf hört
# nach ZEILEN_FRIST_S auf, damit er mit dem übrigen Werk (rund 30 s) weit unter TimeoutStartSec=600 der Unit bleibt. Gemessen am
# 25.09.2026: 0.45 s je Anfrage, also rund 500 Commits je Lauf.
ZEILEN_TAGE, ZEILEN_FRIST_S, ZEILEN_PORTION = 90, 240, 20
# Ein nicht verfügbarer Commit (404, 422) wird nach 1 Stunde, 1 Tag und 1 Woche erneut abgefragt, danach nie mehr (Audit 25.09.2026).
ABSTAENDE_S = (3600, 86400, 604800)


def fehler_text(e):
    """Der Fehler einer Prüfabfrage als Text für pruefungen.*_fehler: Code und GitHubs Meldung, sonst die Fehlerklasse."""
    if isinstance(e, github.GitHubFehler):
        return f"{e.status} {e.meldung}".strip() if e.status else str(e)
    return f"{type(e).__name__}: {e}"


def warnungen_von(fn):
    """Offene Dependabot-Warnungen eines Repos. Die API blättert mit Cursor (after aus dem Link-Header), nicht mit page: alle_seiten
    hängt page an, und GitHub antwortet 400 «Pagination using the `page` parameter is not supported» (gemessen 25.09.2026, nach dem
    Einschalten in allen 14 Repos; bis dahin verdeckte das 403 «disabled» den Fehler)."""
    aus, params = [], {"state": "open", "per_page": 100}
    while True:
        j, kopf = github.liste(f"repos/{fn}/dependabot/alerts", params)
        aus += j
        weiter = re.search(r'[?&]after=([^&>]+)[^>]*>;\s*rel="next"', kopf.get("Link") or "")
        if not weiter:
            return aus
        params = {**params, "after": unquote(weiter.group(1))}   # der Link trägt ihn kodiert, holen() kodiert ihn wieder (Audit S5)


def warnung_zeile(fn, a):
    """Eine offene Dependabot-Warnung als Zeile für die Tabelle warnungen, aus der API-Antwort."""
    abh, adv, verw = a.get("dependency") or {}, a.get("security_advisory") or {}, a.get("security_vulnerability") or {}
    paket = abh.get("package") or {}
    behoben = (verw.get("first_patched_version") or {}).get("identifier")
    return (fn, a.get("number"), paket.get("name") or "?", paket.get("ecosystem"), abh.get("manifest_path"), adv.get("severity"), behoben,
            a.get("html_url") or "", adv.get("summary"), abh.get("scope"), a.get("created_at"), verw.get("vulnerable_version_range"),
            adv.get("ghsa_id"), adv.get("cve_id"))


def pruefen(c, r, stempel):
    """CI und Sicherheitswarnungen eines Repos (25.09.2026). Scheitert nie, liefert die Zahl der gescheiterten Abfragen (0 bis 2).
    Messvertrag seit 03.10.2026 (Codex-Audit): nur eine gelungene Abfrage ersetzt den bekannten Stand, auch durch einen leeren; ein
    Fehler lässt Wert und Messzeitpunkt stehen und setzt nur *_fehler. Bis dahin überschrieb ein 502 bekannte Warnungen mit NULL, und
    die Seite zeigte «Kein Handlungsbedarf». Gemessen ist auch «Dependabot ausgeschaltet» (403 «… disabled …»): dann gibt es keine
    Warnungen. Ein 404 auf die Warnungen ist kein Beleg für «keine» und löscht nichts. Ratenlimit und Netz beenden den Lauf."""
    fn = r["full_name"]
    ci = ci_fehler = sicher = sicher_fehler = einzeln = None
    try:
        j, _ = github.objekt(f"repos/{fn}/actions/runs", {"branch": r["default_branch"], "status": "completed", "per_page": 1}, workflow_runs=list)
        lauf = (j["workflow_runs"] or [None])[0]
        if lauf is not None and not isinstance(lauf, dict):
            raise github.ungueltig(f"repos/{fn}/actions/runs", "eine Liste von Läufen", lauf)
        ci = ("fehlgeschlagen" if lauf.get("conclusion") in FEHLGESCHLAGEN else "ok", lauf.get("name"), lauf.get("updated_at")) if lauf else ("keine", None, None)
    except github.GitHubFehler as e:
        if e.global_:
            raise
        ci_fehler = fehler_text(e)
    except Exception as e:   # noqa: BLE001, die Prüfung darf den Lauf nie mitreissen
        ci_fehler = fehler_text(e)
    try:
        offen = warnungen_von(fn)
        gefunden = [(a.get("security_advisory") or {}).get("severity") for a in offen]
        sicher = ("ok", len(offen), max((g for g in gefunden if g in STUFEN), key=STUFEN.index, default=None))
        einzeln = [warnung_zeile(fn, a) for a in offen if a.get("number") is not None]   # ohne Nummer kein Schlüssel; die Zahl zählt sie mit
    except github.GitHubFehler as e:
        if e.global_:
            raise
        if e.status == 403 and "disabled" in (e.meldung or "").lower():
            sicher, einzeln = ("aus", None, None), []
        else:
            sicher_fehler = fehler_text(e)
    except Exception as e:   # noqa: BLE001
        sicher_fehler = fehler_text(e)
    for was, f_ in (("Actions", ci_fehler), ("Dependabot", sicher_fehler)):
        if f_:
            print(f"{fn}: {was} nicht lesbar, {f_}; der letzte Stand bleibt")
    if einzeln is not None:   # nur nach einer Messung: die offenen Warnungen ersetzen den letzten Stand, ausgeschaltet heisst keine
        c.execute("DELETE FROM warnungen WHERE repo = ?", (fn,))
        c.executemany("INSERT INTO warnungen (repo, nummer, paket, oekosystem, manifest, stufe, behoben, url, summary, scope, geoeffnet, bereich, ghsa, cve) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", einzeln)
    # Zeile anlegen, falls neu (Werte unbekannt), dann je Abfrage entweder Messung samt Zeitpunkt oder nur den Fehler setzen.
    c.execute("INSERT OR IGNORE INTO pruefungen (repo, ci, sicherheit, gesehen) VALUES (?, 'unbekannt', 'unbekannt', ?)", (fn, stempel))
    c.execute("UPDATE pruefungen SET gesehen = ?, ci_fehler = ?, sicher_fehler = ? WHERE repo = ?", (stempel, ci_fehler, sicher_fehler, fn))
    if ci:
        c.execute("UPDATE pruefungen SET ci = ?, ci_workflow = ?, ci_utc = ?, ci_gemessen = ? WHERE repo = ?", (*ci, stempel, fn))
    if sicher:
        c.execute("UPDATE pruefungen SET sicherheit = ?, warnungen = ?, warn_stufe = ?, sicher_gemessen = ? WHERE repo = ?", (*sicher, stempel, fn))
    return bool(ci_fehler) + bool(sicher_fehler)


def dateien_eines(repo, sha):
    """Zeilen je Datei und Zahl der Eltern eines Commits, roh aus dem Commit-Detail: ([(pfad, hinzugefügt, entfernt)], eltern). Welche
    Dateien zählen, entscheidet db.zaehlt() erst bei der Abfrage. Die Dateien kommen seitenweise, die Eltern stehen auf jeder Seite."""
    aus, seite, eltern = [], 1, None
    while True:
        j, kopf = github.objekt(f"repos/{repo}/commits/{sha}", {"per_page": 100, "page": seite}, parents=list, files=list)
        eltern = len(j["parents"]) if eltern is None else eltern
        aus += [(f.get("filename", ""), f.get("additions", 0), f.get("deletions", 0)) for f in j["files"] if isinstance(f, dict)]
        if 'rel="next"' not in (kopf.get("Link") or ""):
            return aus, eltern
        seite += 1


def token_messen():
    """Ablaufdatum des Tokens nach dem Weg unter Harte Grenzen (10.10.2026): der Kopf github-authentication-token-expiration der Antwort auf
    /rate_limit, die selbst kein Kontingent kostet. Geschrieben nach token.json neben der Datenbank, {"ablauf": Text oder null, "gemessen"};
    null heisst ein Token ohne Ablaufdatum. Scheitert die Abfrage, bleibt die alte Datei, und der Lauf geht weiter: die nächste Abfrage
    zeigt einen globalen Fehler ohnehin."""
    try:
        _, kopf = github.holen("rate_limit")
        TOKEN.write_text(json.dumps({"ablauf": kopf.get("github-authentication-token-expiration"), "gemessen": jetzt()}))
    except (Exception, github.GitHubFehler) as e:   # noqa: BLE001, GitHubFehler ist SystemExit; eine Messung am Rand reisst den Lauf nicht mit
        print(f"Ablauf des Tokens nicht gemessen, {type(e).__name__}: {e}")


def utc_text(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def faellig(versuche, versucht, jetzt_):
    """Ein nicht verfügbarer Commit ist wieder dran, wenn seit dem letzten Versuch der Abstand dieser Stufe vergangen ist; nach dem
    letzten Abstand gilt er als dauerhaft ohne Zeilen."""
    if not versuche:
        return True
    if versuche > len(ABSTAENDE_S):
        return False
    return (jetzt_ - datetime.fromisoformat(versucht.replace("Z", "+00:00"))).total_seconds() >= ABSTAENDE_S[versuche - 1]


def repo_fehler(c, repo, e, stempel):
    """Ein Fehler, der nur dieses Repo betrifft: gespeichert je Repo, der erste Zeitpunkt der Reihe bleibt stehen."""
    text = fehler_text(e)
    c.execute("UPDATE repos SET fehler = ?, fehler_utc = COALESCE(fehler_utc, ?) WHERE repo = ?", (text, stempel, repo))
    print(f"{repo}: GitHub {text}, Repo für diesen Lauf übersprungen")


def zeilen_nachladen(c, frist_s=ZEILEN_FRIST_S, jetzt_=None):
    """Zeilen je Datei und Zahl der Eltern aus dem Commit-Detail. In der Liste stehen die Commits der letzten ZEILEN_TAGE ohne Zeilen
    (Merges ausgenommen) und jeder Commit ohne Elternzahl, auch älter (Audit 25.09.2026: sonst zählte ein alter Merge weiter), jüngste
    zuerst. Festgeschrieben je Portion: ein Abbruch verwirft höchstens die laufende Portion.
    404 und 422 (Commit oder Repo nicht mehr da) setzen «nicht verfügbar» (dateien_geladen = 0) mit Versuchen und Zeitpunkt, nie
    geladen; die Elternzahl bleibt NULL. Wiederholt wird nach ABSTAENDE_S. Ein anderer Fehler des Repos überspringt dessen übrige
    Commits für diesen Lauf, nur ein globaler (Netz, 401, Ratenlimit) beendet das Nachladen. Liefert (geladen, noch offen, Fehler); der
    Fehler ist der Text eines globalen Abbruchs, sonst None, und steht seit 10.10.2026 im Laufstatus (Audit O1)."""
    t0, jetzt_ = time.monotonic(), jetzt_ or db.jetzt(timezone.utc)
    grenze, stempel = utc_text(jetzt_ - timedelta(days=ZEILEN_TAGE)), utc_text(jetzt_)
    offen = [(sha, repo) for sha, repo, versuche, versucht in c.execute(
        "SELECT sha, repo, dateien_versuche, dateien_versucht FROM commits WHERE eltern IS NULL "
        "OR (dateien_geladen IS NOT 1 AND eltern <= 1 AND zeitstempel_utc >= ?) ORDER BY zeitstempel_utc DESC", (grenze,))
        if faellig(versuche, versucht, jetzt_)]
    # Erst holen, dann schreiben: die Dateien einer Portion kommen über das Netz, geschrieben wird sie in EINER kurzen Transaktion.
    # Vorher stand die Schreibsperre offen, solange eine Portion geholt wurde (rund 9 s bei 20 Commits), und ein zweiter Schreiber
    # scheiterte nach 5 s mit «database is locked» (gemessen am 25.09.2026 um 03:00, Timer-Lauf neben einem Nachladelauf).
    n, portion, gesperrt, abgewiesen, fehler = 0, [], set(), [], None
    def schreiben():
        for repo_, e_ in abgewiesen:
            repo_fehler(c, repo_, e_, stempel)
        abgewiesen.clear()
        for sha_, ergebnis in portion:
            if ergebnis is None:   # nicht verfügbar; bereits geladene Zeilen bleiben, fehlt nur die Elternzahl
                c.execute("UPDATE commits SET dateien_geladen = CASE WHEN dateien_geladen = 1 THEN 1 ELSE 0 END, "
                          "dateien_versuche = COALESCE(dateien_versuche, 0) + 1, dateien_versucht = ? WHERE sha = ?", (stempel, sha_))
                continue
            dateien_, eltern_ = ergebnis
            c.execute("DELETE FROM commit_dateien WHERE sha = ?", (sha_,))
            c.executemany("INSERT INTO commit_dateien (sha, pfad, plus, minus) VALUES (?,?,?,?)", [(sha_, p, a, m) for p, a, m in dateien_])
            c.execute("UPDATE commits SET dateien_geladen = 1, eltern = ?, dateien_versuche = NULL, dateien_versucht = NULL WHERE sha = ?",
                      (eltern_, sha_))
        c.commit()
        portion.clear()
    for sha, repo in offen:
        if time.monotonic() - t0 > frist_s:
            break
        if repo in gesperrt:
            continue
        try:
            ergebnis = dateien_eines(repo, sha)
        except github.GitHubFehler as e:
            if e.global_:
                fehler = f"Codezeilen: {e}"
                print(f"{fehler}, Nachladen endet für diesen Lauf")
                break
            if e.status not in (404, 422):
                gesperrt.add(repo)
                abgewiesen.append((repo, e))
                continue
            ergebnis = None
        portion.append((sha, ergebnis))
        n += 1
        if len(portion) >= ZEILEN_PORTION:
            schreiben()
    schreiben()
    return n, len(offen) - n, fehler


def main(alles=False, ende=None):
    # Die Verbindung schliesst auch bei einem Abbruch (GitHubFehler): sonst hält sie die Schreibsperre, bis der
    # Prozess endet, und der nächste Lauf im selben Prozess (Tests) sieht «database is locked».
    # Eine Frist ab Laufbeginn für Warten und Werk (26.09.2026), db.LAUFFRIST_S, live 600 s.
    ende = db.frist() if ende is None else ende
    with db.schreibsperre(ende):
        c = db.verbinden(schreibend=True)
        github.FRIST_ENDE = ende   # holen wartet am Ratenlimit, solange es in die Frist passt
        try:
            werk(c, alles, ende)
        except github.GitHubFehler:
            c.commit()   # am Ratenlimit oder Netz: Zweige und Prüfungen der fertigen Repos bleiben, der nächste Lauf setzt fort (10.10.2026)
            raise
        finally:
            github.FRIST_ENDE = None
            c.close()


def gesperrte_zahl(c, start):
    """Repos, die dieser Lauf mit einem Fehler zurückliess: Commits, Zweige oder Codezeilen (repos.fehler) und seit 03.10.2026 auch CI
    oder Sicherheit (pruefungen.*_fehler), je Repo einmal. Jeder Fehler macht den Lauf zum Teilerfolg."""
    return c.execute("SELECT COUNT(*) FROM repos r WHERE r.gesehen = ? AND (r.fehler IS NOT NULL OR EXISTS (SELECT 1 FROM pruefungen p "
                     "WHERE p.repo = r.repo AND p.gesehen = ? AND (p.ci_fehler IS NOT NULL OR p.sicher_fehler IS NOT NULL)))", (start, start)).fetchone()[0]


PRO_SEITE = 100   # per_page der Commitliste; nach so vielen Commits fordert alle_seiten die nächste Seite an
IMPORT_ANTEIL = 0.5   # Anteil der Frist, nach dem ein Teil des Erstimports endet (Befund 3)


def kopf_da(fn, kopf):
    """Ist der beim Start des Imports festgehaltene Kopf noch abrufbar? 404 und 422 heissen nein (Force-Push, anderes Repo unter demselben
    Namen); jeder andere Fehler ist ein Fehler des Repos oder des Laufs wie beim Holen der Commits."""
    try:
        github.objekt(f"repos/{fn}/commits/{kopf}")
        return True
    except github.GitHubFehler as e:
        if e.global_ or e.status not in (404, 422):
            raise
        print(f"{fn}: Kopf {kopf[:7]} des Erstimports nicht mehr abrufbar ({e.status}), der Import beginnt bei Seite 1")
        return False


def commit_zeile(fn, x):
    """Ein Eintrag der Commitliste als Zeile für commits; ein Eintrag ohne sha, Autordatum, Adresse oder Nachricht ist eine gescheiterte
    Abfrage des Repos (Codex-Audit 11.10.2026), nicht ein Commit weniger und kein Abbruch des ganzen Laufs."""
    try:
        k = x["commit"]
        zeile = (x["sha"], fn, k["author"]["date"], k["author"]["email"], (x.get("author") or {}).get("login"),
                 k["message"].split("\n", 1)[0][:120], len(x.get("parents") or []))
    except (KeyError, TypeError, AttributeError) as e:
        raise github.ungueltig(f"repos/{fn}/commits", "eine Liste von Commits", x) from e
    if not all(isinstance(v, str) for v in (zeile[0], zeile[2])):
        raise github.ungueltig(f"repos/{fn}/commits", "eine Liste von Commits mit sha und Datum", x)
    return zeile
UEBERLAPPUNG = timedelta(days=7)   # Commits werden über den sha nur einmal gespeichert; die Überlappung fängt spät gepushte ältere Commits


def werk(c, alles, ende=None):
    ende = db.frist() if ende is None else ende
    def frist(wo):
        """Frist abgelaufen: was geholt ist, festschreiben, dann mit lesbarer Meldung enden (keine Laufzeile, kein voller Erfolg)."""
        if time.monotonic() >= ende:
            c.commit()
            db.frist_pruefen(ende, wo)
    start = jetzt()
    merken(start=start, phase="holt Repos", fertig=None, exit=None, fehler=None, neu=None)
    token_messen()
    gelesen = list(github.alle_seiten("user/repos", {"affiliation": "owner", "per_page": 100}))
    # Typprüfung der Repo-Liste (Codex-Audit 11.10.2026): ein Eintrag ohne Name, ID oder Default-Branch ist eine gescheiterte Abfrage des Laufs,
    # nie ein Repo weniger; ohne das hätte die Karenz ein solches Repo still ausgeblendet.
    for r in gelesen:
        if not (isinstance(r.get("full_name"), str) and isinstance(r.get("id"), int) and isinstance(r.get("default_branch"), str)
                and isinstance(r.get("archived"), bool)):
            raise github.ungueltig("user/repos", "eine Liste von Repos mit full_name, id, default_branch und archived", r)
    alle = [r for r in gelesen if not r["archived"]]
    # Die Liste ist vollständig geholt: gesehen für ALLE gelisteten Repos auf den Laufzeitpunkt, sofort und in
    # einem Schritt, unabhängig vom Push-Fenster, das nur Commit- und Zweigabfragen begrenzt. Vorher stand das
    # Repo für Repo im Zweig-Durchlauf unten; brach der bei einem Repo ab, trugen die davor den neuen Stempel
    # und die danach den alten. Die Karenz in app/web.py rechnet gegen den letzten erfolgreichen Lauf aus laeufe.
    # Umbenennung auf GitHub: eine bekannte ID unter neuem Namen. Vor dem Stempeln in einer Transaktion in allen
    # Tabellen umbenennen, die den Namen tragen, sonst spaltet sich die Historie in alt (fällt nach der Karenz)
    # und neu (ohne Commits): so geschehen bei einer Umbenennung am 11.09.2026.
    bekannt = dict(c.execute("SELECT id, repo FROM repos WHERE id IS NOT NULL"))
    umziehen(c, [(bekannt[r["id"]], r["full_name"]) for r in alle if bekannt.get(r["id"]) not in (None, r["full_name"])])
    # Zeilen ohne ID aus der Zeit vor dieser Spalte, die nicht mehr gelistet sind: GitHub leitet den alten Namen auf
    # den neuen weiter (301, urllib folgt), die Antwort trägt full_name und id. Passt die ID zu einem gelisteten
    # Repo, ist es dieselbe Umbenennung; 404 heisst gelöscht und bleibt der Karenz überlassen.
    gelistet = {r["id"]: r["full_name"] for r in alle}
    for (alt,) in c.execute("SELECT repo FROM repos WHERE id IS NULL").fetchall():
        if alt in gelistet.values():
            continue
        try:
            j, _ = github.objekt(f"repos/{alt}", id=int)
        except github.GitHubFehler as e:
            if e.global_:
                raise
            continue
        if j["id"] in gelistet and gelistet[j["id"]] != alt:
            umbenennen(c, alt, gelistet[j["id"]])
    # Abrufstand je Repo (03.10.2026, Codex-Audit), gelesen nach den Umbenennungen, die ihn mitnehmen. Bis dahin galt für alle Repos ein
    # Fenster ab MAX(zeitstempel_utc) minus sieben Tage: ein einziger Commit mit Autordatum in der Zukunft schob es für alle Repos über
    # jetzt hinaus, und ein erstmals sichtbares älteres Repo wurde nie ganz gelesen. Jetzt: ohne Abrufstand oder mit anderem
    # Default-Branch als beim letzten Abruf ganz, sonst ab dem eigenen Stand minus UEBERLAPPUNG. Ein entarchiviertes oder wieder
    # sichtbares Repo setzt bei seinem Stand fort; ein abgewiesenes behält seinen, bis ein Abruf gelingt.
    stand = {zeile[0]: zeile[1:] for zeile in
             c.execute("SELECT repo, abgerufen_utc, abruf_branch, import_seite, import_start, import_kopf, import_id FROM repos")}
    def seit_von(r):
        """since für die Commitliste, None heisst ganz holen."""
        abg, br = stand.get(r["full_name"], (None,) * 6)[:2]
        if alles or not abg or br != r["default_branch"]:
            return None
        return utc_text(datetime.fromisoformat(abg.replace("Z", "+00:00")) - UEBERLAPPUNG)
    repos = [r for r in alle if seit_von(r) is None or (r["pushed_at"] and r["pushed_at"] >= seit_von(r))]
    c.executemany("INSERT INTO repos (repo, default_branch, sprache, pushed_at, gesehen, id, html_url) VALUES (?,?,?,?,?,?,?) "
                  "ON CONFLICT(repo) DO UPDATE SET default_branch = excluded.default_branch, sprache = excluded.sprache, "
                  "pushed_at = excluded.pushed_at, gesehen = excluded.gesehen, id = excluded.id, html_url = excluded.html_url",
                  [(r["full_name"], r["default_branch"], r["language"], r["pushed_at"], start, r["id"], r.get("html_url")) for r in alle])
    c.commit()
    # Fehler je Repo (403, 404, 451 …, Audit 25.09.2026): gespeichert in repos.fehler, das Repo ist für diesen Lauf übersprungen, die
    # übrigen laden weiter. Nur ein globaler Fehler (github.GitHubFehler.global_) beendet den Lauf. Vorher brach jeder Fehler alles ab.
    gesperrt = set()
    neu = 0

    def festschreiben(zeilen):
        """Eine Seite Commits als Upsert, sofort festgeschrieben: Betreffzeile und Zahl der Eltern werden auch für bekannte Commits
        nachgetragen (Erstbefüllung mit --alles). Liefert die Zahl der neuen Zeilen."""
        vorher = c.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
        c.executemany("INSERT INTO commits (sha, repo, zeitstempel_utc, autor_adresse, autor_login, betreff, eltern) VALUES (?,?,?,?,?,?,?) "
                      "ON CONFLICT(sha) DO UPDATE SET betreff = excluded.betreff, eltern = excluded.eltern", zeilen)
        c.commit()
        return c.execute("SELECT COUNT(*) FROM commits").fetchone()[0] - vorher

    # Je Seite festgeschrieben und der Abrufstand je Repo direkt nach dessen vollständigem Abruf (10.10.2026, Audit B4). Vorher kam der
    # Stand erst am Ende des Gesamtlaufs, und die Frist galt nur vor jedem Repo: ein Fristabbruch liess auch fertig geholte Repos ohne
    # Stand, ein Vollabruf neuer Repos begann Lauf für Lauf von vorn und erreichte Zweige, CI und Sicherheit nie. Jetzt prüft der Lauf
    # die Frist vor jeder weiteren Seite (nach PRO_SEITE Commits fordert der Generator die nächste an), ein Abbruch verliert nichts schon
    # Geholtes, und der nächste Lauf setzt beim ersten Repo ohne neuen Stand fort.
    # Erstimport in Teilen (Codex-Audit 11.10.2026, Befund 3): ein Repo ohne Abrufstand wird ganz geholt, aber Seite für Seite fortsetzbar.
    # Jede fertige Seite schreibt repos.import_seite fest, der nächste Lauf beginnt dort; der Abrufstand ist am Ende der Beginn des ersten
    # Teils (import_start), damit die Überlappung auch Commits fängt, die während des Imports kamen. Ein Teil endet nach seiner ersten Seite,
    # sobald IMPORT_ANTEIL der Frist verbraucht ist: die übrigen Repos, Zweige, CI und Sicherheit kommen in jedem Lauf dran. Vorher begann
    # der Import jedes Mal bei Seite 1, und bei 31 s je Seite blieb ein Konto Lauf für Lauf bei 2000 Commits ohne jede Prüfung stehen.
    # Neue Commits vorn verschieben ältere auf spätere Seiten: die Fortsetzung sieht dann einige doppelt (über sha idempotent), nie eine Lücke.
    import_bis = time.monotonic() + max(0.0, ende - time.monotonic()) * IMPORT_ANTEIL
    for r in sorted(repos, key=lambda r: seit_von(r) is None):   # die kurzen Abrufe zuerst, die Importe danach
        fn = r["full_name"]
        frist(f"beim Holen der Commits von {fn}")
        merken(phase=f"holt {fn}")
        seit = seit_von(r)
        _, br_alt, seite_alt, istart, kopf, iid = stand.get(fn, (None,) * 6)
        # Die Fortsetzung gilt nur für dasselbe Repo (GitHub-ID) und denselben Kopf (Bestätigungslauf zum Codex-Audit, 11.10.2026): der Import
        # blättert gegen den beim Start festgehaltenen Kopf (sha der Commitliste), seine Seiten verschieben sich damit nicht. Ist der Kopf
        # nicht mehr abrufbar (Force-Push) oder ist es ein anderes Repo unter demselben Namen (gelöscht und neu angelegt), beginnt der Import
        # bei Seite 1; die schon geholten Commits sind über den sha idempotent. Vorher setzte er bei der alten Seitenzahl in einer anderen
        # Liste fort und übersprang Commits dauerhaft.
        erste, fortsetzen = 1, seit is None and not alles and br_alt == r["default_branch"] and seite_alt and istart and kopf and iid == r["id"]
        try:
            fortsetzen = fortsetzen and kopf_da(fn, kopf)
        except github.GitHubFehler as e:   # wie ein Fehler beim Holen der Commits: global beendet den Lauf, sonst ist nur dieses Repo übersprungen
            if e.global_:
                raise
            gesperrt.add(fn)
            repo_fehler(c, fn, e, start)
            continue
        if fortsetzen:
            erste = seite_alt
        elif seit is None:
            istart, kopf = start, None
            c.execute("UPDATE repos SET abgerufen_utc = NULL, abruf_branch = ?, import_seite = 1, import_start = ?, import_kopf = NULL, import_id = ? "
                      "WHERE repo = ?", (r["default_branch"], start, r["id"], fn))
            c.commit()
        # Ein neuer Default-Branch wird ganz geholt, die Commits des alten bleiben und zählen weiter (bekannte Grenze). Die Neuzuordnung aus
        # dem Audit (S4, 65f47a1) ist am 10.10.2026 zurückgenommen: ein sha gehört nur einem Repo (S1, nicht gebaut), sie löschte damit einen
        # Commit, den ein zweites Repo auf seinem Default-Branch trägt, und ein Abbruch mitten im Wechsel liess einen gemischten Bestand.
        puffer, n, abgerufen, seite, fertig = [], 0, 0, erste, True
        try:
            # Ein neuer Import holt Seite 1 ohne sha und hält ihren ersten Commit als Kopf fest; danach blättert er mit sha=Kopf weiter.
            weiter = True
            while weiter:
                weiter, gepinnt = False, bool(seit or kopf)
                params = {"per_page": PRO_SEITE, "page": seite} | ({"since": seit} if seit else {}) | ({"sha": kopf} if not seit and kopf else {})
                for x in github.alle_seiten(f"repos/{fn}/commits", params, leer_bei_409=True):
                    zeile = commit_zeile(fn, x)
                    if not seit and not kopf:
                        kopf = zeile[0]
                        c.execute("UPDATE repos SET import_kopf = ? WHERE repo = ?", (kopf, fn))
                    puffer.append(zeile)
                    if len(puffer) == PRO_SEITE:
                        n, abgerufen, puffer, seite = n + festschreiben(puffer), abgerufen + len(puffer), [], seite + 1
                        if seit is None:
                            c.execute("UPDATE repos SET import_seite = ? WHERE repo = ?", (seite, fn))
                            c.commit()
                        frist(f"vor der nächsten Seite der Commits von {fn}")
                        if seit is None and time.monotonic() >= import_bis:
                            fertig = False
                            break
                        if not gepinnt:   # Seite 1 war ohne sha: ab Seite 2 gegen den festgehaltenen Kopf
                            weiter = True
                            break
        except github.GitHubFehler as e:
            if e.global_:
                raise
            gesperrt.add(fn)
            repo_fehler(c, fn, e, start)
            neu += n
            continue
        n, abgerufen = n + festschreiben(puffer), abgerufen + len(puffer)
        neu += n
        if not fertig:
            print(f"{fn}: {abgerufen} abgerufen, {n} neu, Erstimport bis Seite {seite - 1}, der nächste Lauf setzt bei Seite {seite} fort")
            continue
        c.execute("UPDATE repos SET abgerufen_utc = ?, abruf_branch = ?, import_seite = NULL, import_start = NULL, import_kopf = NULL, import_id = NULL WHERE repo = ?",
                  (istart if seit is None else start, r["default_branch"], fn))
        c.commit()
        print(f"{fn}: {abgerufen} abgerufen, {n} neu" + ("" if seit else ", ganz geholt"))
    # Repo-Metadaten und Zweige, für alle nicht archivierten Repos (auch ohne Push im Fenster).
    merken(phase="holt Zweige")
    c.commit()   # die Commits sind geholt; ein globaler Fehler bei den Zweigen verwirft sie nicht mehr
    # Zweigköpfe (03.10.2026): letzter_utc gilt für einen Kopf-sha. Ein neuer Kopf bekommt min(Autordatum, jetzt), ein unveränderter
    # behält seinen Wert ohne neuen Abruf. Vorher hielt ein Zweig mit Autordatum in der Zukunft die Frist von drei Tagen entsprechend
    # lange auf (Codex-Sammellauf).
    koepfe = {(repo, name): (sha, letzter) for repo, name, sha, letzter in c.execute("SELECT repo, name, kopf_sha, letzter_utc FROM zweige")}
    # Zweige und Prüfungen zuerst für die Repos, die am längsten nicht geprüft sind (10.10.2026): endet ein Lauf am Ratenlimit, beginnt der
    # nächste bei den übrigen statt wieder vorn, und auch ein Konto mit hunderten Repos kommt Lauf für Lauf durch.
    geprueft = dict(c.execute("SELECT repo, gesehen FROM pruefungen"))
    alle = sorted(alle, key=lambda r: geprueft.get(r["full_name"], ""))
    for r in alle:
        if r["full_name"] in gesperrt:
            continue
        frist(f"beim Holen der Zweige von {r['full_name']}")
        try:
            zweige = []
            for z in github.alle_seiten(f"repos/{r['full_name']}/branches", {"per_page": 100}):
                sha = (z.get("commit") or {}).get("sha") if isinstance(z.get("commit"), dict) else None
                if not isinstance(z.get("name"), str) or not isinstance(sha, str):
                    raise github.ungueltig(f"repos/{r['full_name']}/branches", "eine Liste von Zweigen mit name und commit.sha", z)
                if z["name"] == r["default_branch"]:
                    continue
                # Zweignamen kodiert (Audit S5): «fix#42» schnitt den Pfad sonst am «#» ab; der Schrägstrich bleibt, GitHub erwartet ihn so.
                v, _ = github.objekt(f"repos/{r['full_name']}/compare/{quote(r['default_branch'], safe='/')}...{quote(z['name'], safe='/')}",
                                     ahead_by=int, behind_by=int)
                alt_sha, letzter = koepfe.get((r["full_name"], z["name"]), (None, None))
                if sha != alt_sha or not letzter:
                    kopf, _ = github.objekt(f"repos/{r['full_name']}/commits/{sha}", commit=dict)
                    datum_ = (kopf["commit"].get("author") or {}).get("date")
                    if not isinstance(datum_, str):
                        raise github.ungueltig(f"repos/{r['full_name']}/commits/{sha}", "ein Commit mit commit.author.date", kopf["commit"])
                    letzter = min(datum_, jetzt())
                zweige.append((r["full_name"], z["name"], v["ahead_by"], v["behind_by"], letzter, sha))
        except github.GitHubFehler as e:
            if e.global_:
                raise
            gesperrt.add(r["full_name"])
            repo_fehler(c, r["full_name"], e, start)
            continue   # die Zweige des letzten gelungenen Laufs bleiben stehen
        c.execute("DELETE FROM zweige WHERE repo = ?", (r["full_name"],))
        c.executemany("INSERT INTO zweige (repo, name, ahead, behind, letzter_utc, kopf_sha) VALUES (?,?,?,?,?,?)", zweige)
    # CI und Sicherheitswarnungen je gelistetem Repo; jede Abfrage fängt ihre Fehler selbst.
    merken(phase="prüft CI und Sicherheit")
    for r in alle:
        frist(f"beim Prüfen von {r['full_name']}")
        pruefen(c, r, start)
    c.executemany("UPDATE repos SET fehler = NULL, fehler_utc = NULL WHERE repo = ?", [(r["full_name"],) for r in alle if r["full_name"] not in gesperrt])
    c.commit()   # das Werk zuerst festschreiben, die Buchführung danach
    laufzeile(c, start, neu, gesperrte_zahl(c, start))
    # Codezeilen nach der Laufzeile: das Nachladen des Altbestands verlängert den Lauf, darf aber weder die Karenz noch das Alter
    # der Daten verschieben, und ein Fehler darin reisst nichts mit.
    merken(phase="lädt Codezeilen")
    fehler = None
    try:
        # Das Nachladen bekommt höchstens die Restzeit der Frist abzüglich 20 s für den Abschluss.
        geladen, rest, fehler = zeilen_nachladen(c, frist_s=min(ZEILEN_FRIST_S, ende - time.monotonic() - 20))
        print(f"Codezeilen: {geladen} geladen, {rest} offen")
        # Ein Repo, das erst beim Nachladen abgewiesen wurde, macht den Lauf nachträglich zum Teilerfolg.
        c.execute("UPDATE laeufe SET teilweise = ? WHERE gestartet_utc = ?", (gesperrte_zahl(c, start), start))
        c.commit()
    except Exception as e:   # noqa: BLE001
        c.rollback()
        traceback.print_exc()
        fehler = f"Codezeilen: {type(e).__name__}: {e}"
    # Ein Abbruch beim Nachladen steht im Laufstatus (Audit O1), der Lauf bleibt ein Erfolg: Commits, Zweige und Prüfungen sind geschrieben.
    merken(phase=None, fertig=jetzt(), exit=0, neu=neu, teilweise=gesperrte_zahl(c, start), fehler=fehler)
    gesamt = c.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
    zweige = c.execute("SELECT COUNT(*) FROM zweige").fetchone()[0]
    print(f"{len(repos)} Repos geholt, {len(alle)} Repos gesamt, {neu} neu, {gesamt} Zeilen gesamt, {zweige} Nebenzweige")


if __name__ == "__main__":
    beginn = jetzt()
    try:
        main(alles="--alles" in sys.argv)
    except db.FristAbgelaufen as e:   # Warten oder Werk über der Frist: lesbar enden, Exit 1, ohne Traceback
        merken(start=_lauf.get("start") or beginn, phase=None, fertig=jetzt(), exit=1, fehler=str(e))
        print(f"gitpuls: {e}", flush=True)
        sys.exit(1)
    except BaseException as e:   # GitHubFehler ist SystemExit; auch ein Abbruch hinterlässt Ende und Exit
        merken(start=_lauf.get("start") or beginn, phase=None, fertig=jetzt(), exit=1, fehler=str(e) or type(e).__name__)
        raise
