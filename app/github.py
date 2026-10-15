"""GitHub-REST über urllib. Token aus einer Datei oder GITHUB_TOKEN, nie geloggt."""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

API = "https://api.github.com"

# WO DER PAT LIEGT — der PFAD, nicht der Wert. Bis zum 08.09.2026 stand der
# Token als GITHUB_TOKEN in der Umgebung des Containers, und `docker inspect`
# wie /proc/1/environ gaben ihn im Klartext aus: der offene rote Punkt aus dem
# Server-Audit. Das Muster stammt aus einem anderen eigenen Dienst und ist dort
# belegt.
#
# DER GESETZTE PFAD IST DIE ERWARTUNG. Ist GITPULS_GITHUB_ENV gesetzt, wird die
# Datei erwartet: fehlt sie, ist sie unlesbar oder trägt sie kein GITHUB_TOKEN,
# bricht der Lauf mit dem Pfad im Text ab — kein stiller Lauf ohne Token.
# Ohne die Variable (Mac, Tests) gilt weiter GITHUB_TOKEN aus der Umgebung.
TOKENPFAD = "GITPULS_GITHUB_ENV"


class GitHubFehler(SystemExit):
    """Abbruch eines Laufs. status und meldung (seit 25.09.2026) tragen HTTP-Code und GitHubs message, damit der Sammler
    «kein Leserecht» (403, «Resource not accessible») von «ausgeschaltet» (403, «… disabled …») unterscheiden kann.
    global_ (Audit 25.09.2026): der Fehler betrifft jeden Aufruf, nicht nur ein Repo, und beendet den Lauf. Das sind Netz, fehlender
    Token, 401 und das Ratenlimit (403 oder 429 mit «rate limit» oder X-RateLimit-Remaining 0); jeder andere HTTP-Fehler betrifft nur
    das Repo der Anfrage, und der Sammler lädt die übrigen weiter."""
    status, meldung, global_ = None, "", True


def _token():
    # Umgebung hat Vorrang: so lässt
    # sich ohne Datei probieren.
    if t := os.environ.get("GITHUB_TOKEN", "").strip():
        return t
    pfad = os.environ.get(TOKENPFAD)
    if not pfad:
        raise GitHubFehler("GITHUB_TOKEN fehlt")
    try:
        text = Path(pfad).read_text(encoding="utf-8")
    except OSError as exc:
        # Fehlt und unlesbar in einem Zweig: der Errno im Text sagt, welches
        # von beiden. Zwei Zweige mit zwei Sätzen sagten nichts mehr.
        raise GitHubFehler(f"Tokendatei nicht lesbar: {pfad} ({exc})") from exc
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if not zeile.startswith("GITHUB_TOKEN="):
            continue
        if wert := zeile.split("=", 1)[1].strip().strip('"').strip("'"):
            return wert
    raise GitHubFehler(f"Tokendatei ohne GITHUB_TOKEN: {pfad}")


# Warten am Ratenlimit (10.10.2026): der Sammler setzt hier das monotone Ende seiner Frist. Erreicht eine Anfrage das Limit (403 oder 429 mit
# X-RateLimit-Remaining 0, Retry-After oder «rate limit»), wartet holen bis zum Reset und fragt erneut, solange das mit RESERVE_S Luft in die
# Frist passt; sonst endet der Lauf mit dem globalen Fehler, das Geholte ist festgeschrieben, und der nächste Lauf setzt fort. Ohne Frist
# (Sternlauf, Tests) wird nicht gewartet. Vorher endete jeder Lauf am Limit, und ein Konto mit rund 5000 Anfragen wurde nie fertig.
FRIST_ENDE = None
RESERVE_S = 30


def wartezeit(kopf):
    """Sekunden bis zum Ende des Limits aus Retry-After oder X-RateLimit-Reset, None ohne Angabe."""
    kopf = kopf or {}
    if (retry := str(kopf.get("Retry-After") or "")).isdigit():
        return int(retry)
    if (reset := str(kopf.get("X-RateLimit-Reset") or "")).isdigit():
        return max(0.0, int(reset) - time.time()) + 1
    return None


