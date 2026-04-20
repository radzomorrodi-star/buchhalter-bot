#!/usr/bin/env python3
"""Auto-Bucher: Bucht alle fälligen Daueraufträge für den aktuellen Monat."""
import json, os, sys
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(__file__))
from dauerauftrag import aktive_da, ungebuchte_da, buche_da

B = "~/.buchhalter-bot/buchungen.json"

try:
    buchungen = json.load(open(B))
except:
    buchungen = []

ms = date.today().strftime("%Y-%m")
ub = ungebuchte_da(buchungen, ms)

if ub:
    for u in ub:
        buche_da(u)
        print(f"✅ {u.get('name')} {abs(u.get('betrag',0)):.2f}€")
    print(f"\n{len(ub)} Daueraufträge gebucht für {ms}")
else:
    print(f"Alle DAs für {ms} bereits gebucht.")
