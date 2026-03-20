# coproxy-ai

[English](../README.md) | [Русский](README.ru.md) | [Español](README.es.md) | [Français](README.fr.md) | [Українська](README.uk.md) | [Ελληνικά](README.el.md) | [中文](README.zh.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

Lokaler Proxy, mit dem Sie Ihr **ChatGPT Plus/Pro/Team-Abo** als OpenAI-API nutzen können — ohne API-Kosten.

```
Ihre App → coproxy (localhost:8765) → api.openai.com
              ↑ nutzt Ihre ChatGPT-OAuth-Token
```

## Funktionsweise

1. Sie melden sich mit Ihrem ChatGPT-Konto an (einmaliger Device-Code-Flow)
2. coproxy erhält und erneuert automatisch einen API-Key aus Ihrer OAuth-Sitzung
3. Jeder OpenAI-kompatible Client kann `http://127.0.0.1:8765/v1` als Base-URL verwenden

Unterstützt: GPT-4o, GPT-4o-mini, GPT-4.1, o3-mini und alle Modelle Ihres Abos.

## Schnellstart

```bash
git clone https://github.com/youruser/coproxy-ai.git
cd coproxy-ai
./setup.sh
```

Das Setup-Skript:
1. Erstellt eine Python-venv und installiert Abhängigkeiten
2. Startet die Device-Code-Anmeldung (Sie geben einen Code auf openai.com ein)
3. Generiert ein Proxy-Secret (Ihr „API-Key" für Clients)
4. Installiert optional einen systemd-Dienst

**Fertig.** Der Proxy läuft auf `http://127.0.0.1:8765/v1`.

## Nutzung

Richten Sie einen beliebigen OpenAI-kompatiblen Client auf den Proxy:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8765/v1
export OPENAI_API_KEY=<Ihr COPROXY_SECRET aus .env>
```

### curl-Beispiel

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Hallo!"}]}'
```

### Python (openai SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    api_key="<Ihr COPROXY_SECRET>",
)

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hallo!"}],
)
print(resp.choices[0].message.content)
```

## Konfiguration

Alle Einstellungen erfolgen über Umgebungsvariablen (Datei `.env`):

| Variable | Standard | Beschreibung |
|---|---|---|
| `COPROXY_SECRET` | *erforderlich* | Bearer-Token zur Client-Authentifizierung |
| `COPROXY_PORT` | `8765` | Port |
| `COPROXY_HOST` | `127.0.0.1` | Adresse (localhost beibehalten!) |
| `COPROXY_AUTH_FILE` | `~/.codex/auth.json` | OAuth-Token-Speicher |
| `COPROXY_RATE_LIMIT` | `30` | Max. Anfragen/Min. (0 = unbegrenzt) |
| `COPROXY_LOG_LEVEL` | `info` | Log-Level |
| `COPROXY_LOG_REQUESTS` | `false` | Jede Proxy-Anfrage protokollieren |

## Endpunkte

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/health` | Gesundheitscheck + Token-TTL |
| `GET` | `/v1/models` | Liste verfügbarer Modelle |
| `POST` | `/v1/chat/completions` | Chat-Completions (Streaming unterstützt) |

## Token-Lebenszyklus

- OAuth-Token werden in `~/.codex/auth.json` gespeichert **(im Ruhezustand verschlüsselt)**
- Verschlüsselung: Fernet (AES-128-CBC + HMAC-SHA256), Schlüssel aus OS keyring oder maschinengebundener Ableitung
- Token sind ~8 Tage gültig
- coproxy erneuert sie automatisch 5 Minuten vor Ablauf
- Bei abgelaufenen Token: `.venv/bin/coproxy-login`

## Erneute Anmeldung

Bei abgelaufener Sitzung oder Kontowechsel:

```bash
.venv/bin/coproxy-login
sudo systemctl restart coproxy  # bei systemd
```

## Anforderungen

- Python 3.12+
- ChatGPT Plus-, Pro- oder Team-Abo
- Linux/macOS (systemd optional)

## Sicherheit

coproxy ist für den Betrieb auf Ihrem eigenen Server konzipiert — Geheimnisse verlassen localhost nicht.

**Netzwerk**
- Bindet nur an `127.0.0.1` — nicht aus dem Netzwerk erreichbar
- Warnung bei Überschreibung von `COPROXY_HOST` auf eine Nicht-Loopback-Adresse
- Swagger / OpenAPI / ReDoc deaktiviert (`docs_url=None`)

**Authentifizierung**
- Alle Anfragen erfordern einen Bearer-Token (`COPROXY_SECRET`)
- Token-Vergleich mit `secrets.compare_digest` (Constant-Time, kein Timing-Angriff)
- Rate-Limiting: Sliding Window im Speicher (Standard 30 Anfragen/Min.)

**Token-Speicherung (verschlüsselt)**
- OAuth-Token werden **im Ruhezustand verschlüsselt** mit Fernet (AES-128-CBC + HMAC-SHA256)
- Desktop: Verschlüsselungsschlüssel im OS-Keyring gespeichert (GNOME Keyring, macOS Keychain usw.)
- Headless-Server: Verschlüsselungsschlüssel abgeleitet aus `/etc/machine-id` + UID + zufälliges Salt (PBKDF2, 480K Iterationen) — **gestohlene Datei ist auf anderem Rechner/Benutzer nutzlos**
- Ältere Klartext-`auth.json` wird beim ersten Laden automatisch ins verschlüsselte Format migriert
- Dateiberechtigungen: `chmod 600`, beim Start überprüft (Warnung bei group/other-Zugriff)
- Atomares Schreiben: `tmp-Datei → os.replace()` — kein teilweises Lesen bei Absturz
- `asyncio.Lock()` bei Erneuerung — keine Race Conditions
- Optional: `pip install 'coproxy-ai[keyring]'` zur Aktivierung des OS-Keyring-Backends

**Protokollierungsrichtlinie**
- Protokolliert: HTTP-Methode, Pfad, Modell, Statuscode, Latenz
- Nie protokolliert: Prompts, Antworten, Access-/Refresh-/API-Token (auch nicht teilweise), Proxy-Secret, Konto-IDs
- Fehlerantworten bereinigt — keine Token-Fragmente in Ausnahmen

**Sonstiges**
- `--dry-run` zur Konfigurationsprüfung ohne Serverstart
- Keine Geheimnisse in Konfigurationsdateien — alles über Umgebungsvariablen oder `.env`
- Ordnungsgemäßes Herunterfahren bei SIGTERM (uvicorn)
- systemd-Dienstvorlage mit `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`

## Lizenz

MIT
