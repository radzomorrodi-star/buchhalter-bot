"""
llm_router.py — robuste Multi-Provider LLM-Rotation für den MAMAD / OpenClaw Bot.

Ziel: Es ist IMMER ein LLM verfügbar. Die Rotation probiert mehrere Provider
der Reihe nach durch und fällt bei jedem Fehler sauber auf den nächsten zurück.
Solange MINDESTENS ein Provider einen gültigen Key/Login hat, antwortet der Bot.

Warum es vorher ausfiel ("Alle LLM-Provider versagt: anthropic: claude CLI exit 1:
Not logged in"): die Rotation hing faktisch am interaktiven Claude-CLI-Login —
läuft der ab, geht alles dunkel. Lösung: ein API-Key-Provider OHNE Browser-Login
(Groq, gratis & schnell) steht an erster Stelle und garantiert den Fallback.

Öffentliche API (vom Bot erwartet):
    ask(prompt, system=None, max_tokens=512, timeout=30, tools=None) -> (text, provider)
    load_keys() -> dict  z.B. {"groq": "...", "openai": "...", ...}
    LLMUnavailableError

Keys werden gelesen aus (Reihenfolge = Priorität):
    1. Umgebungsvariablen (siehe _ENV_KEYS unten)
    2. JSON-Datei aus $LLM_KEYS_FILE, sonst ~/.buchhalter-bot/keys.json
       bzw. /opt/hoy/coding_bot/keys.json  (Format: {"groq": "...", ...})

Reihenfolge der Rotation: $LLM_PROVIDER_ORDER (Komma-separiert),
Default: groq,anthropic,openai,gemini,deepseek,grok
"""

import json
import os
import subprocess
import time

try:
    import requests
except ImportError:  # pragma: no cover - requests ist Standard-Dependency
    requests = None


class LLMUnavailableError(Exception):
    """Wird geworfen, wenn ALLE Provider fehlschlagen."""


# ── Provider-Definitionen ───────────────────────────────────────────
# Alle "openai-kompatiblen" Provider teilen denselben /chat/completions-Aufruf.
_PROVIDERS = {
    "groq": {
        "kind": "openai",
        "base": "https://api.groq.com/openai/v1",
        "model_env": "GROQ_MODEL",
        "model": "llama-3.3-70b-versatile",
    },
    "openai": {
        "kind": "openai",
        "base": "https://api.openai.com/v1",
        "model_env": "OPENAI_MODEL",
        "model": "gpt-4o-mini",
    },
    "gemini": {
        "kind": "openai",
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_env": "GEMINI_MODEL",
        "model": "gemini-2.0-flash",
    },
    "deepseek": {
        "kind": "openai",
        "base": "https://api.deepseek.com/v1",
        "model_env": "DEEPSEEK_MODEL",
        "model": "deepseek-chat",
    },
    "grok": {
        "kind": "openai",
        "base": "https://api.x.ai/v1",
        "model_env": "GROK_MODEL",
        "model": "grok-2-latest",
    },
    "anthropic": {
        "kind": "anthropic",
        "base": "https://api.anthropic.com/v1",
        "model_env": "ANTHROPIC_MODEL",
        "model": "claude-3-5-sonnet-latest",
    },
}

