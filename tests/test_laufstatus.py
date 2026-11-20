"""Laufstatus eines Dienstes (20.11.2026): tools/server-stand liest die Antwort von "lauf_status" und schreibt nur Ergebnis und
Zahlen nach server.json, die Seite zeigt «unvollständig» als Warnung. Jeder Grenzfall mit gesperrtem Netz: kein Test erreicht eine Adresse,
die Antworten kommen aus einer nachgebauten Öffnerfunktion. python3 -m tests.test_laufstatus"""
import io
import json
import socket
import urllib.error

from tests.test_audit import serverstand, umgebung

# Netz gesperrt, auf der Ebene der Verbindung: jeder Versuch fällt auf und wird gezählt.
VERSUCHE = []


def _gesperrt(*args, **kwargs):
    VERSUCHE.append(args)
    raise ConnectionRefusedError("Netz im Test gesperrt")


socket.socket.connect = _gesperrt
socket.create_connection = _gesperrt

db, web = umgebung()
ss = serverstand()


class Antwort(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def mit(roh):
    """Öffnerfunktion, die roh als Antwortkörper liefert (bytes) oder die Ausnahme wirft."""
    def oeffnen(anfrage, timeout):
        assert timeout == ss.LAUF_FRIST_S and anfrage.get_header("User-agent") == "gitpuls-server-stand", (timeout, anfrage.header_items())
        if isinstance(roh, BaseException):
            raise roh
        return Antwort(roh if isinstance(roh, bytes) else json.dumps(roh).encode())
    return oeffnen


URL = "http://127.0.0.1:8787/api/lauf"
# Die am 20.11.2026 auf dem VPS gemessene Form (Stand ce34f37 des Dienstes), mit erfundenen Werten unter .example.
FORM = {"aktiv": False, "seit": None, "angefordert": None, "site": None, "fertig": 48, "gesamt": 48, "zeilen": ["z"], "ergebnis": "fertig",
        "nicht_erreicht": [], "endpoint_fehlgeschlagen": [], "nicht_eingelesen": [], "revision": "x", "hinweis": None}
lauf = lambda **k: ss.lauf_status(URL, mit({**FORM, **k}))
ng = lambda erg: erg["ergebnis"] == "nicht gemessen" and isinstance(erg["grund"], str) and set(erg) == {"ergebnis", "grund"}

# fertig, übersprungen, abgebrochen: Ergebnis und Zahlen, nie die Domains
assert lauf() == {"ergebnis": "fertig", "betroffen": 0, "gesamt": 48}, lauf()
assert lauf(ergebnis="übersprungen") == {"ergebnis": "übersprungen", "betroffen": 0, "gesamt": 48}
assert lauf(ergebnis="abgebrochen", fertig=12) == {"ergebnis": "abgebrochen", "betroffen": 0, "gesamt": 48}
# unvollständig mit Überschneidung: a in zwei Listen, b in zwei Listen, c einmal: drei verschiedene Sites
u = lauf(ergebnis="unvollständig", fertig=45, nicht_erreicht=["a.example", "b.example"], nicht_eingelesen=["b.example", "c.example"],
         endpoint_fehlgeschlagen=["a.example"])
assert u == {"ergebnis": "unvollständig", "betroffen": 3, "gesamt": 48} and ".example" not in json.dumps(u), u
# laufend: gemessen, ohne Ergebnis
assert lauf(aktiv=True, ergebnis=None) == {"aktiv": True}
# alles andere ist nicht gemessen, nie sauber
assert ng(ss.lauf_status(URL, mit(b""))), "leere Antwort"
assert ng(ss.lauf_status(URL, mit(b"null"))) and ng(ss.lauf_status(URL, mit([FORM]))), "keine Objektantwort"
assert ng(ss.lauf_status(URL, mit({k: v for k, v in FORM.items() if k != "gesamt"}))), "fehlendes Feld"
assert ng(lauf(nicht_erreicht=None)) and ng(lauf(gesamt=None)) and ng(lauf(aktiv=None)), "null-Feld"
assert ng(lauf(gesamt="48")) and ng(lauf(gesamt=True)) and ng(lauf(fertig=-1)) and ng(lauf(nicht_erreicht=[1])) and ng(lauf(aktiv="nein")), "falscher Typ"
assert ng(lauf(ergebnis="teilweise")) and ng(lauf(ergebnis=None)) and ng(lauf(aktiv=True, ergebnis="fertig")), "unbekanntes Ergebnis"
assert ng(ss.lauf_status(URL, mit(b"x" * (ss.LAUF_MAX_BYTES + 1)))), "über der Grössengrenze"
assert ng(ss.lauf_status("file:///etc/passwd", mit(FORM))), "nur http(s)"
fehler = {"Verbindungsfehler": urllib.error.URLError(ConnectionRefusedError()), "Zeitüberschreitung": TimeoutError(),
          "Zeitüberschreitung im Abruf": urllib.error.URLError(TimeoutError()), "HTTP 503": urllib.error.HTTPError(URL, 503, "x", {}, None),
          "Weiterleitung": urllib.error.HTTPError(URL, 302, "x", {}, None), "Abbruch": ConnectionResetError()}
for name, e in fehler.items():
    erg = ss.lauf_status(URL, mit(e))
    assert ng(erg), (name, erg)
assert "keine Antwort nach 5 s" in ss.lauf_status(URL, mit(TimeoutError()))["grund"] and ss.lauf_status(URL, mit(fehler["HTTP 503"]))["grund"] == "Laufstatus: HTTP 503"
# Der echte Öffner folgt keiner Weiterleitung, und mit gesperrtem Netz endet er als nicht gemessen statt mit einer Ausnahme.
assert ss._OhneWeiterleitung().redirect_request(None, None, 302, "Found", {}, "http://anderswo.example/") is None
assert ng(ss.lauf_status(URL)) and VERSUCHE, "der echte Öffner trifft die Sperre"

# Diensttabelle: ohne das Feld bleibt der Eintrag unverändert, mit Feld steht "lauf"; ein Feld, das kein Text ist, macht den Eintrag ungültig.
ss.container = lambda n: {"name": n, "status": "running"}
ss.timer = lambda u_: {"unit": u_, "status": "inactive"}
ss.lauf_status = lambda url: {"ergebnis": "fertig", "betroffen": 0, "gesamt": 1}
basis = {"name": "Portal", "container": ["c-1"], "timer": ["c-lauf"]}
ohne = ss.dienst_eintrag(0, basis, {}, None)
mit_feld = ss.dienst_eintrag(0, {**basis, "lauf_status": URL}, {}, None)
assert "lauf" not in ohne and {k: v for k, v in mit_feld.items() if k != "lauf"} == ohne and mit_feld["lauf"]["ergebnis"] == "fertig", mit_feld
assert "fehler" in ss.dienst_eintrag(0, {**basis, "lauf_status": 8787}, {}, None)

# Seite: Dienst mit Timer, Exit 0. «unvollständig» als Warnung in Zeile, Lagesatz und Detail; Exit ungleich 0 geht vor; fertig, übersprungen,
# abgebrochen und laufend ändern nichts; nicht gemessen ist grau und nie sauber; ohne Feld wie bisher.
from datetime import datetime, timezone  # noqa: E402
jetzt = datetime(2026, 11, 20, 20, 0, tzinfo=timezone.utc)
timer_ = {"unit": "portal-volllauf", "status": "inactive", "timer_aktiv": "active", "gestartet_utc": "2026-11-20T19:30:00Z",
          "letzter_utc": "2026-11-20T19:46:00Z", "ergebnis": "success", "exit": 0, "naechster_utc": "2026-11-20T21:30:00Z"}
dienst = lambda **k: {"name": "Kundenportal", "zweck": "", "link": "https://host.example/", "repo": None, "checkout": "/srv/kundenportal",
                      "zeilen": [], "container": [{"name": "kundenportal-web-1", "status": "running", "seit_utc": "2026-11-18T00:00:00Z",
                                                  "bild": "portal:1", "neustarts": 0, "gesund": None}], "timer": [dict(timer_)], "deploy": None, **k}
stand = lambda d: {"erhoben_utc": "2026-11-20T19:58:00Z", "maschine": {}, "zustand": {}, "installiert": {}, "firewall": None, "dienste": [d], "ungemessen": {}}
html = lambda d: web.server_dienste_block(stand(d), jetzt)
satz = lambda d: web.server_lage(stand(d))[0]
TEXT = "letzter Lauf unvollständig, 3 von 48 Sites nicht erhoben"

d_u = dienst(lauf={"ergebnis": "unvollständig", "betroffen": 3, "gesamt": 48})
h = html(d_u)
assert '<i class="punkt glut"' in h and '<span class="palter dzustand"><span class="glut">unvollständig</span></span>' in h, h[:600]
assert f'<div class="dz"><span class="dk">Ergebnis</span><span class="glut">{TEXT}</span></div>' in h, h
assert satz(d_u).startswith(f"Kundenportal: {TEXT}.") and "sauber" not in satz(d_u), satz(d_u)
d_exit = dienst(lauf={"ergebnis": "unvollständig", "betroffen": 3, "gesamt": 48}, timer=[{**timer_, "ergebnis": "exit-code", "exit": 1}])
assert '<span class="glut">Fehler</span>' in html(d_exit) and ">unvollständig<" not in html(d_exit), "Exit ungleich 0 bleibt Fehler und geht vor"
gleich = html(dienst())
assert ">sauber<" in gleich and "Ergebnis" not in gleich and "überall sauber" in satz(dienst())
for lauf_ in ({"ergebnis": "fertig", "betroffen": 0, "gesamt": 48}, {"ergebnis": "übersprungen", "betroffen": 0, "gesamt": 48},
              {"ergebnis": "abgebrochen", "betroffen": 0, "gesamt": 48}, {"aktiv": True}):
    assert html(dienst(lauf=lauf_)) == gleich and satz(dienst(lauf=lauf_)) == satz(dienst()), lauf_
for lauf_ in ({"ergebnis": "nicht gemessen", "grund": "Laufstatus: HTTP 503"}, {"ergebnis": "unvollständig"}, "kaputt"):
    hn = html(dienst(lauf=lauf_))
    assert '<i class="punkt grau"' in hn and ">sauber<" not in hn and '<span class="dk">Ergebnis</span>' in hn and "nicht gemessen" in hn, (lauf_, hn[:500])
    assert "Kundenportal: Laufergebnis nicht gemessen" in satz(dienst(lauf=lauf_)) and "überall sauber" not in satz(dienst(lauf=lauf_)), lauf_
# Englisch

with web.gilt("en"):
    h_en = web.server_dienste_block(stand(d_u), jetzt)
    assert ">incomplete<" in h_en and "last run incomplete, 3 of 48 sites not collected" in h_en and "Result" in h_en, h_en[:600]
    assert web.server_lage(stand(d_u))[0].startswith("Kundenportal: last run incomplete, 3 of 48 sites not collected.")
print("ok: Laufstatus fertig, unvollständig mit Überschneidung, übersprungen, abgebrochen, laufend; leer, fehlend, null, falscher Typ, unbekannt,"
      " Grösse, Schema, Verbindungsfehler, Zeitüberschreitung, HTTP, Weiterleitung, Netz gesperrt; Diensttabelle mit und ohne Feld; Anzeige de und en")
