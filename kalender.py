#!/usr/bin/env python3
"""
iCloud Kalender - Read Only
Liest Termine aus dem iCloud CalDAV Kalender.
"""
import caldav
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG_PATH = os.getenv('CALDAV_CONFIG_PATH', str(Path.home() / '.buchhalter-bot' / 'caldav.json'))

def get_config():
    try:
        return json.load(open(CONFIG_PATH))['icloud_caldav']
    except Exception as e:
        return None

def get_client():
    cfg = get_config()
    if not cfg:
        return None, "Config nicht gefunden"
    try:
        client = caldav.DAVClient(
            url=cfg['url'],
            username=cfg['username'],
            password=cfg['app_password']
        )
        return client, None
    except Exception as e:
        return None, str(e)

def get_calendars():
    client, err = get_client()
    if err:
        return [], err
    try:
        principal = client.principal()
        cals = principal.calendars()
        return cals, None
    except Exception as e:
        return [], str(e)

def get_events(days_from=0, days_to=7, cal_name=None):
    """Gibt Termine im angegebenen Zeitraum zurück."""
    client, err = get_client()
    if err:
        return [], err
    try:
        principal = client.principal()
        cals = principal.calendars()
        if not cals:
            return [], "Keine Kalender gefunden"
        
        now = datetime.now(timezone.utc)
        start = now + timedelta(days=days_from)
        end = now + timedelta(days=days_to)
        
        events = []
        for cal in cals:
            try:
                if cal_name and cal_name.lower() not in cal.name.lower():
                    continue
                results = cal.date_search(start=start, end=end, expand=True)
                for event in results:
                    try:
                        comp = event.vobject_instance.vevent
                        dtstart = comp.dtstart.value
                        if hasattr(dtstart, 'date'):
                            pass
                        else:
                            dtstart = datetime.combine(dtstart, datetime.min.time(), tzinfo=timezone.utc)
                        if not hasattr(dtstart, 'tzinfo') or dtstart.tzinfo is None:
                            dtstart = dtstart.replace(tzinfo=timezone.utc)
                        summary = str(comp.summary.value) if hasattr(comp, 'summary') else '(kein Titel)'
                        location = str(comp.location.value) if hasattr(comp, 'location') else ''
                        description = str(comp.description.value) if hasattr(comp, 'description') else ''
                        events.append({
                            'summary': summary,
                            'start': dtstart,
                            'location': location,
                            'description': description,
                            'calendar': cal.name if hasattr(cal, 'name') else 'Kalender'
                        })
                    except Exception:
                        continue
            except Exception:
                continue
        
        events.sort(key=lambda e: e['start'])
        return events, None
    except Exception as e:
        return [], str(e)

def format_events(events, titel="📅 Termine"):
    if not events:
        return f"{titel}\n\nKeine Termine gefunden."
    
    lines = [f"{titel}"]
    lines.append("━━━━━━━━━━━━━━━━━━")
    
    current_day = None
    for e in events:
        start = e['start']
        if hasattr(start, 'astimezone'):
            from zoneinfo import ZoneInfo
            start_local = start.astimezone(ZoneInfo('Europe/Berlin'))
        else:
            start_local = start
        
        day_str = start_local.strftime('%A, %d.%m.')
        time_str = start_local.strftime('%H:%M')
        
        # Tages-Trennlinie
        if day_str != current_day:
            if current_day:
                lines.append("")
            lines.append(f"📆 *{day_str}*")
            current_day = day_str
        
        line = f"  🕐 {time_str} — {e['summary']}"
        if e['location']:
            line += f"\n     📍 {e['location']}"
        lines.append(line)
    
    return "\n".join(lines)

def kalender_heute():
    events, err = get_events(days_from=0, days_to=1)
    if err:
        return f"❌ Fehler: {err}"
    return format_events(events, "📅 Heute")

def kalender_morgen():
    events, err = get_events(days_from=1, days_to=2)
    if err:
        return f"❌ Fehler: {err}"
    return format_events(events, "📅 Morgen")

def kalender_woche():
    events, err = get_events(days_from=0, days_to=7)
    if err:
        return f"❌ Fehler: {err}"
    return format_events(events, "📅 Diese Woche")

def kalender_monat():
    events, err = get_events(days_from=0, days_to=30)
    if err:
        return f"❌ Fehler: {err}"
    return format_events(events, "📅 Nächste 30 Tage")

def kalender_vergangenheit(tage=7):
    events, err = get_events(days_from=-tage, days_to=0)
    if err:
        return f"❌ Fehler: {err}"
    return format_events(events, f"📅 Letzte {tage} Tage")

if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'woche'
    if cmd == 'heute': print(kalender_heute())
    elif cmd == 'morgen': print(kalender_morgen())
    elif cmd == 'monat': print(kalender_monat())
    elif cmd == 'vergangenheit': print(kalender_vergangenheit(int(sys.argv[2]) if len(sys.argv)>2 else 7))
    else: print(kalender_woche())
