"""
llm_router.py — robuste Multi-Provider LLM-Rotation (Hermes / MAMAD / OpenClaw).

Ziel: Es ist IMMER ein LLM verfügbar. Die Rotation probiert mehrere
Provider-*Instanzen* der Reihe nach durch und fällt bei jedem Fehler sauber auf
die nächste zurück. Solange MINDESTENS eine Instanz funktioniert, antwortet der
Bot — selbst wenn ein Anthropic-Max-Login abläuft.

NEU (Hermes): mehrere Instanzen DESSELBEN Provider-Typs möglich, z.B.
2× Anthropic Max-Plan (jeweils eigener OAuth-Token / eigenes Config-Dir) plus
bis zu 5 API-Key-Provider — alles in EINER Fallback-Kette.

Öffentliche API (unverändert, vom Bot erwartet):
    ask(prompt, system=None, max_tokens=512, timeout=30, tools=None) -> (text, family)
    load_keys() -> dict   (flach: {"openai": "...", "groq": "..."} — für OCR)
    available_providers() -> list[str]
    LLMUnavailableError

Konfiguration (Priorität von oben nach unten):
    1. keys.json (reich ODER flach) aus $LLM_KEYS_FILE, sonst
       ~/.buchhalter-bot/keys.json bzw. /opt/hoy/coding_bot/keys.json
    2. Umgebungsvariablen (GROQ_API_KEY, OPENAI_API_KEY, … siehe _ENV_KEYS)

Reiches keys.json-Format (empfohlen für Hermes) — siehe keys.example.json:
    {
      "providers": [
        {"name": "groq",        "kind": "groq",          "key": "gsk_..."},
        {"name": "openai",      "kind": "openai",        "key": "sk-..."},
        {"name": "gemini",      "kind": "gemini",        "key": "..."},
        {"name": "deepseek",    "kind": "deepseek",      "key": "..."},
        {"name": "grok",        "kind": "grok",          "key": "xai-..."},
        {"name": "claude-max-1","kind": "anthropic-cli", "oauth_token": "sk-ant-oat01-...A"},
        {"name": "claude-max-2","kind": "anthropic-cli", "oauth_token": "sk-ant-oat01-...B"}
      ],
      "order": ["groq", "claude-max-1", "claude-max-2", "openai", "gemini", "deepseek", "grok"]
    }
"""

import json
import os
import subprocess
import time
from shutil import which

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class LLMUnavailableError(Exception):
    """Wird geworfen, wenn ALLE Provider-Instanzen fehlschlagen."""


# ── Provider-Typen (Templates) ──────────────────────────────────────
# OpenAI-kompatible Endpunkte teilen denselben /chat/completions-Aufruf.
_OPENAI_KINDS = {
    "groq":     {"base": "https://api.groq.com/openai/v1",
                 "model": "llama-3.3-70b-versatile", "family": "groq"},
    "openai":   {"base": "https://api.openai.com/v1",
                 "model": "gpt-4o-mini", "family": "openai"},
    "gemini":   {"base": "https://generativelanguage.googleapis.com/v1beta/openai",
                 "model": "gemini-2.0-flash", "family": "gemini"},
    "deepseek": {"base": "https://api.deepseek.com/v1",
                 "model": "deepseek-chat", "family": "deepseek"},
    "grok":     {"base": "https://api.x.ai/v1",
                 "model": "grok-2-latest", "family": "grok"},
}
_ANTHROPIC_MODEL = "claude-3-5-sonnet-latest"

