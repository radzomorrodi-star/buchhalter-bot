#!/usr/bin/env python3
"""bank_parser — Parse bank push notifications and CSV exports.

Supported:
  - Sparkasse push notifications + CSV (CAMT/semicolon/ISO-8859-1)
  - N26 push notifications + CSV (comma/UTF-8)
  - Finanzguru push notifications

All parsers return standardized dicts compatible with speichere_buchung().
"""
import csv
import io
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, '~/.buchhalter-bot/bots/hesabdar')
sys.path.insert(0, '~/.buchhalter-bot/bots/shared')

# ── Amount parsing ─────────────────────────────────────────────────────────────

_DE_AMOUNT = re.compile(r'[-+]?\d{1,3}(?:\.\d{3})*,\d{2}')  # 1.234,56
_EN_AMOUNT = re.compile(r'[-+]?\d{1,3}(?:,\d{3})*\.\d{2}')  # 1,234.56
_SIMPLE_AMOUNT = re.compile(r'[-+]?\d+[,\.]\d{2}')            # 12,50 or 12.50

def _parse_amount(text):
    """Parse German or English amount string to float. Returns None on failure."""
    if not text:
        return None
    text = text.strip().replace(' ', '').replace('\u00a0', '')
    # German: 1.234,56 → 1234.56
    m = _DE_AMOUNT.search(text)
    if m:
        s = m.group().replace('.', '').replace(',', '.')
        try: return float(s)
        except: pass
    # English: 1,234.56 → 1234.56
    m = _EN_AMOUNT.search(text)
    if m:
        s = m.group().replace(',', '')
        try: return float(s)
        except: pass
    # Simple: 12,50 or 12.50
    m = _SIMPLE_AMOUNT.search(text)
    if m:
        s = m.group().replace(',', '.')
        try: return float(s)
        except: pass
    return None

