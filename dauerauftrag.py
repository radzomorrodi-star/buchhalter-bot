#!/usr/bin/env python3
"""Dauerauftrag Manager — Recurring payments with inline buttons."""
import json, os, re
from datetime import datetime, date, timedelta

DA_PATH = "~/.buchhalter-bot/dauerauftraege.json"
os.makedirs(os.path.dirname(DA_PATH), exist_ok=True)

def lade_da():
    try: return json.load(open(DA_PATH, encoding='utf-8'))
    except: return []

def speichere_da(da_list):
    json.dump(da_list, open(DA_PATH, 'w'), ensure_ascii=False, indent=2)

def naechste_id():
    da = lade_da()
    ids = []
    for d in da:
        try: ids.append(int(d.get('id', 0)))
        except: pass
    return max(ids, default=0) + 1

def add_da(name, betrag, kategorie, gruppe, intervall='monatlich'):
    da = lade_da()
    eintrag = {
        'id': naechste_id(),
        'name': name,
        'betrag': -abs(float(betrag)),
        'kategorie': kategorie,
        'gruppe': gruppe,
        'intervall': intervall,
        'aktiv': True,
        'erstellt': date.today().isoformat(),
        'letzte_buchung': None,
    }
    da.append(eintrag)
    speichere_da(da)
    return eintrag

def update_da(da_id, **kwargs):
    da = lade_da()
    for d in da:
        if d['id'] == int(da_id):
            for k, v in kwargs.items():
                d[k] = v
            speichere_da(da)
            return d
    return None

def delete_da(da_id):
    da = lade_da()
    before = len(da)
    da = [d for d in da if d['id'] != int(da_id)]
    if len(da) < before:
        speichere_da(da)
        return True
    return False

def toggle_da(da_id):
    da = lade_da()
    for d in da:
        if d['id'] == int(da_id):
            d['aktiv'] = not d['aktiv']
            speichere_da(da)
            return d
    return None

def get_da(da_id):
    for d in lade_da():
        if d['id'] == int(da_id):
            return d
    return None

def aktive_da():
    return [d for d in lade_da() if d.get('aktiv')]

def monatliche_summe():
    total = 0
    for d in aktive_da():
        b = abs(d['betrag'])
        iv = d.get('intervall', 'monatlich')
        if iv == 'monatlich': total += b
        elif iv == 'woechentlich': total += b * 4.33
        elif iv == 'taeglich': total += b * 30
        elif iv == 'quartal': total += b / 3
        elif iv == 'jaehrlich': total += b / 12
    return total

def from_buchung(buchung):
    """Erstelle Dauerauftrag aus einer einzelnen Buchung."""
    return add_da(
        name=buchung.get('notiz') or buchung.get('kategorie', 'Unbekannt'),
        betrag=buchung.get('betrag', 0),
        kategorie=buchung.get('kategorie', 'Sonstiges'),
        gruppe=buchung.get('gruppe') or buchung.get('kategorie') or 'Sonstiges',
    )

def format_da(d):
    status = "✅" if d['aktiv'] else "⏸️"
    intervall = {"taeglich": "tgl", "monatlich": "mtl", "woechentlich": "wtl", "quartal": "qtl", "jaehrlich": "jrl"}.get(d.get('intervall','monatlich'), d.get('intervall','monatlich'))
    return f"{status} #{d['id']} {d['name']} — {abs(d['betrag']):.2f}€/{intervall}"

def format_liste():
    da = lade_da()
    if not da:
        return "Keine Daueraufträge."
    lines = []
    aktiv = [d for d in da if d.get('aktiv')]
    inaktiv = [d for d in da if not d.get('aktiv')]
    if aktiv:
        lines.append(f"✅ *{len(aktiv)} aktiv:*")
        for d in aktiv:
            lines.append(f"  {format_da(d)}")
    if inaktiv:
        lines.append(f"\n⏸️ *{len(inaktiv)} pausiert:*")
        for d in inaktiv:
            lines.append(f"  {format_da(d)}")
    lines.append(f"\n💰 *Monatlich: {monatliche_summe():.2f}€*")
    return "\n".join(lines)

