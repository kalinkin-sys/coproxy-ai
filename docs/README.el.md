# coproxy-ai

[English](../README.md) | [Русский](README.ru.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Français](README.fr.md) | [Українська](README.uk.md) | [中文](README.zh.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

Τοπικό proxy που σας επιτρέπει να χρησιμοποιείτε τη συνδρομή σας **ChatGPT Plus/Pro/Team** ως OpenAI API — χωρίς κόστος tokens.

```
Η εφαρμογή σας → coproxy (localhost:8765) → api.openai.com
                    ↑ χρησιμοποιεί τα OAuth tokens του ChatGPT σας
```

## Πώς λειτουργεί

1. Συνδέεστε με τον λογαριασμό ChatGPT σας (μία φορά, device code flow)
2. Το coproxy λαμβάνει και ανανεώνει αυτόματα ένα API key από την OAuth session σας
3. Οποιοσδήποτε OpenAI-συμβατός client μπορεί να χρησιμοποιήσει το `http://127.0.0.1:8765/v1` ως base URL

Υποστηρίζει: GPT-4o, GPT-4o-mini, GPT-4.1, o3-mini και οποιοδήποτε μοντέλο της συνδρομής σας.

## Γρήγορη εκκίνηση

```bash
git clone https://github.com/youruser/coproxy-ai.git
cd coproxy-ai
./setup.sh
```

Το script εγκατάστασης:
1. Δημιουργεί Python venv και εγκαθιστά εξαρτήσεις
2. Ξεκινά την αυθεντικοποίηση device code (εισάγετε κωδικό στο openai.com)
3. Δημιουργεί ένα proxy secret (το «API key» σας για τους clients)
4. Προαιρετικά εγκαθιστά systemd service

**Αυτό ήταν.** Το proxy τρέχει στο `http://127.0.0.1:8765/v1`.

## Χρήση

Κατευθύνετε οποιονδήποτε OpenAI-συμβατό client στο proxy:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8765/v1
export OPENAI_API_KEY=<το COPROXY_SECRET σας από .env>
```

### Παράδειγμα curl

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Γεια σου!"}]}'
```

### Python (openai SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    api_key="<το COPROXY_SECRET σας>",
)

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Γεια σου!"}],
)
print(resp.choices[0].message.content)
```

## Ρύθμιση

Όλη η ρύθμιση γίνεται μέσω μεταβλητών περιβάλλοντος (αρχείο `.env`):

| Μεταβλητή | Προεπιλογή | Περιγραφή |
|---|---|---|
| `COPROXY_SECRET` | *απαιτείται* | Bearer token για αυθεντικοποίηση clients |
| `COPROXY_PORT` | `8765` | Θύρα |
| `COPROXY_HOST` | `127.0.0.1` | Διεύθυνση (κρατήστε localhost!) |
| `COPROXY_AUTH_FILE` | `~/.codex/auth.json` | Αποθήκευση OAuth tokens |
| `COPROXY_RATE_LIMIT` | `30` | Μέγ. αιτήματα/λεπτό (0 = χωρίς όριο) |
| `COPROXY_LOG_LEVEL` | `info` | Επίπεδο logging |
| `COPROXY_LOG_REQUESTS` | `false` | Καταγραφή κάθε proxied αιτήματος |

## Endpoints

| Μέθοδος | Διαδρομή | Περιγραφή |
|---|---|---|
| `GET` | `/health` | Έλεγχος υγείας + TTL token |
| `GET` | `/v1/models` | Λίστα διαθέσιμων μοντέλων |
| `POST` | `/v1/chat/completions` | Chat completions (υποστήριξη streaming) |

## Κύκλος ζωής tokens

- Τα OAuth tokens αποθηκεύονται στο `~/.codex/auth.json` **(κρυπτογραφημένα σε αδράνεια)**
- Κρυπτογράφηση: Fernet (AES-128-CBC + HMAC-SHA256), κλειδί από OS keyring ή παραγωγή δεσμευμένη στο μηχάνημα
- Τα tokens ισχύουν ~8 ημέρες
- Το coproxy τα ανανεώνει αυτόματα 5 λεπτά πριν τη λήξη
- Αν λήξουν: `.venv/bin/coproxy-login`

## Επανασύνδεση

Αν η session λήξει ή χρειάζεται αλλαγή λογαριασμού:

```bash
.venv/bin/coproxy-login
sudo systemctl restart coproxy  # αν χρησιμοποιείτε systemd
```

## Απαιτήσεις

- Python 3.12+
- Συνδρομή ChatGPT Plus, Pro ή Team
- Linux/macOS (systemd προαιρετικό)

## Ασφάλεια

Το coproxy σχεδιάστηκε για λειτουργία στον δικό σας server — κανένα secret δεν φεύγει από το localhost.

**Δίκτυο**
- Δέσμευση μόνο στο `127.0.0.1` — μη προσβάσιμο από το δίκτυο
- Προειδοποίηση αν αντικατασταθεί το `COPROXY_HOST` με μη-loopback διεύθυνση
- Swagger / OpenAPI / ReDoc απενεργοποιημένα (`docs_url=None`)

**Αυθεντικοποίηση**
- Όλα τα αιτήματα απαιτούν Bearer token (`COPROXY_SECRET`)
- Σύγκριση tokens με `secrets.compare_digest` (σταθερός χρόνος, χωρίς timing attacks)
- Rate limiting: sliding window στη μνήμη (προεπιλογή 30 αιτήματα/λεπτό)

**Αποθήκευση tokens (κρυπτογραφημένη)**
- Τα OAuth tokens **κρυπτογραφούνται σε αδράνεια** με Fernet (AES-128-CBC + HMAC-SHA256)
- Desktop: κλειδί κρυπτογράφησης αποθηκευμένο στο keyring του ΛΣ (GNOME Keyring, macOS Keychain κ.λπ.)
- Headless server: κλειδί κρυπτογράφησης παράγεται από `/etc/machine-id` + UID + τυχαίο salt (PBKDF2, 480K επαναλήψεις) — **κλεμμένο αρχείο είναι άχρηστο σε άλλο μηχάνημα/χρήστη**
- Το παλαιό `auth.json` σε απλό κείμενο μεταφέρεται αυτόματα σε κρυπτογραφημένη μορφή κατά την πρώτη φόρτωση
- Δικαιώματα αρχείου: `chmod 600`, ελέγχονται κατά την εκκίνηση (προειδοποίηση αν προσβάσιμο από group/other)
- Ατομική εγγραφή: `tmp αρχείο → os.replace()` — χωρίς μερική ανάγνωση σε crash
- `asyncio.Lock()` κατά την ανανέωση — χωρίς race conditions
- Προαιρετικά: `pip install 'coproxy-ai[keyring]'` για ενεργοποίηση του backend keyring του ΛΣ

**Πολιτική logging**
- Καταγράφεται: HTTP μέθοδος, διαδρομή, μοντέλο, κωδικός κατάστασης, καθυστέρηση
- Ποτέ δεν καταγράφεται: prompts, απαντήσεις, access/refresh/API tokens (ούτε μερικά), proxy secret, account IDs
- Sanitized απαντήσεις σφαλμάτων — χωρίς τμήματα tokens σε exceptions

**Άλλα**
- Flag `--dry-run` για επικύρωση ρύθμισης χωρίς εκκίνηση server
- Κανένα secret σε αρχεία ρύθμισης — όλα μέσω μεταβλητών περιβάλλοντος ή `.env`
- Graceful shutdown με SIGTERM (uvicorn)
- Template systemd service με `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`

## Άδεια

MIT
