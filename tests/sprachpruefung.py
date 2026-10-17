"""Findet Deutsch in einem Text der englischen Seite: Umlaute, ß und typische Wörter der Oberfläche. Für tests/test_sprache.py und
die Messung der Live-Seite. Daten (Betreffzeilen, Zweck der Dienste, Zweignamen) tragen translate="no" und
werden vor der Prüfung entfernt; der Aufrufer übergibt nur den Rest."""
import re
from html.parser import HTMLParser

WOERTER = ("heute", "gestern", "Woche", "Wochen", "Vorwoche", "Tag", "Tage", "Tagen", "seit", "Zuletzt", "ruht", "still", "Zweig",
           "Zweige", "wartet", "warten", "kein", "keine", "nicht", "und", "mit", "von", "bis", "über", "Stand", "Lauf", "läuft", "sauber",
           "beendet", "gescheitert", "Neustart", "Dienste", "Sprache", "Alter", "Summe", "Rang", "Serie", "Rekorde", "Bester",
           "Beste", "Laufende", "Längste", "Rückblick", "Projekte", "Aktualisieren", "Uhr", "Std", "Min", "Zeilen", "Achse",
           "Durchschnitt", "Handlungsbedarf", "Sicherheitswarnungen", "Maschine", "Installiert", "Pakete", "Kerne", "Platte",
           "Arbeitsspeicher", "Auslastung", "Zweck", "Dazu", "Bild", "gesund", "nächster", "zuletzt", "davor", "letzte", "diese", "Hell",
           "Dunkel", "Punkte", "Kommentare", "Lizenz", "erstmals", "gesehen", "Fenster", "Daten", "noch", "vor", "ab", "am", "je")
MUSTER = re.compile(r"[äöüÄÖÜß]|\b(" + "|".join(WOERTER) + r")\b")


def deutsch(text, namen=()):
    """Die Fundstellen mit etwas Umgebung, leer wenn keine. `namen` sind Namen aus den Testdaten (Repos wie fernwerk-studio/still), die vor der
    Prüfung wegfallen: ein Repo darf heissen, wie es will."""
    for n in sorted(namen, key=len, reverse=True):
        text = re.sub(rf"(?<![\w/-]){re.escape(n)}(?![\w/-])", " ", text)
    return [text[max(0, m.start() - 25):m.end() + 25].replace("\n", " ") for m in MUSTER.finditer(text)]


LEER = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "circle", "line", "path",
        "rect", "polygon", "stop"}


class _Text(HTMLParser):
    """Sichtbarer Text und title/aria-label eines HTML-Stücks, ohne script, style und alles unter translate="no"."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.teile, self.stapel = [], []

    def aus(self):
        return any(self.stapel)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        weg = self.aus() or tag in ("script", "style") or a.get("translate") == "no"
        if not weg:
            self.teile += [a[k] for k in ("title", "aria-label") if a.get(k)]
        if tag not in LEER:
            self.stapel.append(weg)

    def handle_startendtag(self, tag, attrs):
        if not self.aus():
            a = dict(attrs)
            self.teile += [a[k] for k in ("title", "aria-label") if a.get(k)]

    def handle_endtag(self, tag):
        if tag not in LEER and self.stapel:
            self.stapel.pop()

    def handle_data(self, data):
        if not self.aus() and data.strip():
            self.teile.append(data.strip())


def text_ohne_daten(stueck):
    p = _Text()
    p.feed(stueck)
    return "\n".join(p.teile)
