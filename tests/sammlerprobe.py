"""Ein Sammellauf mit nachgebautem GitHub. user/repos kommt wie in app/github.py::alle_seiten als Generator
seitenweise (seiten Seiten, gleich gross), wahlweise mit GitHubFehler nach Seite fehler_nach_seite; Commits und
Zweige sind leer, ausser bei fehler_bei, wo der Zweigabruf des genannten Repos scheitert und der Lauf abbricht.
stempel_vorzeitig stempelt gesehen schon beim Lesen jeder Seite, wie es ein streamender Sammler täte: nur für
die Gegenprobe, dass der Abbruchtest so rot wird. Für die Karenzprüfungen in test_web und test_sammler; die
Zeit des Laufs kommt von aussen."""
import zlib

from app import db, github, sammler


def lauf(zeit, gelistet, fehler_bei=None, pushed=None, seiten=1, fehler_nach_seite=None, stempel_vorzeitig=False, ids=None, weiterleitung=None):
    """ids: Name -> GitHub-ID; ohne Angabe eine aus dem Namen abgeleitete Zahl, eine Umbenennung braucht die ID ausdrücklich.
    weiterleitung: alter Name -> (neuer Name, id), wie GitHub einen umbenannten Namen per 301 beantwortet."""
    pushed, ids, weiterleitung = pushed or {}, ids or {}, weiterleitung or {}
    groesse = -(-len(gelistet) // seiten) or 1

    def user_repos():
        for nr in range(seiten):
            seite = gelistet[nr * groesse:(nr + 1) * groesse]
            if stempel_vorzeitig:
                c = db.verbinden(schreibend=True)
                c.executemany("UPDATE repos SET gesehen = ? WHERE repo = ?", [(zeit, n) for n in seite])
                c.commit(); c.close()
            yield from [{"full_name": n, "id": ids.get(n, zlib.crc32(n.encode())), "archived": False, "pushed_at": pushed.get(n, "2026-01-01T00:00:00Z"),
                         "default_branch": "main", "language": "Python"} for n in seite]
            if fehler_nach_seite == nr + 1:
                raise github.GitHubFehler(f"nachgebaut: user/repos Seite {nr + 2} antwortet 502")

    def alle_seiten(pfad, params=None, leer_bei_409=False):
        if pfad == "user/repos":
            return user_repos()
        if fehler_bei and pfad == f"repos/{fehler_bei}/branches":
            return iter([{"name": "wip", "commit": {"sha": "abc"}}])
        return iter([])

    def holen(pfad, params=None, leer_bei_409=False):
        if pfad.startswith("repos/") and pfad.count("/") == 2 and pfad[6:] in weiterleitung:
            neu, nr = weiterleitung[pfad[6:]]
            return {"full_name": neu, "id": nr}, {}
        if "/dependabot/" in pfad:   # keine offenen Warnungen
            return [], {}
        if "/actions/" in pfad:   # kein Actions-Lauf: GitHub antwortet 200 mit leerer Liste (gemessen live am 03.10.2026, 7 Repos); ein 404 wäre
            return {"total_count": 0, "workflow_runs": []}, {}   # seit dem Messvertrag vom 03.10.2026 ein Fehler, keine Messung
        raise github.GitHubFehler(f"nachgebaut: {pfad} antwortet 502")

    alt = github.alle_seiten, github.holen, sammler.jetzt
    github.alle_seiten, github.holen, sammler.jetzt = alle_seiten, holen, lambda: zeit
    try:
        sammler.main()
        return True
    except SystemExit:
        return False
    finally:
        github.alle_seiten, github.holen, sammler.jetzt = alt
