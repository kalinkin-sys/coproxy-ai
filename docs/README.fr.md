# coproxy-ai

[English](../README.md) | [Русский](README.ru.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Українська](README.uk.md) | [Ελληνικά](README.el.md) | [中文](README.zh.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

Proxy local qui permet d'utiliser votre abonnement **ChatGPT Plus/Pro/Team** comme API OpenAI — sans frais de tokens.

```
Votre app → coproxy (localhost:8765) → api.openai.com
               ↑ utilise vos tokens OAuth ChatGPT
```

## Comment ça marche

1. Vous vous connectez avec votre compte ChatGPT (flux device code, une seule fois)
2. coproxy obtient et renouvelle automatiquement une clé API depuis votre session OAuth
3. Tout client compatible OpenAI peut utiliser `http://127.0.0.1:8765/v1` comme base URL

Compatible avec : GPT-4o, GPT-4o-mini, GPT-4.1, o3-mini et tout modèle de votre abonnement.

## Démarrage rapide

```bash
git clone https://github.com/youruser/coproxy-ai.git
cd coproxy-ai
./setup.sh
```

Le script d'installation :
1. Crée un venv Python et installe les dépendances
2. Lance l'authentification device code (vous entrez un code sur openai.com)
3. Génère un secret proxy (votre « clé API » pour les clients)
4. Installe optionnellement un service systemd

**C'est tout.** Votre proxy tourne sur `http://127.0.0.1:8765/v1`.

## Utilisation

Dirigez n'importe quel client compatible OpenAI vers le proxy :

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8765/v1
export OPENAI_API_KEY=<votre COPROXY_SECRET depuis .env>
```

### Exemple curl

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Bonjour !"}]}'
```

### Python (openai SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    api_key="<votre COPROXY_SECRET>",
)

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Bonjour !"}],
)
print(resp.choices[0].message.content)
```

## Configuration

Toute la configuration se fait via variables d'environnement (fichier `.env`) :

| Variable | Défaut | Description |
|---|---|---|
| `COPROXY_SECRET` | *obligatoire* | Token Bearer pour l'authentification des clients |
| `COPROXY_PORT` | `8765` | Port |
| `COPROXY_HOST` | `127.0.0.1` | Adresse (gardez localhost !) |
| `COPROXY_AUTH_FILE` | `~/.codex/auth.json` | Stockage des tokens OAuth |
| `COPROXY_RATE_LIMIT` | `30` | Max requêtes/min (0 = illimité) |
| `COPROXY_LOG_LEVEL` | `info` | Niveau de log |
| `COPROXY_LOG_REQUESTS` | `false` | Logger chaque requête proxifiée |

## Points de terminaison

| Méthode | Chemin | Description |
|---|---|---|
| `GET` | `/health` | Vérification de santé + TTL du token |
| `GET` | `/v1/models` | Liste des modèles disponibles |
| `POST` | `/v1/chat/completions` | Completions de chat (streaming supporté) |

## Cycle de vie des tokens

- Les tokens OAuth sont stockés dans `~/.codex/auth.json` **(chiffrés au repos)**
- Chiffrement : Fernet (AES-128-CBC + HMAC-SHA256), clé issue du OS keyring ou d'une dérivation liée à la machine
- Les tokens sont valides ~8 jours
- coproxy les renouvelle automatiquement 5 minutes avant expiration
- En cas d'expiration : `.venv/bin/coproxy-login`

## Reconnexion

Si la session expire ou si vous devez changer de compte :

```bash
.venv/bin/coproxy-login
sudo systemctl restart coproxy  # si vous utilisez systemd
```

## Prérequis

- Python 3.12+
- Abonnement ChatGPT Plus, Pro ou Team
- Linux/macOS (systemd optionnel)

## Sécurité

coproxy est conçu pour tourner sur votre propre serveur — aucun secret ne quitte localhost.

**Réseau**
- Écoute uniquement sur `127.0.0.1` — inaccessible depuis le réseau
- Avertissement si `COPROXY_HOST` est remplacé par une adresse non-loopback
- Swagger / OpenAPI / ReDoc désactivés (`docs_url=None`)

**Authentification**
- Toutes les requêtes nécessitent un token Bearer (`COPROXY_SECRET`)
- Comparaison de tokens via `secrets.compare_digest` (temps constant, pas d'attaque par timing)
- Rate limiting : fenêtre glissante en mémoire (30 requêtes/min par défaut)

**Stockage des tokens (chiffré)**
- Les tokens OAuth sont **chiffrés au repos** avec Fernet (AES-128-CBC + HMAC-SHA256)
- Bureau : clé de chiffrement stockée dans le trousseau du SE (GNOME Keyring, macOS Keychain, etc.)
- Serveur headless : clé de chiffrement dérivée de `/etc/machine-id` + UID + sel aléatoire (PBKDF2, 480K itérations) — **un fichier volé est inutile sur une autre machine/utilisateur**
- L'ancien `auth.json` en clair est automatiquement migré au format chiffré au premier chargement
- Permissions de fichier : `chmod 600`, vérifiées au démarrage (avertissement si accessible par group/other)
- Écriture atomique : `fichier tmp → os.replace()` — pas de lecture partielle en cas de crash
- `asyncio.Lock()` lors du renouvellement — pas de conditions de concurrence
- Optionnel : `pip install 'coproxy-ai[keyring]'` pour activer le backend du trousseau du SE

**Politique de journalisation**
- Journalisé : méthode HTTP, chemin, modèle, code de statut, latence
- Jamais journalisé : prompts, réponses, tokens access/refresh/API (même partiellement), proxy secret, IDs de compte
- Réponses d'erreur assainies — pas de fragments de tokens dans les exceptions

**Autres**
- Flag `--dry-run` pour valider la configuration sans démarrer le serveur
- Aucun secret dans les fichiers de configuration — tout via variables d'environnement ou `.env`
- Arrêt gracieux sur SIGTERM (uvicorn)
- Template de service systemd avec `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`

## Licence

MIT
