# coproxy-ai

[English](../README.md) | [Русский](README.ru.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Français](README.fr.md) | [Ελληνικά](README.el.md) | [中文](README.zh.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

Локальний проксі, що дозволяє використовувати підписку **ChatGPT Plus/Pro/Team** як OpenAI API — без оплати за токени.

```
Ваш додаток → coproxy (localhost:8765) → api.openai.com
                 ↑ використовує ваші OAuth-токени ChatGPT
```

## Як це працює

1. Ви входите в акаунт ChatGPT (одноразовий device code flow)
2. coproxy отримує та автоматично оновлює API-ключ з вашої OAuth-сесії
3. Будь-який OpenAI-сумісний клієнт може використовувати `http://127.0.0.1:8765/v1` як base URL

Підтримуються: GPT-4o, GPT-4o-mini, GPT-4.1, o3-mini та будь-які моделі вашої підписки.

## Швидкий старт

```bash
git clone https://github.com/youruser/coproxy-ai.git
cd coproxy-ai
./setup.sh
```

Скрипт встановлення:
1. Створить Python venv та встановить залежності
2. Запустить авторизацію через device code (ви вводите код на openai.com)
3. Згенерує секрет проксі (ваш «API-ключ» для клієнтів)
4. Опціонально встановить systemd-сервіс

**Готово.** Проксі працює на `http://127.0.0.1:8765/v1`.

## Використання

Направте будь-який OpenAI-сумісний клієнт на проксі:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8765/v1
export OPENAI_API_KEY=<ваш COPROXY_SECRET з .env>
```

### Приклад з curl

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Привіт!"}]}'
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
    messages=[{"role": "user", "content": "Привіт!"}],
)
print(resp.choices[0].message.content)
```

## Конфігурація

Все налаштування через змінні оточення (файл `.env`):

| Змінна | За замовчуванням | Опис |
|---|---|---|
| `COPROXY_SECRET` | *обов'язково* | Bearer-токен для автентифікації клієнтів |
| `COPROXY_PORT` | `8765` | Порт |
| `COPROXY_HOST` | `127.0.0.1` | Адреса (залиште localhost!) |
| `COPROXY_AUTH_FILE` | `~/.codex/auth.json` | Сховище OAuth-токенів |
| `COPROXY_RATE_LIMIT` | `30` | Макс. запитів/хв (0 = без обмежень) |
| `COPROXY_LOG_LEVEL` | `info` | Рівень логування |
| `COPROXY_LOG_REQUESTS` | `false` | Логувати кожен проксований запит |

## Ендпоінти

| Метод | Шлях | Опис |
|---|---|---|
| `GET` | `/health` | Перевірка здоров'я + TTL токена |
| `GET` | `/v1/models` | Список доступних моделей |
| `POST` | `/v1/chat/completions` | Чат-завершення (підтримка streaming) |

## Життєвий цикл токенів

- OAuth-токени зберігаються в `~/.codex/auth.json` **(зашифровані при зберіганні)**
- Шифрування: Fernet (AES-128-CBC + HMAC-SHA256), ключ з OS keyring або машинно-прив'язаного виведення
- Токени дійсні ~8 днів
- coproxy автоматично оновлює їх за 5 хвилин до закінчення
- Якщо токени закінчились: `.venv/bin/coproxy-login`

## Повторний вхід

Якщо сесія закінчилась або потрібно змінити акаунт:

```bash
.venv/bin/coproxy-login
sudo systemctl restart coproxy  # якщо використовуєте systemd
```

## Вимоги

- Python 3.12+
- Підписка ChatGPT Plus, Pro або Team
- Linux/macOS (systemd опціонально)

## Безпека

coproxy спроектований для роботи на вашому сервері — секрети не залишають localhost.

**Мережа**
- Прив'язка тільки до `127.0.0.1` — недоступний з мережі
- Попередження в логах при перевизначенні `COPROXY_HOST` на не-loopback адресу
- Swagger / OpenAPI / ReDoc вимкнені (`docs_url=None`)

**Автентифікація**
- Всі запити вимагають Bearer-токен (`COPROXY_SECRET`)
- Порівняння токенів через `secrets.compare_digest` (constant-time, захист від timing-атак)
- Rate limiting: ковзне вікно в пам'яті (за замовчуванням 30 запитів/хв)

**Зберігання токенів (шифрування)**
- OAuth-токени **шифруються при зберіганні** за допомогою Fernet (AES-128-CBC + HMAC-SHA256)
- Десктоп: ключ шифрування зберігається у системному сховищі ключів ОС (GNOME Keyring, macOS Keychain тощо)
- Headless-сервер: ключ шифрування виводиться з `/etc/machine-id` + UID + випадкова сіль (PBKDF2, 480K ітерацій) — **вкрадений файл марний на іншій машині/користувачі**
- Застарілий текстовий `auth.json` автоматично мігрується у зашифрований формат при першому завантаженні
- Права файлу: `chmod 600`, перевіряються при запуску (попередження при доступі group/other)
- Атомарний запис: `tmp файл → os.replace()` — немає часткового читання при збої
- `asyncio.Lock()` при оновленні — немає гонок між конкурентними запитами
- Опціонально: `pip install 'coproxy-ai[keyring]'` для увімкнення бекенду системного сховища ключів ОС

**Політика логування**
- Логується: HTTP-метод, шлях, модель, код статусу, затримка
- Ніколи не логується: промпти, відповіді, access/refresh/API-токени (навіть частково), proxy secret, ID акаунтів
- Відповіді про помилки санітизовані — без фрагментів токенів у винятках

**Інше**
- Прапорець `--dry-run` для перевірки конфігурації без запуску сервера
- Жодних секретів у конфіг-файлах — все через змінні оточення або `.env`
- Коректне завершення по SIGTERM (uvicorn)
- Шаблон systemd-сервісу з `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`

## Ліцензія

MIT
