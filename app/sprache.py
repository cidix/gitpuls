"""Sprache der Oberfläche (25.09.2026): jeder sichtbare Text kommt aus einer Tabelle je Sprache (app/texte/de.py, app/texte/en.py).

GITPULS_SPRACHE wählt en oder de, Vorgabe en; ein anderer Wert bricht den Start ab. ?lang=en oder ?lang=de übersteuert für einen
Aufruf. Ein Eintrag ist ein fertiger Text, eine Vorlage mit {Feldern} oder eine Funktion: Satzbau, Singular und Plural und die
Formate (Zahl, Datum, Prozent, Wochentag) gehören der Sprache, nicht dem Aufrufer. Fehlt ein Schlüssel, gilt der englische Text
und eine Zeile geht ins Log."""
import os
from contextlib import contextmanager
from contextvars import ContextVar

from app.texte import de, en

TABELLEN = {"en": en.T, "de": de.T}


def einstellung(wert=None):
    """Die eingestellte Sprache aus GITPULS_SPRACHE; leer oder nicht gesetzt heisst en."""
    w = (os.environ.get("GITPULS_SPRACHE", "") if wert is None else wert).strip() or "en"
    if w not in TABELLEN:
        raise SystemExit(f"gitpuls: GITPULS_SPRACHE={w!r} wird nicht unterstützt. Erlaubt sind {' und '.join(TABELLEN)}, "
                         f"Vorgabe en.")
    return w


STANDARD = einstellung()
_aktiv = ContextVar("gitpuls_sprache", default=STANDARD)
_gemeldet = set()


def aktiv():
    return _aktiv.get()


@contextmanager
def gilt(sprache):
    """Für die Dauer eines Seitenaufbaus diese Sprache. Je Thread eigen (ThreadingHTTPServer), zwei Aufrufe stören sich nicht."""
    marke = _aktiv.set(sprache if sprache in TABELLEN else STANDARD)
    try:
        yield
    finally:
        _aktiv.reset(marke)


def t(schluessel, *args, **kw):
    s = _aktiv.get()
    tabelle = TABELLEN[s]
    if schluessel in tabelle:
        v = tabelle[schluessel]
    else:
        v = TABELLEN["en"][schluessel]
        if (s, schluessel) not in _gemeldet:
            _gemeldet.add((s, schluessel))
            print(f"gitpuls: Text {schluessel!r} fehlt in der Tabelle {s}, es gilt der englische", flush=True)
    if callable(v):
        return v(*args, **kw)
    return v.format(*args, **kw) if args or kw else v