def _parse_date_de(text):
    """Parse DD.MM.YYYY or DD.MM.YY → YYYY-MM-DD."""
    for fmt in ('%d.%m.%Y', '%d.%m.%y'):
        try:
            return datetime.strptime(text.strip(), fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None

# ── Push Notification Parsers ──────────────────────────────────────────────────

# Bank detection keywords
_SPARKASSE_KW = ['sparkasse', 'konto', 'girokonto', 's-pushtan', 'spk']
_N26_KW = ['n26', 'number26']
_FINANZGURU_KW = ['finanzguru', 'finanz guru']

_MERCHANT_STRIP = [
    'kartenzahlung bei ', 'kartenzahlung ', 'lastschrift von ', 'lastschrift ',
    'ueberweisung an ', 'überweisung an ', 'zahlung an ', 'gutschrift von ',
    'eingang von ', 'abbuchung ', 'dauerauftrag an ', 'echtzeitüberweisung an ',
]

def _clean_merchant(text):
    """Strip common payment prefixes from merchant name."""
    t = text.strip()
    for prefix in _MERCHANT_STRIP:
        if t.lower().startswith(prefix):
            t = t[len(prefix):]
    # Remove trailing reference numbers
    t = re.sub(r'\s+\d{4,}$', '', t)
    # Remove trailing dates
    t = re.sub(r'\s+\d{2}\.\d{2}\.\d{2,4}$', '', t)
    return t.strip()


def _parse_sparkasse_push(text):
    """Parse Sparkasse push notification."""
    # Patterns:
    # "Umsatz: -23,50 EUR bei EDEKA am 14.04.2026"
    # "Kartenzahlung: 23,50 EUR EDEKA Zentrale 14.04."
    # "Kontostand: 1.234,56 EUR nach Abbuchung 23,50 EUR"
    result = {
        'bank': 'sparkasse', 'raw_text': text,
        'is_income': False, 'datum': datetime.now().strftime('%Y-%m-%d'),
    }
    # Betrag
    amounts = _DE_AMOUNT.findall(text) or _SIMPLE_AMOUNT.findall(text)
    if not amounts:
        return None
    betrag = _parse_amount(amounts[0])
    if betrag is None:
        return None
    # Negative = Ausgabe
    if '-' in amounts[0] or any(kw in text.lower() for kw in ['abbuchung', 'lastschrift', 'kartenzahlung', 'zahlung']):
        result['is_income'] = False
    elif any(kw in text.lower() for kw in ['gutschrift', 'eingang', 'gehalt']):
        result['is_income'] = True
    result['betrag'] = abs(betrag)
    # Datum
    dates = re.findall(r'\d{2}\.\d{2}\.\d{2,4}', text)
    if dates:
        d = _parse_date_de(dates[0])
        if d:
            result['datum'] = d
    # Merchant — text after amount, before date
    lines = text.split('\n')
    merchant_parts = []
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        # Skip lines that are just amounts or dates
        if re.match(r'^[-+]?\d+[,\.]\d{2}\s*(EUR|€)?$', line_clean):
            continue
        # Skip "Umsatz:", "Konto:", "Kontostand:" labels
        if re.match(r'^(Umsatz|Konto|Kontostand|Saldo|Verfügbar)', line_clean):
            continue
        merchant_parts.append(line_clean)
    # Try to find merchant in the remaining text
    merchant = ' '.join(merchant_parts)
    # Remove amount strings
    for a in amounts:
        merchant = merchant.replace(a, '')
    merchant = re.sub(r'EUR|€', '', merchant)
    merchant = _clean_merchant(merchant)
    if len(merchant) > 80:
        merchant = merchant[:80]
    result['merchant'] = merchant.strip() or 'Sparkasse Umsatz'
    return result


def _parse_n26_push(text):
    """Parse N26 push notification."""
    # Patterns:
    # "You spent €23.50 at EDEKA"
    # "Zahlung von 23,50 € bei EDEKA"
    # "Du hast 23,50 € bei EDEKA ausgegeben"
    result = {
        'bank': 'n26', 'raw_text': text,
        'is_income': False, 'datum': datetime.now().strftime('%Y-%m-%d'),
    }
    # Betrag
    amounts = []
    for pattern in [_EN_AMOUNT, _DE_AMOUNT, _SIMPLE_AMOUNT]:
        amounts.extend(pattern.findall(text))
    if not amounts:
        # Try €XX.XX format
        m = re.search(r'€\s*(\d+[,\.]\d{2})', text)
        if m:
            amounts = [m.group(1)]
    if not amounts:
        return None
    betrag = _parse_amount(amounts[0])
    if betrag is None:
        return None
    result['betrag'] = abs(betrag)
    if any(kw in text.lower() for kw in ['received', 'eingang', 'gutschrift', 'erhalten']):
        result['is_income'] = True
    # Merchant
    # "at MERCHANT" or "bei MERCHANT"
    m = re.search(r'(?:at|bei|von|an)\s+(.+?)(?:\.|$|\n)', text, re.IGNORECASE)
    merchant = m.group(1).strip() if m else ''
    merchant = _clean_merchant(merchant)
    # Remove amount from merchant
    for a in amounts:
        merchant = merchant.replace(a, '')
    merchant = re.sub(r'[€$]', '', merchant).strip()
    result['merchant'] = merchant or 'N26 Umsatz'
    return result


def _parse_finanzguru_push(text):
    """Parse Finanzguru app notification."""
    # Patterns:
    # "Neue Ausgabe: 23,50 € bei EDEKA (Lebensmittel)"
    # "Abbuchung: -45,00 € Netflix"
    result = {
        'bank': 'finanzguru', 'raw_text': text,
        'is_income': False, 'datum': datetime.now().strftime('%Y-%m-%d'),
    }
    amounts = _DE_AMOUNT.findall(text) or _SIMPLE_AMOUNT.findall(text)
    if not amounts:
        return None
    betrag = _parse_amount(amounts[0])
    if betrag is None:
        return None
    result['betrag'] = abs(betrag)
    if any(kw in text.lower() for kw in ['einnahme', 'eingang', 'gutschrift']):
        result['is_income'] = True
    # Merchant — usually after "bei" or after the amount
    m = re.search(r'(?:bei|von|an)\s+(.+?)(?:\s*\(|$|\n)', text, re.IGNORECASE)
    if m:
        result['merchant'] = _clean_merchant(m.group(1))
    else:
        # Fallback: everything after amount
        idx = text.find(amounts[0])
        if idx >= 0:
            rest = text[idx + len(amounts[0]):]
            rest = re.sub(r'[€$\s]+', ' ', rest).strip()
            result['merchant'] = _clean_merchant(rest[:60]) or 'Finanzguru'
        else:
            result['merchant'] = 'Finanzguru Umsatz'
    # Category hint from Finanzguru (in parentheses)
    cat_m = re.search(r'\(([^)]+)\)', text)
    if cat_m:
        result['fg_category'] = cat_m.group(1)
    return result


def parse_push_notification(text):
    """Top-level push notification parser. Returns dict or None."""
    if not text or len(text) < 5:
        return None
    t_lower = text.lower()
    # Detect bank
    if any(kw in t_lower for kw in _FINANZGURU_KW):
        return _parse_finanzguru_push(text)
    if any(kw in t_lower for kw in _N26_KW):
        return _parse_n26_push(text)
    if any(kw in t_lower for kw in _SPARKASSE_KW):
        return _parse_sparkasse_push(text)
    # Generic: try to detect any bank-like notification
    # Must have an amount + some merchant indicator
    if re.search(r'(€|EUR|Umsatz|Zahlung|Kartenzahlung|Lastschrift|Abbuchung)', text, re.IGNORECASE):
        amounts = _DE_AMOUNT.findall(text) or _SIMPLE_AMOUNT.findall(text) or _EN_AMOUNT.findall(text)
        if amounts:
            result = {
                'bank': 'unbekannt', 'raw_text': text,
                'is_income': False, 'datum': datetime.now().strftime('%Y-%m-%d'),
                'betrag': abs(_parse_amount(amounts[0]) or 0),
            }
            m = re.search(r'(?:bei|von|an|at)\s+(.+?)(?:\.|$|\n)', text, re.IGNORECASE)
            result['merchant'] = _clean_merchant(m.group(1)) if m else text[:60]
            return result
    return None


# ── CSV Parsers ────────────────────────────────────────────────────────────────

def detect_csv_format(content):
    """Detect CSV format from content. Returns 'sparkasse', 'n26', or 'unknown'."""
    first_lines = content[:500].lower()
    if 'auftragskonto' in first_lines or 'buchungstag' in first_lines:
        return 'sparkasse'
    if 'payee' in first_lines or 'amount (eur)' in first_lines:
        return 'n26'
    if ';' in first_lines and ('betrag' in first_lines or 'umsatz' in first_lines):
        return 'sparkasse'  # generic German bank CSV
    if ',' in first_lines and 'date' in first_lines:
        return 'n26'  # generic English bank CSV
    return 'unknown'


def parse_csv_sparkasse(content):
    """Parse Sparkasse CSV (CAMT format).
    Semicolon-separated, ISO-8859-1, German dates/amounts.
    Returns list of transaction dicts.
    """
    # Try decoding
    if isinstance(content, bytes):
        for enc in ('iso-8859-1', 'cp1252', 'utf-8'):
            try:
                content = content.decode(enc)
                break
            except:
                continue
    results = []
    reader = csv.reader(io.StringIO(content), delimiter=';', quotechar='"')
    headers = None
    for row in reader:
        if not row:
            continue
        # Find header row
        if headers is None:
            row_lower = [c.lower().strip() for c in row]
            if any('buchung' in c for c in row_lower) or any('betrag' in c for c in row_lower):
                headers = row_lower
                continue
            continue
        if len(row) < len(headers):
            continue
        data = dict(zip(headers, [c.strip() for c in row]))
        # Find betrag column
        betrag_str = None
        for key in ['betrag', 'betrag (eur)', 'umsatz']:
            if key in data:
                betrag_str = data[key]
                break
        if not betrag_str:
            continue
        betrag = _parse_amount(betrag_str)
        if betrag is None:
            continue
        # Datum
        datum = None
        for key in ['buchungstag', 'buchungsdatum', 'valutadatum', 'datum']:
            if key in data and data[key]:
                datum = _parse_date_de(data[key])
                if datum:
                    break
        if not datum:
            datum = datetime.now().strftime('%Y-%m-%d')
        # Merchant / Empfaenger
        merchant = ''
        for key in ['beguenstigter/zahlungspflichtiger', 'begünstigter/zahlungspflichtiger',
                     'empfaenger', 'empfänger', 'name']:
            if key in data and data[key]:
                merchant = data[key]
                break
        # Verwendungszweck
        zweck = ''
        for key in ['verwendungszweck', 'buchungstext']:
            if key in data and data[key]:
                zweck = data[key]
                break
        results.append({
            'betrag': abs(betrag),
            'is_income': betrag > 0,
            'datum': datum,
            'merchant': _clean_merchant(merchant) or zweck[:60] or 'Sparkasse',
            'verwendungszweck': zweck,
            'beteiligter': merchant,
            'bank': 'sparkasse',
            'konto': 'Sparkasse',
        })
    return results


def parse_csv_n26(content):
    """Parse N26 CSV (comma-separated, UTF-8, English headers).
    Returns list of transaction dicts.
    """
    if isinstance(content, bytes):
        content = content.decode('utf-8')
    results = []
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        # Amount
        betrag_str = row.get('Amount (EUR)', row.get('amount', ''))
        betrag = _parse_amount(betrag_str)
        if betrag is None:
            continue
        # Date
        datum = row.get('Date', row.get('date', ''))
        if datum:
            # Try YYYY-MM-DD first
            try:
                datetime.strptime(datum, '%Y-%m-%d')
            except ValueError:
                datum = _parse_date_de(datum) or datetime.now().strftime('%Y-%m-%d')
        else:
            datum = datetime.now().strftime('%Y-%m-%d')
        # Merchant
        merchant = row.get('Payee', row.get('payee', ''))
        zweck = row.get('Payment reference', row.get('reference', ''))
        results.append({
            'betrag': abs(betrag),
            'is_income': betrag > 0,
            'datum': datum,
            'merchant': _clean_merchant(merchant) or zweck[:60] or 'N26',
            'verwendungszweck': zweck,
            'beteiligter': merchant,
            'bank': 'n26',
            'konto': 'N26',
        })
    return results


# ── Auto-Kategorisierung ──────────────────────────────────────────────────────

def categorize_transaction(merchant, verwendungszweck=''):
    """Auto-categorize a bank transaction.
    Returns (kategorie, gruppe, confidence).
    """
    from kat_guesser import guess_kategorie
    combined = f"{merchant} {verwendungszweck}".strip()
    kat, gruppe, conf = guess_kategorie(combined)
    if conf >= 0.5:
        return kat, gruppe, conf
    # LLM fallback for low-confidence guesses
    try:
        from llm_router import ask as llm_ask
        prompt = (
            f"Kategorisiere diese Banktransaktion:\n"
            f"Empfänger: {merchant}\n"
            f"Verwendungszweck: {verwendungszweck}\n\n"
            f"Antworte NUR im Format: KATEGORIE|GRUPPE\n"
            f"Mögliche Gruppen: Einkauf, Restaurant, Fahrzeug, Verkehr, Gesundheit, "
            f"Unterhaltung, Freizeit, Haushalt, Abonnements, Wohnen, Urlaub, "
            f"Körperpflege, Sonstiges, Ratenzahlung, Kinder, Versicherung, Nebenkosten"
        )
        reply, _ = llm_ask(prompt, system="Kategorisiere kurz und präzise.",
                           max_tokens=30, timeout=8)
        if '|' in reply:
            parts = reply.strip().split('|')
            return parts[0].strip(), parts[1].strip(), 0.7
    except Exception as e:
        print(f"[bank_cat_llm] {e}")
    return kat, gruppe, conf


# ── Duplikat-Erkennung ─────────────────────────────────────────────────────────

def is_duplicate_bank(buchungen, new_entry, window_days=3):
    """Check if a transaction already exists in buchungen.
    Matches by: date (within window), amount (exact), similar merchant.
    """
    new_date = new_entry.get('datum', '')
    new_betrag = abs(new_entry.get('betrag', 0))
    new_merchant = (new_entry.get('merchant', '') or new_entry.get('notiz', '')).lower()
    try:
        nd = datetime.strptime(new_date, '%Y-%m-%d')
    except:
        return False
    for b in buchungen:
        try:
            bd = datetime.strptime(b.get('datum', ''), '%Y-%m-%d')
        except:
            continue
        # Date within window
        if abs((nd - bd).days) > window_days:
            continue
        # Amount match (exact to cent)
        b_betrag = abs(b.get('betrag', 0))
        if abs(b_betrag - new_betrag) > 0.02:
            continue
        # Merchant similarity (simple substring check)
        b_notiz = (b.get('notiz', '') or b.get('beteiligter', '')).lower()
        if not new_merchant or not b_notiz:
            # Amount + date match is enough if one side has no merchant
            return True
        # Check if merchant names overlap
        words_new = set(new_merchant.split())
        words_old = set(b_notiz.split())
        overlap = words_new & words_old
        if overlap and len(overlap) >= 1:
            return True
        # Short merchant name: substring match
        if len(new_merchant) >= 3 and (new_merchant in b_notiz or b_notiz in new_merchant):
            return True
    return False
