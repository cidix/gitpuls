"""Kleinste Prüfung: der PAT kommt aus der Datei, und ein gesetzter Pfad scheitert LAUT. python3 -m tests.test_github"""
import os
import tempfile

os.environ["TZ"] = "Europe/Zurich"   # die Zone setzt der Test selbst (10.10.2026, Audit S28): grün unter jeder TZ der Shell
from app import github

ordner = tempfile.mkdtemp()
datei = os.path.join(ordner, "github.env")
os.environ.pop("GITHUB_TOKEN", None)
os.environ.pop(github.TOKENPFAD, None)


def scheitert(erwartet):
    try:
        github._token()
    except github.GitHubFehler as e:
        assert erwartet in str(e), f"{erwartet!r} nicht in {e!r}"
        return
    raise AssertionError(f"kein Abbruch, erwartet war {erwartet!r}")


# Ohne alles: die alte Meldung, damit der Mac-Aufruf unverändert bleibt
scheitert("GITHUB_TOKEN fehlt")

# Umgebung hat Vorrang, auch wenn ein Pfad danebensteht (Probieren ohne Datei)
os.environ["GITHUB_TOKEN"] = "aus-der-umgebung"
os.environ[github.TOKENPFAD] = datei
assert github._token() == "aus-der-umgebung"
del os.environ["GITHUB_TOKEN"]

# DER GESETZTE PFAD IST DIE ERWARTUNG: fehlende Datei ist ein Abbruch mit Pfad,
# kein stiller Lauf ohne Token. Genau dafür steht die Änderung vom 08.09.2026.
scheitert(datei)

# Datei ohne Wert zählt als fehlend, nicht als leeres Token
with open(datei, "w", encoding="utf-8") as f:
    f.write("# nur ein Kommentar\nGITHUB_TOKEN=\n")
scheitert("ohne GITHUB_TOKEN")

# Unlesbar ist ebenfalls laut — der Fall 0600 root gegen UID 10001 im Container
with open(datei, "w", encoding="utf-8") as f:
    f.write('GITHUB_TOKEN="ghp_ausderdatei"\n')
os.chmod(datei, 0o000)
if os.getuid() != 0:  # als root ist jede Datei lesbar, dann sagt der Fall nichts
    scheitert("nicht lesbar")
os.chmod(datei, 0o600)

assert github._token() == "ghp_ausderdatei"

# 409 (03.10.2026, Codex-Audit): nur die Commitliste eines leeren Repos ist dann leer; für jede andere Liste ist 409 ein Fehler des
# Repos, sonst hätte eine Fehlerantwort als gemessen leere Zweig-, CI- oder Warnungsliste den alten Stand ersetzt.
import email.message  # noqa: E402
import io  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402


def antwort_409(req, timeout=None):
    raise urllib.error.HTTPError(req.full_url, 409, "Conflict", email.message.Message(), io.BytesIO(b'{"message": "Git Repository is empty."}'))


echt_urlopen, urllib.request.urlopen = urllib.request.urlopen, antwort_409
try:
    assert github.holen("repos/fernwerk-studio/leer/commits", {"per_page": 100}, leer_bei_409=True)[0] == []
    assert list(github.alle_seiten("repos/fernwerk-studio/leer/commits", {"per_page": 100}, leer_bei_409=True)) == []
    for pfad in ("repos/fernwerk-studio/leer/branches", "repos/fernwerk-studio/leer/actions/runs", "repos/fernwerk-studio/leer/dependabot/alerts"):
        try:
            github.holen(pfad)
        except github.GitHubFehler as e:
            assert e.status == 409 and not e.global_ and "empty" in e.meldung, (pfad, e.status, e.meldung)
        else:
            raise AssertionError(f"409 auf {pfad} als leere Liste übernommen")
    try:
        list(github.alle_seiten("repos/fernwerk-studio/leer/branches", {"per_page": 100}))
        raise AssertionError("409 auf branches über alle_seiten als leer übernommen")
    except github.GitHubFehler as e:
        assert e.status == 409
finally:
    urllib.request.urlopen = echt_urlopen
print("ok: Umgebung vor Datei, gesetzter Pfad scheitert laut mit Pfad im Text; 409 nur bei der Commitliste leer")
