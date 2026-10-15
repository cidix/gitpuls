"""Serien und Rekorde aus einer Tageszählung (lokales Datum -> Commits), nichts vorberechnet. Die Kopfzahlen für /api/kopf
(kopf(), rollende Fenster mit eigener Formatierung) sind am 10.10.2026 mit dem Endpunkt entfallen; Woche, Vorwoche und Monat rechnet
app/web.py, die SQL-Gegenprobe dazu steht in tests/gegenprobe.py."""
from collections import Counter
from datetime import timedelta


def wochenkey(d):
    return d.strftime("%G-W%V")


def serien(tage, heute):
    aktiv = sorted(d for d, v in tage.items() if v > 0)
    if not aktiv:
        return {"aktuell": 0, "aktuell_seit": None, "laengste": 0, "laengste_von": None, "laengste_bis": None, "aktiv_monat": 0}
    laengste, lauf, start, best = 0, 0, None, (None, None)
    vorher = None
    for d in aktiv:
        if vorher and d - vorher == timedelta(days=1):
            lauf += 1
        else:
            lauf, start = 1, d
        if lauf > laengste:
            laengste, best = lauf, (start, d)
        vorher = d
    # aktuelle Serie: endet heute oder gestern (heute darf noch leer sein)
    ende = heute if tage.get(heute, 0) > 0 else heute - timedelta(days=1)
    aktuell, d = 0, ende
    while tage.get(d, 0) > 0:
        aktuell += 1
        d -= timedelta(days=1)
    aktiv_monat = sum(1 for x in aktiv if x.year == heute.year and x.month == heute.month)
    # heute: ein eigener Commit von heute (10.10.2026, Audit S20); eine Serie von 1, die gestern endet, ist kein «heute wieder aktiv».
    return {"aktuell": aktuell, "aktuell_seit": ende - timedelta(days=aktuell - 1) if aktuell else None, "heute": tage.get(heute, 0) > 0,
            "laengste": laengste, "laengste_von": best[0], "laengste_bis": best[1], "aktiv_monat": aktiv_monat}


def kernzeit(stunden):
    """stunden: Counter Stunde -> n. Kürzestes zusammenhängendes Fenster mit >= 50 % der Commits."""
    gesamt = sum(stunden.values())
    if not gesamt:
        return None
    reihe = [stunden.get(h, 0) for h in range(24)]
    best = None
    for laenge in range(1, 25):
        for start in range(24):
            n = sum(reihe[(start + i) % 24] for i in range(laenge))
            if n * 2 >= gesamt and (best is None or n > best[2]):
                best = (start, (start + laenge) % 24, n)
        if best:
            return best[0], best[1], best[2] * 100 / gesamt
    return None


def rekorde(tage):
    """Bester Tag und beste Woche. Der Rang der laufenden Woche steht seit 10.10.2026 in app/web.py::wochenrang (Audit S19): er braucht
    Zeitstempel, nicht Tageszählungen, um wirklich zum selben Zeitpunkt zu vergleichen."""
    if not tage:
        return None
    bester_tag = max(tage.items(), key=lambda kv: kv[1])
    wochen = Counter()
    for d, v in tage.items():
        wochen[wochenkey(d)] += v
    return {"tag": bester_tag, "woche": max(wochen.items(), key=lambda kv: kv[1])}
