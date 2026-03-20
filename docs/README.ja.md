# coproxy-ai

[English](../README.md) | [Русский](README.ru.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Français](README.fr.md) | [Українська](README.uk.md) | [Ελληνικά](README.el.md) | [中文](README.zh.md) | [한국어](README.ko.md)

**ChatGPT Plus/Pro/Team サブスクリプション**を OpenAI API として使えるローカルプロキシ — APIクレジット不要。

```
あなたのアプリ → coproxy (localhost:8765) → api.openai.com
                   ↑ ChatGPT の OAuth トークンを使用
```

## 仕組み

1. ChatGPT アカウントでログイン（一回限りの device code フロー）
2. coproxy が OAuth セッションから API キーを取得し、自動更新
3. OpenAI 互換クライアントで `http://127.0.0.1:8765/v1` を base URL として使用可能

対応モデル：GPT-4o、GPT-4o-mini、GPT-4.1、o3-mini、サブスクリプションの全モデル。

## クイックスタート

```bash
git clone https://github.com/youruser/coproxy-ai.git
cd coproxy-ai
./setup.sh
```

セットアップスクリプトの処理：
1. Python venv を作成し依存関係をインストール
2. device code 認証を開始（openai.com でコードを入力）
3. プロキシシークレット（クライアント用「APIキー」）を生成
4. オプションで systemd サービスをインストール

**完了。** プロキシは `http://127.0.0.1:8765/v1` で稼働中。

## 使い方

OpenAI 互換クライアントをプロキシに向ける：

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8765/v1
export OPENAI_API_KEY=<.env の COPROXY_SECRET>
```

### curl の例

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"こんにちは！"}]}'
```

### Python (openai SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    api_key="<COPROXY_SECRET>",
)

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "こんにちは！"}],
)
print(resp.choices[0].message.content)
```

## 設定

すべての設定は環境変数（`.env` ファイル）で行います：

| 変数 | デフォルト | 説明 |
|---|---|---|
| `COPROXY_SECRET` | *必須* | クライアント認証用 Bearer トークン |
| `COPROXY_PORT` | `8765` | ポート |
| `COPROXY_HOST` | `127.0.0.1` | アドレス（localhost のまま！） |
| `COPROXY_AUTH_FILE` | `~/.codex/auth.json` | OAuth トークンストレージ |
| `COPROXY_RATE_LIMIT` | `30` | 最大リクエスト数/分（0 = 無制限） |
| `COPROXY_LOG_LEVEL` | `info` | ログレベル |
| `COPROXY_LOG_REQUESTS` | `false` | 各プロキシリクエストをログ |

## エンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| `GET` | `/health` | ヘルスチェック + トークン TTL |
| `GET` | `/v1/models` | 利用可能なモデル一覧 |
| `POST` | `/v1/chat/completions` | チャット補完（ストリーミング対応） |

## トークンのライフサイクル

- OAuth トークンは `~/.codex/auth.json` に保存 **（保存時に暗号化）**
- 暗号化：Fernet (AES-128-CBC + HMAC-SHA256)、OS keyring またはマシン固有の鍵導出からキーを取得
- トークンの有効期間は約 8 日間
- coproxy が有効期限の 5 分前に自動更新
- 期限切れの場合：`.venv/bin/coproxy-login`

## 再ログイン

セッション期限切れやアカウント切り替え時：

```bash
.venv/bin/coproxy-login
sudo systemctl restart coproxy  # systemd 使用時
```

## 要件

- Python 3.12+
- ChatGPT Plus、Pro、または Team サブスクリプション
- Linux/macOS（systemd はオプション）

## セキュリティ

coproxy は自分のサーバーで動作するよう設計 — シークレットは localhost から出ません。

**ネットワーク**
- `127.0.0.1` のみにバインド — ネットワークからアクセス不可
- `COPROXY_HOST` を非ループバックアドレスに変更すると警告
- Swagger / OpenAPI / ReDoc 無効（`docs_url=None`）

**認証**
- 全リクエストに Bearer トークン（`COPROXY_SECRET`）が必要
- トークン比較に `secrets.compare_digest` 使用（定時間、タイミング攻撃防止）
- レート制限：メモリ内スライディングウィンドウ（デフォルト 30 リクエスト/分）

**トークンストレージ（暗号化）**
- OAuth トークンは Fernet (AES-128-CBC + HMAC-SHA256) で**保存時に暗号化**
- デスクトップ：暗号化キーは OS キーリングに保存（GNOME Keyring、macOS Keychain など）
- ヘッドレスサーバー：暗号化キーは `/etc/machine-id` + UID + ランダムソルトから導出（PBKDF2、480K 反復）— **盗まれたファイルは別のマシン/ユーザーでは無効**
- レガシーの平文 `auth.json` は初回読み込み時に暗号化形式へ自動移行
- ファイル権限：`chmod 600`、起動時に検証（group/other アクセス可能な場合は警告）
- アトミック書き込み：`tmp ファイル → os.replace()` — クラッシュ時の部分読み取りなし
- 更新時に `asyncio.Lock()` — 競合状態なし
- オプション：`pip install 'coproxy-ai[keyring]'` で OS キーリングバックエンドを有効化

**ログポリシー**
- 記録：HTTP メソッド、パス、モデル、ステータスコード、レイテンシ
- 記録しない：プロンプト、レスポンス、access/refresh/API トークン（部分的にも）、プロキシシークレット、アカウント ID
- エラーレスポンスはサニタイズ済み — 例外にトークン断片なし

**その他**
- `--dry-run` フラグでサーバー起動なしに設定検証
- 設定ファイルにシークレットなし — すべて環境変数または `.env` 経由
- SIGTERM でグレースフルシャットダウン（uvicorn）
- systemd サービステンプレート：`NoNewPrivileges`、`PrivateTmp`、`ProtectSystem=strict`

## ライセンス

MIT
