"""Schreibweisen der Oberfläche (26.09.2026): jede Zahl, jedes Dezimal, Prozent, Vorzeichen, Datum, jede Uhrzeit und Spanne der Seite
kommt aus diesem Modul, nach CLDR über Babel statt aus handgebauten Tabellen. Anlass: bis dahin folgten die Schreibweisen der
Oberflächensprache (de 1'234, 25.09., 34 %), also bekam jeder Deutschsprachige das Schweizer Format; derselbe Fehler wie bei der
Watchface Umbra.

Die Format-Locale ist getrennt von der Oberflächensprache. Vorrang:
  1. GITPULS_FORMAT (Betreiber, etwa de-CH), sofern ihre Sprache die aktive Oberflächensprache ist; eine englische Seite bekommt
     sonst deutsche Monats- und Wochentagsnamen,
  2. der erste Eintrag aus Accept-Language (nach q geordnet), dessen Sprache die aktive Oberflächensprache ist,
  3. de-DE für Deutsch, en-US für Englisch.
Ein ungültiger GITPULS_FORMAT bricht den Start ab, wie GITPULS_SPRACHE.

Festlegungen: das Minus ist überall das echte Minuszeichen U+2212, nie der Bindestrich, den CLDR für de und en liefert. Jedes normale
Leerzeichen in einem formatierten Wert wird geschützt (U+00A0), damit ein Wert nie umbricht. Die Uhrzeit folgt der Locale, en-US also
12 Stunden. Die Kurzform eines Datums folgt der Sprache der Locale: Deutsch numerisch (Skelett ddMM, «24.09.»), Englisch mit
Monatskürzel (MMMd, «Sep 24», «24 Sept»); beides sind CLDR-Skelette, gewählt wird nur das Skelett. Die Wochenlogik bleibt ISO mit
Montag als Wochenbeginn, auch für en-US: sie ist Rechnung, keine Schreibweise."""
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar

from babel import Locale, UnknownLocaleError
from babel.dates import format_date, format_interval, format_skeleton, format_time
from babel.numbers import format_decimal, format_percent

VORGABE = {"de": "de_DE", "en": "en_US"}
MINUS = "−"
SKELETT = {  # je Sprache der Locale: (kurz, mit Jahr, mit Wochentag)
    "de": ("ddMM", "ddMMyyyy", "EEEddMM"),
    "en": ("MMMd", "yMMMd", "MMMEd"),
}


def locale_von(tag):
    """Locale aus einem Tag wie «de-CH» oder «de_CH»; None, wenn Babel ihn nicht kennt."""
    try:
        return Locale.parse((tag or "").strip().replace("-", "_")) if (tag or "").strip() else None
    except (ValueError, UnknownLocaleError):
        return None


def einstellung(wert=None):
    """GITPULS_FORMAT, leer heisst keine Vorgabe des Betreibers; ein unbekannter Wert bricht den Start ab."""
    w = (os.environ.get("GITPULS_FORMAT", "") if wert is None else wert).strip()
    if not w:
        return None
    loc = locale_von(w)
    if not loc or loc.language not in VORGABE:
        raise SystemExit(f"gitpuls: GITPULS_FORMAT={w!r} ist keine Locale für de oder en (erwartet etwa de-CH, de-DE, en-US, en-GB).")
    return loc


BETREIBER = einstellung()


Q_WERT = re.compile(r"(0(\.\d{0,3})?|1(\.0{0,3})?)")   # RFC 9110, 12.4.2: qvalue = ( "0" [ "." 0*3DIGIT ] ) / ( "1" [ "." 0*3("0") ] )


def accept_language(kopf):
    """Die Tags aus Accept-Language nach q absteigend, bei gleichem q in ihrer Reihenfolge im Kopf. Seit 26.09.2026 (Codex-Review) nach
    RFC 9110: q=0 heisst ausdrücklich nicht gewünscht und fällt weg, ebenso jeder Eintrag mit einem q ausserhalb der Syntax oder des
    Bereichs 0 bis 1 («garbage», «nan», «2», «-1»); vorher wurden beide zu q=0 und blieben Kandidaten."""
    eintraege = []
    for i, teil in enumerate((kopf or "").split(",")):
        tag, *param = [x.strip() for x in teil.split(";")]
        q, gueltig = 1.0, True
        for p in param:
            if p[:2].lower() == "q=":
                gueltig = bool(Q_WERT.fullmatch(p[2:]))
                q = float(p[2:]) if gueltig else 0.0
        if tag and tag != "*" and gueltig and q > 0:
            eintraege.append((-q, i, tag))
    return [tag for _, _, tag in sorted(eintraege)]


def waehlen(sprache, kopf="", betreiber=None):
    """Die Format-Locale eines Aufrufs nach dem Vorrang oben."""
    betreiber = BETREIBER if betreiber is None else betreiber
    if betreiber and betreiber.language == sprache:
        return betreiber
    for tag in accept_language(kopf):
        loc = locale_von(tag)
        if loc and loc.language == sprache:
            return loc
    return Locale.parse(VORGABE.get(sprache, "en_US"))