# Umgebungsvariablen-Namen je Provider (erster Treffer gewinnt).
_ENV_KEYS = {
    "groq": ["GROQ_API_KEY", "LLM_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "grok": ["XAI_API_KEY", "GROK_API_KEY"],
    # sk-ant-api... -> echte API-Keys.  sk-ant-oat... (OAuth) nutzt die CLI.
    "anthropic": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
}

_DEFAULT_ORDER = ["groq", "anthropic", "openai", "gemini", "deepseek", "grok"]

# Kurz-Cooldown: ein gerade gescheiterter Provider wird für N Sekunden ans Ende
# sortiert, damit ein toter Provider nicht jede Anfrage ausbremst. Er wird aber
# weiterhin als letzter probiert — so bleibt "immer ein LLM verfügbar".
_COOLDOWN_SECONDS = 60
_cooldown = {}  # provider -> unix_ts bis wann gemieden


# ── Key-Verwaltung ──────────────────────────────────────────────────

def _keys_file_path():
    explicit = os.environ.get("LLM_KEYS_FILE")
    candidates = [explicit] if explicit else []
    candidates += [
        os.path.expanduser("~/.buchhalter-bot/keys.json"),
        "/opt/hoy/coding_bot/keys.json",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def load_keys():
    """Sammelt alle verfügbaren Provider-Keys. Env-Vars haben Vorrang vor Datei."""
    keys = {}

    # 1. JSON-Datei (füllt Lücken)
    path = _keys_file_path()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for prov, val in data.items():
                    if val:
                        keys[prov.lower()] = str(val).strip()
        except Exception as e:  # pragma: no cover
            print(f"[llm_router] keys.json ({path}) nicht lesbar: {e}")

    # 2. Umgebungsvariablen (überschreiben Datei)
    for prov, names in _ENV_KEYS.items():
        for name in names:
            val = os.environ.get(name)
            if val and val.strip():
                keys[prov] = val.strip()
                break

    return keys


def _claude_cli_available():
    """anthropic via Subscription-CLI nutzbar, auch ohne API-Key."""
    from shutil import which
    return which("claude") is not None


def _provider_order():
    raw = os.environ.get("LLM_PROVIDER_ORDER", "")
    order = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if not order:
        order = list(_DEFAULT_ORDER)
    # Unbekannte rausfiltern, fehlende Defaults hinten anhängen
    order = [p for p in order if p in _PROVIDERS]
    for p in _DEFAULT_ORDER:
        if p not in order:
            order.append(p)
    return order


def available_providers():
    """Provider in Rotations-Reihenfolge, die tatsächlich einsetzbar sind."""
    keys = load_keys()
    result = []
    for prov in _provider_order():
        if keys.get(prov):
            result.append(prov)
        elif prov == "anthropic" and _claude_cli_available():
            # API-Key fehlt, aber die Claude-CLI ist da (Subscription-Login).
            result.append(prov)
    return result


# ── Einzelne Provider-Aufrufe ───────────────────────────────────────

def _augment_system_for_tools(system, tools):
    """Der Bot parst JSON aus dem Text. Statt nativer Tool-Calls weisen wir das
    Modell an, NUR ein passendes JSON-Objekt auszugeben — portabel über alle
    Provider hinweg."""
    if not tools:
        return system
    try:
        schema = tools[0]["function"]["parameters"]
        schema_str = json.dumps(schema, ensure_ascii=False)
    except Exception:
        schema_str = ""
    instr = (
        "Antworte AUSSCHLIESSLICH mit EINEM gültigen JSON-Objekt, "
        "ohne Markdown, ohne Erklärung, ohne ```-Fences."
    )
    if schema_str:
        instr += f" Es muss diesem JSON-Schema entsprechen: {schema_str}"
    return f"{system}\n\n{instr}" if system else instr


def _call_openai_compatible(prov, prompt, system, max_tokens, timeout, key):
    cfg = _PROVIDERS[prov]
    model = os.environ.get(cfg["model_env"], "") or cfg["model"]
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = requests.post(
        f"{cfg['base']}/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.4,
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_anthropic_api(prompt, system, max_tokens, timeout, key):
    cfg = _PROVIDERS["anthropic"]
    model = os.environ.get(cfg["model_env"], "") or cfg["model"]
    # OAuth-Tokens (sk-ant-oat...) gehören NICHT an die Messages-API -> CLI.
    if key.startswith("sk-ant-oat"):
        raise RuntimeError("OAuth-Token gehört zur CLI, nicht zur Messages-API")
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    resp = requests.post(
        f"{cfg['base']}/messages", headers=headers, json=body, timeout=timeout
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(parts)


def _call_claude_cli(prompt, system, timeout):
    """Letzter Ausweg: die lokale Claude-CLI (Subscription-Login)."""
    full = f"{system}\n\n{prompt}" if system else prompt
    try:
        proc = subprocess.run(
            ["claude", "--print", full],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError("claude CLI nicht installiert")
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude CLI Timeout")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:200]
        raise RuntimeError(f"claude CLI exit {proc.returncode}: {err or 'unbekannt'}")
    return proc.stdout.strip()


def _call_provider(prov, prompt, system, max_tokens, timeout, key):
    cfg = _PROVIDERS[prov]
    if cfg["kind"] == "openai":
        return _call_openai_compatible(prov, prompt, system, max_tokens, timeout, key)
    if cfg["kind"] == "anthropic":
        if key:
            return _call_anthropic_api(prompt, system, max_tokens, timeout, key)
        return _call_claude_cli(prompt, system, timeout)
    raise RuntimeError(f"unbekannter Provider-Typ: {cfg['kind']}")


# ── Öffentliche Rotation ────────────────────────────────────────────

def ask(prompt, system=None, max_tokens=512, timeout=30, tools=None, **_):
    """Fragt der Reihe nach Provider an, bis einer antwortet.

    Returns: (text, provider_name)
    Raises:  LLMUnavailableError, wenn ALLE Provider scheitern.
    """
    if requests is None:
        raise LLMUnavailableError("Modul 'requests' fehlt — pip install requests")

    keys = load_keys()
    candidates = available_providers()
    if not candidates:
        raise LLMUnavailableError(
            "Kein LLM konfiguriert. Setze z.B. GROQ_API_KEY (gratis) oder "
            "lege Keys in keys.json an."
        )

    # Gescheiterte Provider (Cooldown) ans Ende sortieren — aber NICHT entfernen,
    # damit weiterhin garantiert ein LLM probiert wird.
    now = time.time()
    candidates.sort(key=lambda p: _cooldown.get(p, 0) > now)

    system = _augment_system_for_tools(system, tools)

    fehler = []
    for prov in candidates:
        try:
            text = _call_provider(prov, prompt, system, max_tokens, timeout, keys.get(prov))
            if not text or not text.strip():
                raise RuntimeError("leere Antwort")
            _cooldown.pop(prov, None)  # Erfolg -> Cooldown löschen
            return text.strip(), prov
        except Exception as e:
            _cooldown[prov] = time.time() + _COOLDOWN_SECONDS
            msg = str(e).splitlines()[0][:160] if str(e) else e.__class__.__name__
            fehler.append(f"{prov}: {msg}")
            print(f"[llm_router] {prov} fehlgeschlagen: {msg}")
            continue

    raise LLMUnavailableError("Alle LLM-Provider versagt: " + " · ".join(fehler))


if __name__ == "__main__":
    # Smoke-Test:  python llm_router.py "Sag kurz Hallo"
    import sys
    print("Verfügbare Provider:", available_providers() or "KEINE (Keys fehlen)")
    frage = sys.argv[1] if len(sys.argv) > 1 else "Antworte mit genau einem Wort: OK"
    try:
        antwort, prov = ask(frage, max_tokens=50, timeout=30)
        print(f"[{prov}] -> {antwort}")
    except LLMUnavailableError as e:
        print(f"FEHLER: {e}")
