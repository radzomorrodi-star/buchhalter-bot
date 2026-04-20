# Buchhalter-Bot

Ein schlanker, selbst-gehosteter Telegram-Bot für persönliche Buchhaltung und Ausgabenverfolgung. Fokus liegt auf **mechanischer Zuverlässigkeit** — Buchungen werden ohne LLM gespeichert und abgerufen, KI wird nur gezielt für Smart-Fragen, OCR und Kategorie-Vorschläge eingesetzt.

## Features

- **Mechanische Buchungen** — kein LLM, keine Halluzinationen. Alle Ausgaben werden deterministisch in JSON gespeichert, mit Datum, Betrag, Kategorie, Gruppe und Notiz.
- **Kategorie-Rater** (`kat_guesser.py`) — regelbasierter Klassifizierer für deutsche Vertragspartner: erkennt automatisch ob es um Lebensmittel (Edeka, Rewe, Aldi...), Tanken (Shell, Aral...), Transport (HVV, DB...), Versicherungen oder Abos geht. Zero LLM, zero Kosten.
- **Dauerauftrags-Tracker** — wiederkehrende Kosten (Miete, Abos, Versicherungen) werden automatisch jeden Monat vorgebucht. Vermisste Monate werden bei Aufruf nachgeholt.
- **Bank-CSV-Import** — Parser für deutsche Bank-Export-Formate (ING, DKB, Sparkasse, etc.) zum Bulk-Import von Kontoauszügen.
- **OCR für Kassenbons** — schicke ein Foto, der Bot extrahiert Betrag und schlägt Kategorie vor.
- **Voice-Commands** — Sprachnachricht "35 Euro Tanken" → Buchung.
- **Smart-Fragen via LLM** — nur auf Anforderung: "Top 5 Kategorien diesen Monat", "Vergleich mit Vormonat", etc.
- **iCloud-Kalender-Readonly** — integriert Termine (optional) in die Wochenübersicht.
- **Channel-Integration** — bestimmte Telegram-Usernames dürfen Kassenbons in einen geteilten Channel posten, werden automatisch gebucht.

## Tech-Stack

- Python 3.12+
- `pyTelegramBotAPI` (telebot)
- `caldav` (optional, für iCloud-Kalender)
- JSON-basierte Persistenz (keine Datenbank nötig)
- OpenAI-kompatible API für Smart-Fragen und OCR (Groq, OpenAI, oder lokales LLM)

## Setup

```bash
# 1. Clone
git clone https://github.com/radzomorrodi-star/buchhalter-bot.git
cd buchhalter-bot

# 2. Dependencies
pip install -r requirements.txt

# 3. Env-Variablen kopieren und ausfüllen
cp .env.example .env
# → BOT_TOKEN, OWNER_CHAT_ID und optional CHANNEL_ID setzen

# 4. Starten
export $(cat .env | xargs)
python bot.py
```

## Env-Variablen

| Variable | Pflicht | Beschreibung |
|----------|---------|-------------|
| `BOT_TOKEN` | ✓ | Telegram-Bot-Token von @BotFather |
| `OWNER_CHAT_ID` | ✓ | Deine Telegram-Chat-ID (numerisch). User-only Auth — nur der Owner darf den Bot nutzen. |
| `CHANNEL_ID` | – | Optional: Channel-ID für geteilte Kassenbons |
| `CHANNEL_USERS` | – | Komma-separierte Telegram-Usernames die Bons im Channel posten dürfen |
| `CALDAV_CONFIG_PATH` | – | Optional: Pfad zu iCloud-CalDAV-Config für Kalender-Integration |
| `LLM_API_KEY` | – | Für Smart-Fragen/OCR. Funktioniert mit Groq/OpenAI-kompatiblen APIs. |

## Dateistruktur

```
buchhalter-bot/
├── bot.py                  # Haupt-Entry, Telegram-Handler, Menüs
├── dauerauftrag.py         # Dauerauftrags-CRUD, Auto-Buchung
├── auto_da.py              # Cron-artiger Auto-Runner für Dauerauftrags-Buchung
├── kat_guesser.py          # Regelbasierter Kategorie-Rater (deutsche Vertragspartner)
├── bank_parser.py          # CSV-Parser für deutsche Bank-Exports
├── kalender.py             # iCloud CalDAV Read-Only-Client (optional)
├── shared/media_archive.py # Einheitliche Media-Archivierung für Fotos/Voices
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

Daten werden persistent in `~/.buchhalter-bot/` gespeichert (JSON-Dateien, nicht im Repo).

## Design-Prinzipien

- **Mechanik vor Magic** — LLM nur wo es genuinen Mehrwert bringt (Natural-Language-Fragen, OCR). Alles andere ist deterministisch.
- **Single-User** — absichtlich kein Multi-Tenant-Design. `OWNER_CHAT_ID` schließt alle anderen aus. Sicher & simpel.
- **Stateless Persistence** — keine Datenbank, keine Migrations. JSON-Dateien mit atomic-write (temp-file + rename + lock).
- **Offline-fähig** — Bot funktioniert ohne LLM. Buchen, Abfragen, Daueraufträge laufen rein lokal. Nur Smart-Fragen und OCR brauchen Internet.

## Kategorien

Eingebaute Kategorien (anpassbar via `kat_guesser.py` oder über Runtime in der App):

🛒 Lebensmittel · 🍕 Essen · ⛽ Tanken · 🚇 Transport · 💊 Apotheke · 🃏 Poker · 👕 Kleidung · 💡 Strom/Gas · 📱 Abo/Software · 🏠 Miete · ✈️ Reise · 💇 Friseur · 🎮 Freizeit · und weitere.

## Beitragen

Issues und Pull Requests sind willkommen. Besonders interessant:
- Zusätzliche Bank-Parser (Trade Republic, N26, Revolut, etc.)
- Weitere Kategorie-Rules für deutsche Vertragspartner
- Export-Formate (CSV, Excel, DATEV)

## Lizenz

MIT — siehe [LICENSE](LICENSE).
