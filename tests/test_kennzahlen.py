"""Kleinste Prüfung, die rot wird, wenn die Ableitungen brechen. python3 -m tests.test_kennzahlen"""
import os
from collections import Counter
from datetime import date, timedelta

os.environ["TZ"] = "Europe/Zurich"   # die Zone setzt der Test selbst (10.10.2026, Audit S28): grün unter jeder TZ der Shell
from app.kennzahlen import kernzeit, rekorde, serien

heute = date(2026, 9, 2)  # Mittwoch, wt=2
tage = {}
# Vorwoche Mo-Mi = 1+2+3 = 6, Do-So = 10 -> ganze Vorwoche 16; diese Woche Mo-Mi = 4+0+5 = 9
for i, v in enumerate([1, 2, 3, 10, 0, 0, 0]):
    tage[date(2026, 8, 24) + timedelta(days=i)] = v
tage[date(2026, 8, 31)] = 4; tage[date(2026, 9, 2)] = 5
s = serien(tage, heute)
assert s["laengste"] == 4 and s["laengste_von"] == date(2026, 8, 24) and s["laengste_bis"] == date(2026, 8, 27), s
assert s["aktuell"] == 1 and s["aktiv_monat"] == 1, s  # 01.09. leer, 02.09. aktiv
assert kernzeit(Counter({9: 5, 10: 5, 22: 2})) == (9, 11, 10 * 100 / 12)
r = rekorde(tage)
assert r == {"tag": (date(2026, 8, 27), 10), "woche": ("2026-W35", 16)}, r
print("ok: Serien, Kernzeit, Rekorde")