def holen(pfad, params=None, leer_bei_409=False):
    """Eine Seite. Liefert (json, headers). Mit `leer_bei_409` heisst 409 «leeres Repo» -> ([], headers); seit 03.10.2026 nur noch für die
    Commitliste (Codex-Audit): eine andere Liste hätte eine Fehlerantwort als gemessen leer übernommen und den alten Stand ersetzt."""
    url = f"{API}/{pfad}"
    if params:
        # Kodiert (10.10.2026, Audit S5): ein Zweig «release&x=1» als branch ergab vorher zwei Parameter. Pfadteile kodiert der Aufrufer.
        url += "?" + urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "gitpuls",
    })
    while True:
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r), r.headers
        except urllib.error.HTTPError as e:
            if e.code == 409 and leer_bei_409:   # leerer Default-Branch, kein Fehler
                return [], e.headers
            try:
                meldung = json.loads(e.read().decode() or "{}").get("message", "")
            except (ValueError, AttributeError):
                meldung = ""
            kopf = e.headers or {}
            limit = e.code in (403, 429) and (kopf.get("X-RateLimit-Remaining") == "0" or kopf.get("Retry-After") or "rate limit" in meldung.lower())
            warten = wartezeit(kopf) if limit else None
            if warten is not None and FRIST_ENDE is not None and time.monotonic() + warten < FRIST_ENDE - RESERVE_S:
                print(f"Ratenlimit erreicht, warte {warten:.0f} s bis zum Reset", flush=True)
                time.sleep(warten)
                continue
            fehler = GitHubFehler(f"GitHub {e.code} auf {pfad}: {e.reason}")
            fehler.status, fehler.meldung = e.code, meldung
            fehler.global_ = e.code in (401, 429) or limit
            raise fehler
        except urllib.error.URLError as e:
            raise GitHubFehler(f"GitHub nicht erreichbar: {e.reason}")


def ungueltig(pfad, was, daten):
    """Eine Antwort mit 200, deren Typ nicht passt (null, {}, "", false, 0 statt Liste oder Objekt; Codex-Audit 11.10.2026, Befund 1): eine
    gescheiterte Abfrage des Repos, nie «gemessen leer». Vorher wurde aus `j or []` eine leere Warnungsliste, und bekannte Warnungen fielen weg."""
    fehler = GitHubFehler(f"GitHub-Antwort auf {pfad} ist {was}, nicht {type(daten).__name__}")
    fehler.status, fehler.meldung, fehler.global_ = 200, f"Antwort ist {type(daten).__name__} statt {was}", False
    return fehler


def liste(pfad, params=None, leer_bei_409=False):
    """Eine Seite, die eine Liste von Objekten sein muss: (liste, headers), sonst GitHubFehler des Repos."""
    daten, kopf = holen(pfad, params, leer_bei_409)
    if not isinstance(daten, list) or not all(isinstance(x, dict) for x in daten):
        raise ungueltig(pfad, "eine Liste von Objekten", daten)
    return daten, kopf


def objekt(pfad, params=None, **felder):
    """Eine Antwort, die ein Objekt sein muss, mit den Feldern und Typen aus `felder` (Name=Typ): (objekt, headers), sonst GitHubFehler des Repos."""
    daten, kopf = holen(pfad, params)
    if not isinstance(daten, dict) or any(not isinstance(daten.get(k), typ) or isinstance(daten.get(k), bool) and typ is int for k, typ in felder.items()):
        raise ungueltig(pfad, "ein Objekt" + (f" mit {', '.join(felder)}" if felder else ""), daten)
    return daten, kopf


def alle_seiten(pfad, params, leer_bei_409=False):
    """Folgt dem Link-Header. per_page setzt der Aufrufer, params["page"] die erste Seite (Vorgabe 1, Fortsetzung des Erstimports)."""
    seite = params.get("page", 1)
    while True:
        daten, kopf = liste(pfad, {**params, "page": seite}, leer_bei_409)
        yield from daten
        if 'rel="next"' not in (kopf.get("Link") or ""):
            return
        seite += 1