# Env-Vars je Provider-Familie (erster Treffer gewinnt).
_ENV_KEYS = {
    "groq": ["GROQ_API_KEY", "LLM_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "grok": ["XAI_API_KEY", "GROK_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
}

_DEFAULT_ORDER = ["groq", "anthropic", "openai", "gemini", "deepseek", "grok"]

# Kurz-Cooldown: eine gerade gescheiterte Instanz wird ans Ende sortiert
# (nicht entfernt!), damit ein toter Provider nicht jede Anfrage ausbremst,
# aber weiterhin als letzter probiert wird -> "immer ein LLM verfügbar".
_COOLDOWN_SECONDS = 60
_cooldown = {}  # instance-name -> unix_ts


# ── Konfiguration einlesen ──────────────────────────────────────────

def _keys_file_path():
    explicit = os.environ.get("LLM_KEYS_FILE")
    for p in ([explicit] if explicit else []) + [
        os.path.expanduser("~/.buchhalter-bot/keys.json"),
        "/opt/hoy/coding_bot/keys.json",
    ]:
        if p and os.path.isfile(p):
            return p
    return None


def _normalize_kind(kind, key="", oauth_token=""):
    """Mappt Aliase und entscheidet anthropic-api vs anthropic-cli."""
    k = (kind or "").lower().strip()
    if k in ("xai", "x.ai", "x-ai"):
        return "grok"
    if k in ("google",):
        return "gemini"
    if k in ("anthropic", "claude", "anthropic-api", "anthropic-cli"):
        if k == "anthropic-cli":
            return "anthropic-cli"
        if k == "anthropic-api":
            return "anthropic-api"
        # generisch: echter API-Key -> API, sonst CLI (OAuth/Subscription)
        if key.startswith("sk-ant-api"):
            return "anthropic-api"
        return "anthropic-cli"
    return k


def _make_instance(entry):
    """Baut eine Instanz-Definition aus einem reichen Config-Eintrag."""
    raw_kind = entry.get("kind") or entry.get("provider") or ""
    key = (entry.get("key") or entry.get("api_key") or "").strip()
    oauth = (entry.get("oauth_token") or entry.get("token") or "").strip()
    config_dir = (entry.get("config_dir") or "").strip()
    kind = _normalize_kind(raw_kind, key, oauth)

    if kind in _OPENAI_KINDS:
        family = _OPENAI_KINDS[kind]["family"]
    elif kind in ("anthropic-api", "anthropic-cli"):
        family = "anthropic"
    else:
        return None  # unbekannter Typ -> ignorieren

    name = (entry.get("name") or kind).strip()
    return {
        "name": name,
        "kind": kind,
        "family": family,
        "key": key,
        "oauth_token": oauth,
        "config_dir": config_dir,
        "model": (entry.get("model") or "").strip(),
        "base": (entry.get("base") or "").strip(),
    }


def _instances_from_env_and_flat(flat):
    """Backward-compat: je Familie eine Instanz aus flachem Dict + Env-Vars."""
    insts = []
    for family in _DEFAULT_ORDER:
        if family == "anthropic":
            key = flat.get("anthropic", "")
            if key and key.startswith("sk-ant-api"):
                insts.append(_make_instance({"name": "anthropic", "kind": "anthropic-api", "key": key}))
            elif key and key.startswith("sk-ant-oat"):
                insts.append(_make_instance({"name": "anthropic", "kind": "anthropic-cli", "oauth_token": key}))
            elif which("claude"):
                # API-Key fehlt, aber Subscription-CLI ist da.
                insts.append(_make_instance({"name": "anthropic", "kind": "anthropic-cli"}))
        else:
            key = flat.get(family, "")
            if key:
                insts.append(_make_instance({"name": family, "kind": family, "key": key}))
    return [i for i in insts if i]


def _load_config():
    """Liefert (instances_in_order, flat_keys_dict)."""
    flat = {}
    rich = None

    # 1. keys.json
    path = _keys_file_path()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("providers"), list):
                rich = data
            elif isinstance(data, dict):
                for prov, val in data.items():
                    if val:
                        flat[prov.lower()] = str(val).strip()
        except Exception as e:  # pragma: no cover
            print(f"[llm_router] keys.json ({path}) nicht lesbar: {e}")

    # 2. Env-Vars (ergänzen flach, ohne reiches Config zu überschreiben)
    for family, names in _ENV_KEYS.items():
        if family in flat:
            continue
        for name in names:
            v = os.environ.get(name)
            if v and v.strip():
                flat[family] = v.strip()
                break

    # Instanzen bauen
    if rich:
        insts = [i for i in (_make_instance(e) for e in rich["providers"]) if i]
        order = rich.get("order")
    else:
        insts = _instances_from_env_and_flat(flat)
        order = None

    # Reihenfolge: explizit (order) > Env LLM_PROVIDER_ORDER > Config-Reihenfolge
    if not order:
        env_order = os.environ.get("LLM_PROVIDER_ORDER", "")
        order = [p.strip().lower() for p in env_order.split(",") if p.strip()] or None

    if order:
        idx = {n.lower(): k for k, n in enumerate(order)}
        def rank(inst):
            return idx.get(inst["name"].lower(),
                           idx.get(inst["family"].lower(), len(order)))
        insts = sorted(insts, key=rank)

    # flat aus Instanzen ableiten (für load_keys / OCR), Datei-flat ergänzen
    for inst in insts:
        if inst["key"] and inst["family"] not in flat:
            flat[inst["family"]] = inst["key"]

    return insts, flat


