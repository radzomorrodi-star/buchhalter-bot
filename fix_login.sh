#!/usr/bin/env bash
#
# fix_login.sh — erneuert den abgelaufenen Claude/LLM-OAuth-Token für den
# MAMAD / OpenClaw Bot und startet den Dienst sauber neu.
#
# Hintergrund / Symptom:
#   ❌ LLMUnavailableError: Alle LLM-Provider versagt:
#      anthropic: claude CLI exit 1: Not logged in · Please run /login
#
# Ursache: der Max-Plan-OAuth-Token ist abgelaufen. Kein Code-Bug — reiner
# Auth-Ablauf. Dieses Skript erledigt die *automatisierbaren* Teile:
#   1) Token interaktiv neu generieren (einmaliger Browser-Login)
#   2) Token in die .env des Bots schreiben (idempotent, mit Backup)
#   3) Dienst neu starten + Health-Check
#
# WICHTIGER STOLPERSTEIN, den dieses Skript abfängt:
#   Der Login MUSS als der *Service-User* laufen — der Dienst liest dessen
#   $HOME/.claude. Wer als root einloggt, während der Dienst als 'hoy' läuft,
#   sieht weiterhin "Not logged in". Darum: alles über  sudo -u "$HOY_USER".
#
# Aufruf auf dem Server (als root):
#   sudo bash fix_login.sh
#
# Defaults sind auf das bekannte Setup gesetzt, per Env-Var überschreibbar:
#   HOY_USER   (default: hoy)
#   ENV_FILE   (default: /opt/hoy/coding_bot/.env)
#   SERVICE    (default: mamadclaudecodebot)
#   TOKEN_VAR  (default: ANTHROPIC_AUTH_TOKEN)
#
# Token bereits vorhanden? Dann Generierung überspringen:
#   sudo ANTHROPIC_AUTH_TOKEN=sk-ant-oat01-... bash fix_login.sh

set -euo pipefail

HOY_USER="${HOY_USER:-hoy}"
ENV_FILE="${ENV_FILE:-/opt/hoy/coding_bot/.env}"
SERVICE="${SERVICE:-mamadclaudecodebot}"
TOKEN_VAR="${TOKEN_VAR:-ANTHROPIC_AUTH_TOKEN}"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

# ── 0. Vorbedingungen ───────────────────────────────────────────────
if [[ "${EUID}" -ne 0 ]]; then
  red "Bitte als root ausführen:  sudo bash fix_login.sh"
  exit 1
fi

if ! id "${HOY_USER}" &>/dev/null; then
  red "User '${HOY_USER}' existiert nicht. Setze HOY_USER=... passend."
  exit 1
fi

if ! sudo -u "${HOY_USER}" -i bash -lc 'command -v claude >/dev/null'; then
  red "claude CLI für User '${HOY_USER}' nicht gefunden."
  red "Installiere die Claude CLI für ${HOY_USER} und versuch es erneut."
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  red ".env nicht gefunden: ${ENV_FILE}  (setze ENV_FILE=... passend)"
  exit 1
fi

bold "🔧 Claude-Login-Reparatur für ${SERVICE}"
echo "   User:    ${HOY_USER}"
echo "   .env:    ${ENV_FILE}"
echo "   Service: ${SERVICE}"
echo "   Var:     ${TOKEN_VAR}"
echo

# ── 1. Token besorgen ───────────────────────────────────────────────
TOKEN="${ANTHROPIC_AUTH_TOKEN:-}"

if [[ -z "${TOKEN}" ]]; then
  bold "1) Token generieren — ein einmaliger Browser-Login ist nötig."
  echo "   Es startet jetzt 'claude setup-token' als User '${HOY_USER}'."
  echo "   → Öffne die angezeigte Auth-URL (Handy/Laptop), logge dich mit dem"
  echo "     Max-Plan-Account ein, und kopiere den ausgegebenen Token."
  echo
  read -r -p "   Weiter mit [Enter] (oder Strg-C zum Abbrechen) ..."
  echo
  # Interaktiv am Terminal — gibt URL aus, wartet auf Login, druckt Token.
  # Falls deine claude-Version 'setup-token' nicht kennt: stattdessen
  #   sudo -u "${HOY_USER}" -i claude   → dann im REPL  /login
  sudo -u "${HOY_USER}" -i claude setup-token || {
    red "setup-token fehlgeschlagen. Alternativ manuell:  sudo -u ${HOY_USER} -i claude  → /login"
    exit 1
  }
  echo
  read -r -p "   Token hier einfügen (sk-ant-...): " TOKEN
fi

# Whitespace entfernen, validieren
TOKEN="$(printf '%s' "${TOKEN}" | tr -d '[:space:]')"
if [[ -z "${TOKEN}" || "${TOKEN}" != sk-ant-* ]]; then
  red "Ungültiger Token (muss mit 'sk-ant-' beginnen). Abbruch — .env unverändert."
  exit 1
fi

# ── 2. .env aktualisieren (idempotent, mit Backup) ──────────────────
bold "2) Token in ${ENV_FILE} schreiben"
backup="${ENV_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
cp -a "${ENV_FILE}" "${backup}"
echo "   Backup: ${backup}"

tmp="$(mktemp)"
# Vorhandene Zeile(n) der Var entfernen, dann frisch anhängen
grep -v -E "^[[:space:]]*${TOKEN_VAR}=" "${ENV_FILE}" > "${tmp}" || true
printf '%s=%s\n' "${TOKEN_VAR}" "${TOKEN}" >> "${tmp}"
# cat-in-place erhält Owner/Permissions der bestehenden Datei
cat "${tmp}" > "${ENV_FILE}"
rm -f "${tmp}"
chmod 600 "${ENV_FILE}" || true
green "   ${TOKEN_VAR} gesetzt (Wert maskiert: ${TOKEN:0:14}…)."

# ── 3. Dienst neu starten + Health-Check ────────────────────────────
bold "3) Dienst neu starten"
systemctl restart "${SERVICE}"
sleep 2

if systemctl is-active --quiet "${SERVICE}"; then
  green "✅ ${SERVICE} läuft."
else
  red "❌ ${SERVICE} ist nicht aktiv — letzte Logs:"
  journalctl -u "${SERVICE}" -n 30 --no-pager || true
  red "Backup liegt unter ${backup} — bei Bedarf zurückspielen."
  exit 1
fi

bold "4) Letzte Logs"
journalctl -u "${SERVICE}" -n 15 --no-pager || true
echo
green "Fertig. Test im Telegram-Chat:  /frage test   (Antwort sollte 🟣 anthropic zeigen)"
