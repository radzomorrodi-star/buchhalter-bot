"""Kategorie-Rater: errät Kategorie aus Bon-Text oder Notiz."""

RULES = {
    'Lebensmittel': ['edeka', 'rewe', 'aldi', 'lidl', 'penny', 'netto', 'kaufland', 'dm', 'rossmann', 'budni', 'lebensmittel', 'supermarkt', 'bio', 'milch', 'brot', 'flink', 'gorillas', 'getir', 'picnic', 'real', 'nahkauf', 'tegut'],
    'Essen': ['restaurant', 'pizza', 'burger', 'sushi', 'kebab', 'mcdonalds', 'subway', 'lieferando', 'wolt', 'uber eats', 'cafe', 'kaffee', 'starbucks', 'bakery', 'bistro', 'imbiss', 'gastro', 'backwerk', 'dunkin', 'dean david', 'nordsee', 'vapiano'],
    'Tanken': ['shell', 'aral', 'esso', 'total', 'jet', 'tanken', 'benzin', 'diesel', 'tankstelle', 'star tankstelle', 'agip', 'bp'],
    'Strafzettel': ['strafzettel', 'bußgeld', 'bussgeld', 'knöllchen', 'verwarnung', 'verwarnungsgeld', 'ordnungswidrigkeit', 'falschparken', 'blitzer', 'geschwindigkeit', 'parkverstoß', 'parkversto'],
    'Transport': ['hvv', 'bahn', 'db', 'taxi', 'uber', 'bolt', 'moia', 'bus', 'zug', 'ticket', 'fahrkarte', 'flixbus', 'miles', 'share now', 'sixt', 'tier', 'lime', 'voi'],
    'Apotheke': ['apotheke', 'medikament', 'aspirin', 'ibuprofen', 'arzt', 'rezept', 'docmorris', 'shop-apotheke'],
    'Abo/Software': ['netflix', 'spotify', 'youtube', 'amazon prime', 'disney', 'apple', 'google', 'microsoft', 'chatgpt', 'openai', 'anthropic', 'adobe', 'hetzner', 'digitalocean', 'github', 'notion', 'dropbox', 'icloud'],
    'Miete': ['miete', 'nebenkosten', 'strom', 'gas', 'wasser', 'hausgeld', 'stadtwerke', 'vattenfall', 'eon', 'e.on', 'enercity', 'gez', 'rundfunk'],
    'Poker': ['poker', 'casino', 'esplanade', 'schenefeld', 'portier', 'buy-in'],
    'Kleidung': ['zara', 'h&m', 'uniqlo', 'nike', 'adidas', 'kleidung', 'schuhe', 'jacke', 'peek', 'cloppenburg', 'aboutyou', 'about you', 'zalando'],
    'Freizeit': ['kino', 'theater', 'museum', 'konzert', 'gym', 'fitness', 'sport', 'spiel'],
    'Versicherung': ['versicherung', 'haftpflicht', 'allianz', 'huk', 'axa', 'ergo', 'debeka', 'barmer', 'tk', 'aok', 'dak', 'krankenkasse'],
    'Internet/Handy': ['telekom', 'vodafone', 'o2', 'congstar', 'mobilfunk', 'internet', 'dsl', 'glasfaser', '1und1', '1&1', 'freenet'],
    'Shopping': ['amazon', 'ebay', 'otto', 'mediamarkt', 'saturn', 'ikea', 'paypal', 'klarna', 'temu', 'shein', 'aliexpress', 'wish'],
    'Gebühren': ['gebuehr', 'gebühr', 'kontoführung', 'kontofuehrung', 'bankgebühr', 'kartengebühr', 'auslandseinsatz', 'nachnahme'],
    'Werkstatt': ['werkstatt', 'tuev', 'tüv', 'dekra', 'atu', 'a.t.u', 'reifen', 'inspektion', 'reparatur auto'],
}

# Map Kategorie → (kategorie, gruppe)
KAT_MAP = {
    'Lebensmittel': ('Lebensmittel', 'Einkauf'),
    'Essen': ('Essen', 'Restaurant'),
    'Tanken': ('Tanken', 'Auto'),
    'Transport': ('Transport', 'Verkehr'),
    'Apotheke': ('Apotheke', 'Gesundheit'),
    'Abo/Software': ('Abo/Software', 'Digital'),
    'Miete': ('Miete', 'Wohnen'),
    'Poker': ('Poker', 'Freizeit'),
    'Kleidung': ('Kleidung', 'Shopping'),
    'Freizeit': ('Freizeit', 'Unterhaltung'),
    'Strafzettel': ('Strafzettel', 'Fahrzeug'),
    'Versicherung': ('Versicherung', 'Pflicht'),
    'Internet/Handy': ('Internet/Handy', 'Kommunikation'),
    'Shopping': ('Shopping', 'Einkauf'),
    'Gebühren': ('Gebühren', 'Bank'),
    'Werkstatt': ('Werkstatt', 'Fahrzeug'),
}

def guess_kategorie(text):
    """Errät Kategorie aus Text. Returns (kategorie, gruppe, confidence)."""
    if not text:
        return 'Lebensmittel', 'Einkauf', 0.3
    t = text.lower()
    scores = {}
    for kat, keywords in RULES.items():
        score = sum(1 for kw in keywords if kw in t)
        if score > 0:
            scores[kat] = score
    if scores:
        best = max(scores, key=scores.get)
        confidence = min(scores[best] / 2, 1.0)
        k, g = KAT_MAP.get(best, ('Sonstiges', 'Misc'))
        return k, g, confidence
    return 'Lebensmittel', 'Einkauf', 0.3