def generate_due_recurring(monat_str=None):
    """Auto-buche alle fälligen Daueraufträge für den angegebenen Monat.

    Nutzt dauerauftrag_id als Duplikat-Schutz.
    Gibt Liste der neu erstellten Buchungen zurück.
    """
    if monat_str is None:
        monat_str = date.today().strftime("%Y-%m")

    year, month = int(monat_str[:4]), int(monat_str[5:7])
    BUCHUNGEN_PATH = "~/.buchhalter-bot/buchungen.json"

    try:
        buchungen = json.load(open(BUCHUNGEN_PATH, encoding='utf-8'))
    except:
        buchungen = []

    # Alle bestehenden dauerauftrag_ids UND name+betrag Combos für diesen Monat
    existing_da_ids = set()
    existing_name_betrag = set()
    for b in buchungen:
        if str(b.get('datum', '')).startswith(monat_str):
            da_id = b.get('dauerauftrag_id', '')
            if da_id:
                existing_da_ids.add(da_id)
            if b.get('quelle') in ('dauerauftrag_auto', 'dauerauftrag'):
                name_key = (b.get('beteiligter') or b.get('notiz', '')).lower().strip()
                betrag_key = round(abs(float(b.get('betrag', 0))), 2)
                existing_name_betrag.add((name_key, betrag_key))

    alle_da = aktive_da()
    neue = []

    for d in alle_da:
        da_id = d['id']
        name = d.get('name', '')
        betrag = -abs(float(d.get('betrag', 0)))
        intervall = d.get('intervall', 'monatlich')
        tag = d.get('tag') or 1

        # Unique ID für diesen Monat
        safe_name = re.sub(r'[^a-z0-9]', '_', name.lower())[:20]
        unique_id = f"{safe_name}_{tag:02d}_{abs(betrag):.0f}"

        if intervall == 'monatlich':
            # Skip if already booked (by ID or by name+amount match)
            name_check = (name.lower().strip(), round(abs(betrag), 2))
            if unique_id in existing_da_ids or name_check in existing_name_betrag:
                continue
            # Buchungsdatum: tag des Monats (oder heute wenn tag schon vorbei)
            try:
                buch_datum = date(year, month, min(tag, 28))
            except:
                buch_datum = date(year, month, 1)

            eintrag = {
                "datum": buch_datum.isoformat(),
                "zeit": "00:01",
                "art": "Dauerauftrag",
                "betrag": betrag,
                "waehrung": "EUR",
                "kategorie": d.get('kategorie', 'Sonstiges'),
                "notiz": d.get('notiz') or name,
                "beteiligter": name,
                "konto": d.get('konto', 'N26'),
                "einzahlkonto": "Kosten",
                "gruppe": d.get('gruppe') or d.get('kategorie') or 'Sonstiges',
                "quittung": "",
                "dauerauftrag_id": unique_id,
                "quelle": "dauerauftrag_auto",
            }
            buchungen.append(eintrag)
            neue.append(eintrag)

        elif intervall == 'woechentlich':
            # Alle Wochen im Monat
            import calendar
            first_day = date(year, month, 1)
            last_day = date(year, month, calendar.monthrange(year, month)[1])
            today = date.today()
            d_date = first_day
            week_num = 0
            while d_date <= last_day:
                week_num += 1
                week_id = f"{safe_name}_w{week_num}_{abs(betrag):.0f}"
                if week_id not in existing_da_ids and d_date <= today:
                    eintrag = {
                        "datum": d_date.isoformat(),
                        "zeit": "00:01",
                        "art": "Dauerauftrag",
                        "betrag": betrag,
                        "waehrung": "EUR",
                        "kategorie": d.get('kategorie', 'Sonstiges'),
                        "notiz": d.get('notiz') or name,
                        "beteiligter": name,
                        "konto": d.get('konto', 'N26'),
                        "einzahlkonto": "Kosten",
                        "gruppe": d.get('gruppe') or d.get('kategorie') or 'Sonstiges',
                        "quittung": "",
                        "dauerauftrag_id": week_id,
                        "quelle": "dauerauftrag_auto",
                    }
                    buchungen.append(eintrag)
                    neue.append(eintrag)
                d_date += timedelta(days=7)

    if neue:
        os.makedirs(os.path.dirname(BUCHUNGEN_PATH), exist_ok=True)
        json.dump(buchungen, open(BUCHUNGEN_PATH, 'w'), ensure_ascii=False, indent=2)

        # letzte_buchung updaten
        da_list = lade_da()
        for d in da_list:
            for n in neue:
                if n.get('beteiligter') == d.get('name'):
                    d['letzte_buchung'] = date.today().isoformat()
        speichere_da(da_list)

    return neue


def ungebuchte_da(buchungen, monat_str):
    """Finde DAs die noch nicht als Buchung im Monat existieren."""
    da = aktive_da()
    monat_b = [b for b in buchungen if str(b.get('datum','')).startswith(monat_str)]
    
    ungebucht = []
    for d in da:
        name = d.get('name', '').lower()
        kat = d.get('kategorie', '').lower()
        betrag = abs(d.get('betrag', 0))
        
        # Check ob ähnliche Buchung schon existiert
        found = False
        for b in monat_b:
            b_notiz = (b.get('notiz', '') or '').lower()
            b_kat = (b.get('kategorie', '') or '').lower()
            b_betrag = abs(float(b.get('betrag', 0)))
            
            if (name in b_notiz or name in b_kat or kat in b_kat) and abs(b_betrag - betrag) < 1:
                found = True
                break
        
        if not found:
            ungebucht.append(d)
    
    return ungebucht

def buche_da(da_entry):
    """Buche einen Dauerauftrag als normale Buchung."""
    import os
    from datetime import datetime, date
    BUCHUNGEN_PATH = "~/.buchhalter-bot/buchungen.json"
    try:
        buchungen = json.load(open(BUCHUNGEN_PATH, encoding='utf-8'))
    except:
        buchungen = []
    
    eintrag = {
        "datum": datetime.now().strftime("%Y-%m-%d"),
        "uhrzeit": datetime.now().strftime("%H:%M"),
        "betrag": -abs(float(da_entry.get('betrag', 0))),
        "kategorie": da_entry.get('kategorie', 'Sonstiges'),
        "gruppe": da_entry.get('gruppe', 'Misc'),
        "notiz": da_entry.get('name', ''),
        "quelle": "dauerauftrag"
    }
    buchungen.append(eintrag)
    os.makedirs(os.path.dirname(BUCHUNGEN_PATH), exist_ok=True)
    json.dump(buchungen, open(BUCHUNGEN_PATH, 'w'), ensure_ascii=False, indent=2)
    
    # Letzte Buchung updaten
    da_list = lade_da()
    for d in da_list:
        if d['id'] == da_entry['id']:
            d['letzte_buchung'] = datetime.now().strftime("%Y-%m-%d")
    speichere_da(da_list)
    
    return eintrag
