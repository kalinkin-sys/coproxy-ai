# coproxy-ai

[English](../README.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Français](README.fr.md) | [Українська](README.uk.md) | [Ελληνικά](README.el.md) | [中文](README.zh.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

Локальный прокси, позволяющий использовать подписку **ChatGPT Plus/Pro/Team** как OpenAI API — без оплаты за токены.

```
Ваше приложение → coproxy (localhost:8765) → api.openai.com
                     ↑ использует ваши OAuth-токены ChatGPT
```

## Как это работает

1. Вы входите в аккаунт ChatGPT (одноразовый device code flow)
2. coproxy получает и автоматически обновляет API-ключ из вашей OAuth-сессии
3. Любой OpenAI-совместимый клиент может использовать `http://127.0.0.1:8765/v1` как base URL

Поддерживаются: GPT-4o, GPT-4o-mini, GPT-4.1, o3-mini и любые модели вашей подписки.

## Быстрый старт

```bash
git clone https://github.com/youruser/coproxy-ai.git
cd coproxy-ai
./setup.sh
```

Скрипт установки:
1. Создаст Python venv и установит зависимости
2. Запустит авторизацию через device code (вы вводите код на openai.com)
3. Сгенерирует секрет прокси (ваш «API-ключ» для клиентов)
4. Опционально установит systemd-сервис

**Готово.** Прокси работает на `http://127.0.0.1:8765/v1`.

## Использование

Направьте любой OpenAI-совместимый клиент на прокси:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8765/v1
export OPENAI_API_KEY=<ваш COPROXY_SECRET из .env>
```

### Пример с curl

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Привет!"}]}'
```

### Python (openai SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    api_key="<ваш COPROXY_SECRET>",
)

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Привет!"}],
)
print(resp.choices[0].message.content)
```

## Конфигурация

Вся настройка через переменные окружения (файл `.env`):

| Переменная | По умолчанию | Описание |
|---|---|---|
| `COPROXY_SECRET` | *обязательно* | Bearer-токен для аутентификации клиентов |
| `COPROXY_PORT` | `8765` | Порт |
| `COPROXY_HOST` | `127.0.0.1` | Адрес (оставьте localhost!) |
| `COPROXY_AUTH_FILE` | `~/.codex/auth.json` | Хранилище OAuth-токенов |
| `COPROXY_RATE_LIMIT` | `30` | Максимум запросов/мин (0 = без лимита) |
| `COPROXY_LOG_LEVEL` | `info` | Уровень логирования |
| `COPROXY_LOG_REQUESTS` | `false` | Логировать каждый проксированный запрос |

## Эндпоинты

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/health` | Проверка здоровья + TTL токена |
| `GET` | `/v1/models` | Список доступных моделей |
| `POST` | `/v1/chat/completions` | Чат-завершения (поддержка streaming) |

## Жизненный цикл токенов

- OAuth-токены хранятся в `~/.codex/auth.json` **(зашифрованы при хранении)**
- Шифрование: Fernet (AES-128-CBC + HMAC-SHA256), ключ из OS keyring или машинно-привязанного вывода
- Токены действительны ~8 дней
- coproxy автоматически обновляет их за 5 минут до истечения
- Если токены истекли: `.venv/bin/coproxy-login`

## Повторный вход

Если сессия истекла или нужно сменить аккаунт:

```bash
.venv/bin/coproxy-login
sudo systemctl restart coproxy  # если используете systemd
```

## Требования

- Python 3.12+
- Подписка ChatGPT Plus, Pro или Team
- Linux/macOS (systemd опционально)

## Безопасность

coproxy спроектирован для работы на вашем сервере — секреты не покидают localhost.

**Сеть**
- Привязка только к `127.0.0.1` — недоступен из сети
- Предупреждение в логах при переопределении `COPROXY_HOST` на не-loopback адрес
- Swagger / OpenAPI / ReDoc отключены (`docs_url=None`)

**Аутентификация**
- Все запросы требуют Bearer-токен (`COPROXY_SECRET`)
- Сравнение токенов через `secrets.compare_digest` (constant-time, защита от timing-атак)
- Rate limiting: скользящее окно в памяти (по умолчанию 30 запросов/мин)

**Хранение токенов (шифрование)**
- OAuth-токены **шифруются при хранении** с помощью Fernet (AES-128-CBC + HMAC-SHA256)
- Десктоп: ключ шифрования хранится в системном хранилище ключей ОС (GNOME Keyring, macOS Keychain и т. д.)
- Headless-сервер: ключ шифрования выводится из `/etc/machine-id` + UID + случайная соль (PBKDF2, 480K итераций) — **украденный файл бесполезен на другой машине/пользователе**
- Устаревший текстовый `auth.json` автоматически мигрируется в зашифрованный формат при первой загрузке
- Права файла: `chmod 600`, проверяются при запуске (предупреждение при доступе group/other)
- Атомарная запись: `tmp файл → os.replace()` — нет частичного чтения при сбое
- `asyncio.Lock()` при обновлении — нет гонок между конкурентными запросами
- Опционально: `pip install 'coproxy-ai[keyring]'` для включения бэкенда системного хранилища ключей ОС

**Политика логирования**
- Логируется: HTTP-метод, путь, модель, код статуса, задержка
- Никогда не логируется: промпты, ответы, access/refresh/API-токены (даже частично), proxy secret, ID аккаунтов
- Ответы об ошибках санитизированы — без фрагментов токенов в исключениях

**Прочее**
- Флаг `--dry-run` для проверки конфигурации без запуска сервера
- Никаких секретов в конфиг-файлах — всё через переменные окружения или `.env`
- Корректное завершение по SIGTERM (uvicorn)
- Шаблон systemd-сервиса с `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`

## Лицензия

MIT
