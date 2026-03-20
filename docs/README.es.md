# coproxy-ai

[English](../README.md) | [Русский](README.ru.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Українська](README.uk.md) | [Ελληνικά](README.el.md) | [中文](README.zh.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

Proxy local que permite usar tu suscripción de **ChatGPT Plus/Pro/Team** como API de OpenAI — sin costes por tokens.

```
Tu app → coproxy (localhost:8765) → api.openai.com
            ↑ usa tus tokens OAuth de ChatGPT
```

## Cómo funciona

1. Inicias sesión con tu cuenta de ChatGPT (flujo device code, una sola vez)
2. coproxy obtiene y renueva automáticamente una clave API de tu sesión OAuth
3. Cualquier cliente compatible con OpenAI puede usar `http://127.0.0.1:8765/v1` como base URL

Compatible con: GPT-4o, GPT-4o-mini, GPT-4.1, o3-mini y cualquier modelo de tu suscripción.

## Inicio rápido

```bash
git clone https://github.com/youruser/coproxy-ai.git
cd coproxy-ai
./setup.sh
```

El script de instalación:
1. Crea un venv de Python e instala dependencias
2. Inicia el flujo device code (introduces un código en openai.com)
3. Genera un secreto del proxy (tu «clave API» para clientes)
4. Opcionalmente instala un servicio systemd

**Listo.** Tu proxy está funcionando en `http://127.0.0.1:8765/v1`.

## Uso

Apunta cualquier cliente compatible con OpenAI al proxy:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8765/v1
export OPENAI_API_KEY=<tu COPROXY_SECRET de .env>
```

### Ejemplo con curl

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"¡Hola!"}]}'
```

### Python (openai SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    api_key="<tu COPROXY_SECRET>",
)

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "¡Hola!"}],
)
print(resp.choices[0].message.content)
```

## Configuración

Toda la configuración es mediante variables de entorno (archivo `.env`):

| Variable | Por defecto | Descripción |
|---|---|---|
| `COPROXY_SECRET` | *obligatorio* | Token Bearer para autenticación de clientes |
| `COPROXY_PORT` | `8765` | Puerto |
| `COPROXY_HOST` | `127.0.0.1` | Dirección (¡mantén localhost!) |
| `COPROXY_AUTH_FILE` | `~/.codex/auth.json` | Almacén de tokens OAuth |
| `COPROXY_RATE_LIMIT` | `30` | Máx. peticiones/min (0 = sin límite) |
| `COPROXY_LOG_LEVEL` | `info` | Nivel de log |
| `COPROXY_LOG_REQUESTS` | `false` | Registrar cada petición proxificada |

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Chequeo de salud + TTL del token |
| `GET` | `/v1/models` | Lista de modelos disponibles |
| `POST` | `/v1/chat/completions` | Completions de chat (streaming soportado) |

## Ciclo de vida de tokens

- Los tokens OAuth se almacenan en `~/.codex/auth.json` **(cifrados en reposo)**
- Cifrado: Fernet (AES-128-CBC + HMAC-SHA256), clave del OS keyring o derivación vinculada a la máquina
- Los tokens son válidos ~8 días
- coproxy los renueva automáticamente 5 minutos antes de expirar
- Si expiran: `.venv/bin/coproxy-login`

## Reinicio de sesión

Si la sesión expira o necesitas cambiar de cuenta:

```bash
.venv/bin/coproxy-login
sudo systemctl restart coproxy  # si usas systemd
```

## Requisitos

- Python 3.12+
- Suscripción ChatGPT Plus, Pro o Team
- Linux/macOS (systemd opcional)

## Seguridad

coproxy está diseñado para ejecutarse en tu propio servidor — los secretos no salen de localhost.

**Red**
- Solo escucha en `127.0.0.1` — no accesible desde la red
- Aviso si se sobreescribe `COPROXY_HOST` con una dirección no-loopback
- Swagger / OpenAPI / ReDoc desactivados (`docs_url=None`)

**Autenticación**
- Todas las peticiones requieren un token Bearer (`COPROXY_SECRET`)
- Comparación de tokens con `secrets.compare_digest` (tiempo constante, sin ataques de timing)
- Rate limiting: ventana deslizante en memoria (por defecto 30 peticiones/min)

**Almacenamiento de tokens (cifrado)**
- Los tokens OAuth se **cifran en reposo** usando Fernet (AES-128-CBC + HMAC-SHA256)
- Escritorio: clave de cifrado almacenada en el llavero del SO (GNOME Keyring, macOS Keychain, etc.)
- Servidor headless: clave de cifrado derivada de `/etc/machine-id` + UID + sal aleatoria (PBKDF2, 480K iteraciones) — **un archivo robado es inútil en otra máquina/usuario**
- El `auth.json` en texto plano heredado se migra automáticamente al formato cifrado en la primera carga
- Permisos de archivo: `chmod 600`, verificados al inicio (aviso si accesible por group/other)
- Escritura atómica: `archivo tmp → os.replace()` — sin lecturas parciales en caídas
- `asyncio.Lock()` en renovación — sin condiciones de carrera
- Opcional: `pip install 'coproxy-ai[keyring]'` para habilitar el backend del llavero del SO

**Política de logging**
- Se registra: método HTTP, ruta, modelo, código de estado, latencia
- Nunca se registra: prompts, respuestas, tokens access/refresh/API (ni parcialmente), proxy secret, IDs de cuenta
- Respuestas de error sanitizadas — sin fragmentos de tokens en excepciones

**Otros**
- Flag `--dry-run` para validar configuración sin iniciar el servidor
- Sin secretos en archivos de configuración — todo mediante variables de entorno o `.env`
- Apagado graceful con SIGTERM (uvicorn)
- Plantilla de servicio systemd con `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`

## Licencia

MIT
