"""Endpunkt der Kachel 3 (10.10.2026, Befund Cédric 07:28): in «30 Tage» und «Monat» enden volle Linie, Fläche und Punkt an derselben
Stelle, dem letzten Punkt der Reihe. Bis hier endeten Linie und Fläche einen Tag früher, das letzte Stück war gepunktet und der Punkt stand
daneben. Geprüft am 10. (Monat und 30 Tage), am 1. (ein einziger Punkt bleibt ein kurzer Strich mit dem Punkt in der Mitte, beide ganz
in der Zeichenfläche, auch mit der runden Kappe: 1 px sind bei der schmalsten Fläche von 300 px 3.4 Einheiten) und am 25.10.2026 (Ende
der Sommerzeit). Gemessen in Einheiten der viewBox (1000 x 260); der Punkt steht als HTML in Prozent mit einer Stelle.
python3 -m tests.test_endpunkt  (auf dem Mac mit .venv/bin/python)"""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from tests.test_audit import commit, umgebung

db, web = umgebung()
ZH = ZoneInfo("Europe/Zurich")
H = 260
c = db.verbinden(schreibend=True)
for i in range(80):   # 14.08. bis 01.11.2026, je Tag 0 bis 3 Commits um 10:00, damit die Kumulierung steigt und Stufen hat
    for k in range(i % 4):
        commit(c, f"s{i}-{k}", "o/r", datetime.combine(date(2026, 8, 14) + timedelta(days=i), datetime.min.time(), ZH).replace(hour=10))
c.commit()

ZAHL = r"(-?[\d.]+)"


def fenster(html_, name):
    a = html_.index(f'<div class="fenster" data-gruppe="k3" data-fenster="{name}"')
    return html_[a:html_.index("</svg>", a) + 400]


def messen(f):
    """(Linienende, Flächenende, Punktmitte) der vollen Reihe; ein einziger Punkt als Mitte seines Strichs."""
    pfade = re.findall(r'<path class="l( [^"]*)?" d="([^"]+)"', f)
    strich = re.search(rf'<line class="l" x1="{ZAHL}" y1="{ZAHL}" x2="{ZAHL}" y2="{ZAHL}"', f)
    if pfade:
        z = [float(v) for v in re.findall(ZAHL, pfade[-1][1])]
        ende, klasse = (z[-2], z[-1]), pfade[-1][0] or ""
    elif strich:
        x1, y1, x2, y2 = map(float, strich.groups())
        ende, klasse = ((x1 + x2) / 2, y1), ""
    else:
        ende, klasse = None, ""
    poly = re.search(r'<polygon class="flaeche" points="([^"]+)"', f)
    flaeche = None
    if poly:
        ecken = [tuple(map(float, p.split(","))) for p in poly.group(1).split()]
        flaeche = (max(x for x, _ in ecken), ecken[-2][1])
    p = re.search(rf'class="pjetzt" style="left:{ZAHL}%;top:{ZAHL}%"', f)
    punkt = (float(p.group(1)) * 10, float(p.group(2)) * H / 100) if p else None
    return ende, klasse, flaeche, punkt, bool(strich)


nah = lambda a, b: a and b and abs(a[0] - b[0]) <= .6 and abs(a[1] - b[1]) <= .6
fehler = []
for jetzt in (datetime(2026, 10, 10, 7, 28, tzinfo=ZH), datetime(2026, 10, 1, 7, 28, tzinfo=ZH), datetime(2026, 10, 25, 12, 0, tzinfo=ZH),
              datetime(2026, 11, 1, 22, 40, tzinfo=ZH)):
    s = web.kacheln_block(c, jetzt.date(), jetzt)
    for name in ("30", "monat"):
        ende, klasse, flaeche, punkt, strich = messen(fenster(s, name))
        wo = f"{jetzt:%d.%m.%Y} {name}"
        if not nah(ende, punkt):
            fehler.append(f"{wo}: Linienende {ende} ist nicht die Punktmitte {punkt}")
        if klasse.strip():
            fehler.append(f"{wo}: das letzte Stück ist «{klasse.strip()}», nicht die volle Linie")
        if jetzt.day == 1 and name == "monat":
            if not strich:
                fehler.append(f"{wo}: ein einziger Punkt ohne kurzen Strich")
            x1, x2 = map(float, re.search(rf'<line class="l" x1="{ZAHL}" y1="{ZAHL}" x2="{ZAHL}"', fenster(s, name)).group(1, 3)) if strich else (0, 0)
            if not (3.4 <= x1 and x2 <= 996.6):
                fehler.append(f"{wo}: Strich {x1}..{x2} ragt aus der Zeichenfläche 0..1000")
        elif not nah(flaeche, ende):
            fehler.append(f"{wo}: Fläche endet bei {flaeche}, die Linie bei {ende}")
assert not fehler, "\n".join(fehler)
print("ok: Linie, Fläche und Punkt der Kachel 3 enden am selben Punkt, ein einziger Punkt liegt ganz in der Zeichenfläche (10.10., 1.10., 25.10., 1.11.2026)")