# ── Öffentliche Helfer ──────────────────────────────────────────────

def load_keys():
    """Flaches {familie: key}-Dict — vom Bot für OCR/Whisper genutzt."""
    _, flat = _load_config()
    return flat


def available_providers():
    """Instanz-Namen in Rotations-Reihenfolge, die einsetzbar sind."""
    insts, _ = _load_config()
    out = []
    for i in insts:
        if i["kind"] in _OPENAI_KINDS and not i["key"]:
            continue
        if i["kind"] == "anthropic-api" and not i["key"]:
            continue
        if i["kind"] == "anthropic-cli" and not (i["oauth_token"] or which("claude")):
            continue
        out.append(i["name"])
    return out


# ── Provider-Aufrufe ────────────────────────────────────────────────

def _augment_system_for_tools(system, tools):
    """Der Bot parst JSON aus dem Text — also weisen wir das Modell an, NUR ein
    passendes JSON-Objekt auszugeben (portabel über alle Provider)."""
    if not tools:
        return system
    try:
        schema_str = json.dumps(tools[0]["function"]["parameters"], ensure_ascii=False)
    except Exception:
        schema_str = ""
    instr = ("Antworte AUSSCHLIESSLICH mit EINEM gültigen JSON-Objekt, "
             "ohne Markdown, ohne Erklärung, ohne ```-Fences.")
    if schema_str:
        instr += f" Es muss diesem JSON-Schema entsprechen: {schema_str}"
    return f"{system}\n\n{instr}" if system else instr


def _call_openai_compatible(inst, prompt, system, max_tokens, timeout):
    tmpl = _OPENAI_KINDS[inst["kind"]]
    base = inst["base"] or tmpl["base"]
    model = inst["model"] or os.environ.get(f"{inst['kind'].upper()}_MODEL", "") or tmpl["model"]
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {inst['key']}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages,
              "max_tokens": max_tokens, "temperature": 0.4},
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
    return resp.json()["choices"][0]["message"]["content"]