# Ausserhalb eines Seitenaufbaus (Gesundheitspfad, Tests einzelner Blöcke) gilt die Locale zur eingestellten Sprache, wie app/sprache.py.
_aktiv = ContextVar("gitpuls_format", default=waehlen(os.environ.get("GITPULS_SPRACHE", "").strip() or "en"))


@contextmanager
def gilt(loc):
    marke = _aktiv.set(loc)
    try:
        yield
    finally:
        _aktiv.reset(marke)


def aktiv():
    return _aktiv.get()


def bcp47():
    """Die aktive Locale als BCP-47-Tag für das Skript im Browser (Intl, ebenfalls CLDR)."""
    loc = aktiv()
    return "-".join(x for x in (loc.language, loc.script, loc.territory) if x)


def _fest(s):
    """Minus als U+2212, jedes normale Leerzeichen geschützt (U+00A0), das schmale U+2009 der CLDR-Spannen ebenso (U+202F)."""
    return s.replace("-", MINUS).replace(" ", "\u00a0").replace("\u2009", "\u202f")


def zahl(n, vorzeichen=False):
    """Ganze Zahl mit Gruppierung der Locale: de-CH 1’234, de-DE 1.234, en 1,234; mit vorzeichen «+1’234» und «−476»."""
    s = _fest(format_decimal(n, locale=aktiv()))
    return f"+{s}" if vorzeichen and n > 0 else s


def zeilen(plus, minus):
    """Codezeilen als Paar mit festem Vorzeichen, auch bei null: («+45’997», «−11’300»), («+0», «−0»)."""
    return "+" + zahl(plus), MINUS + zahl(minus)


def dezimal(x, stellen=1):
    """Dezimalwert mit fester Zahl von Nachkommastellen: de-CH 26.1, de-DE 26,1, en 26.1."""
    return _fest(format_decimal(x, format="#,##0." + "0" * stellen if stellen else "#,##0", locale=aktiv()))


def prozent(p, vorzeichen=False):
    """Prozent aus einem Wert in Prozentpunkten (34 für 34 %), gerundet auf ganze: de-DE «34 %», de-CH und en «34%»; mit vorzeichen
    «+12 %», negativ immer «−23 %»."""
    s = _fest(format_percent(abs(round(p)) / 100, locale=aktiv()))
    if round(p) < 0:
        return MINUS + s
    return f"+{s}" if vorzeichen and round(p) > 0 else s


def _skelett(i):
    return SKELETT.get(aktiv().language, SKELETT["en"])[i]


def datum(d):
    """Kurzes Datum ohne Jahr: de «24.09.», en-US «Sep 24», en-GB «24 Sept»."""
    return _fest(format_skeleton(_skelett(0), d, locale=aktiv()))


def datum_jahr(d):
    """Datum mit Jahr: de «24.09.2026», en-US «Sep 24, 2026», en-GB «24 Sept 2026»."""
    return _fest(format_skeleton(_skelett(1), d, locale=aktiv()))


def wochentag_datum(d):
    """Wochentag und Datum: de «Do., 24.9.», en-US «Thu, Sep 24», en-GB «Thu 24 Sept»."""
    return _fest(format_skeleton(_skelett(2), d, locale=aktiv()))


def monat(d):
    """Monatsname allein stehend: de «September», en «September»."""
    return _fest(format_date(d, "LLLL", locale=aktiv()))


def monat_jahr(d):
    """Monat mit Jahr: de «Oktober 2026», en «October 2026»."""
    return _fest(format_date(d, "LLLL y", locale=aktiv()))


def wochentag(d):
    """Kurzer Wochentag: de «Mo.», en «Mon»."""
    return _fest(format_date(d, "EEE", locale=aktiv()))


def spanne(a, b):
    """Datumsspanne als CLDR-Intervall im Skelett des kurzen Datums: de «15.–21.09.», en-US «Sep 15 – 21»."""
    if a == b:   # CLDR fiele hier auf die Rückfallform «16.9.» statt «16.09.»
        return datum(a)
    return _fest(format_interval(a, b, _skelett(0), locale=aktiv()))


def spanne_jahr(a, b):
    """Datumsspanne mit Jahr als CLDR-Intervall im Skelett des Datums mit Jahr: de «03.09.–02.10.2026», en-US «Sep 3 – Oct 2, 2026»."""
    if a == b:
        return datum_jahr(a)
    return _fest(format_interval(a, b, _skelett(1), locale=aktiv()))


def jahr(d):
    return format_date(d, "y", locale=aktiv())


def uhrzeit(t):
    """Uhrzeit in der Form der Locale: de und en-GB «17:03», en-US «5:03 PM»."""
    return _fest(format_time(t, "short", locale=aktiv()))


def zeitpunkt(dt, mit_jahr=True):
    """Datum und Uhrzeit, verbunden nach dem CLDR-Muster der Locale: de «24.09.2026, 17:03», en-US «Sep 24, 2026, 5:03 PM»."""
    d = datum_jahr(dt) if mit_jahr else datum(dt)
    return _fest(aktiv().datetime_formats["medium"].format(uhrzeit(dt), d))