def _call_anthropic_api(inst, prompt, system, max_tokens, timeout):
    model = inst["model"] or os.environ.get("ANTHROPIC_MODEL", "") or _ANTHROPIC_MODEL
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": inst["key"], "anthropic-version": "2023-06-01",
                 "Content-Type": "application/json"},
        json=body, timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
    data = resp.json()
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _call_claude_cli(inst, prompt, system, timeout):
    """Anthropic Max-Plan via lokale Claude-CLI. Jede Instanz kann ihren eigenen
    OAuth-Token (CLAUDE_CODE_OAUTH_TOKEN) und/oder ihr eigenes Config-Dir
    (CLAUDE_CONFIG_DIR) haben -> mehrere Max-Accounts parallel rotierbar."""
    if not which("claude"):
        raise RuntimeError("claude CLI nicht installiert")
    env = os.environ.copy()
    if inst["oauth_token"]:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = inst["oauth_token"]
        env.pop("ANTHROPIC_API_KEY", None)  # Konflikt vermeiden
    if inst["config_dir"]:
        env["CLAUDE_CONFIG_DIR"] = inst["config_dir"]
    full = f"{system}\n\n{prompt}" if system else prompt
    try:
        proc = subprocess.run(["claude", "--print", full],
                              capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude CLI Timeout")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:160]
        raise RuntimeError(f"claude CLI exit {proc.returncode}: {err or 'unbekannt'}")
    return proc.stdout.strip()


def _call_instance(inst, prompt, system, max_tokens, timeout):
    if inst["kind"] in _OPENAI_KINDS:
        return _call_openai_compatible(inst, prompt, system, max_tokens, timeout)
    if inst["kind"] == "anthropic-api":
        return _call_anthropic_api(inst, prompt, system, max_tokens, timeout)
    if inst["kind"] == "anthropic-cli":
        return _call_claude_cli(inst, prompt, system, timeout)
    raise RuntimeError(f"unbekannter Typ: {inst['kind']}")


# ── Rotation ────────────────────────────────────────────────────────

def ask(prompt, system=None, max_tokens=512, timeout=30, tools=None, **_):
    """Fragt Instanzen der Reihe nach an, bis eine antwortet.

    Returns: (text, family)   family z.B. "groq" / "anthropic" (für Emoji im Bot)
    Raises:  LLMUnavailableError, wenn ALLE Instanzen scheitern.
    """
    if requests is None:
        raise LLMUnavailableError("Modul 'requests' fehlt — pip install requests")

    insts, _ = _load_config()
    # nur einsatzfähige Instanzen
    usable = []
    for i in insts:
        if i["kind"] in _OPENAI_KINDS and not i["key"]:
            continue
        if i["kind"] == "anthropic-api" and not i["key"]:
            continue
        if i["kind"] == "anthropic-cli" and not (i["oauth_token"] or which("claude")):
            continue
        usable.append(i)

    if not usable:
        raise LLMUnavailableError(
            "Kein LLM konfiguriert. Setze z.B. GROQ_API_KEY (gratis) oder lege "
            "Provider in keys.json an (siehe keys.example.json)."
        )

    # Gescheiterte Instanzen (Cooldown) ans Ende — aber nicht entfernen.
    now = time.time()
    usable.sort(key=lambda i: _cooldown.get(i["name"], 0) > now)

    system = _augment_system_for_tools(system, tools)

    fehler = []
    for inst in usable:
        try:
            text = _call_instance(inst, prompt, system, max_tokens, timeout)
            if not text or not text.strip():
                raise RuntimeError("leere Antwort")
            _cooldown.pop(inst["name"], None)
            return text.strip(), inst["family"]
        except Exception as e:
            _cooldown[inst["name"]] = time.time() + _COOLDOWN_SECONDS
            msg = (str(e).splitlines()[0][:160] if str(e) else e.__class__.__name__)
            fehler.append(f"{inst['name']}: {msg}")
            print(f"[llm_router] {inst['name']} fehlgeschlagen: {msg}")
            continue

    raise LLMUnavailableError("Alle LLM-Provider versagt: " + " · ".join(fehler))


if __name__ == "__main__":
    import sys
    print("Verfügbare Instanzen:", available_providers() or "KEINE (Keys fehlen)")
    frage = sys.argv[1] if len(sys.argv) > 1 else "Antworte mit genau einem Wort: OK"
    try:
        antwort, fam = ask(frage, max_tokens=50, timeout=30)
        print(f"[{fam}] -> {antwort}")
    except LLMUnavailableError as e:
        print(f"FEHLER: {e}")
