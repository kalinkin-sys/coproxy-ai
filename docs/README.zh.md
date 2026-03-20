# coproxy-ai

[English](../README.md) | [Русский](README.ru.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Français](README.fr.md) | [Українська](README.uk.md) | [Ελληνικά](README.el.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

本地代理，让您使用 **ChatGPT Plus/Pro/Team 订阅** 作为 OpenAI API — 无需 API 额度。

```
您的应用 → coproxy (localhost:8765) → api.openai.com
              ↑ 使用您的 ChatGPT OAuth 令牌
```

## 工作原理

1. 使用 ChatGPT 账户登录（一次性 device code 流程）
2. coproxy 从 OAuth 会话中获取并自动刷新 API 密钥
3. 任何 OpenAI 兼容客户端都可以使用 `http://127.0.0.1:8765/v1` 作为 base URL

支持：GPT-4o、GPT-4o-mini、GPT-4.1、o3-mini 及订阅中的所有模型。

## 快速开始

```bash
git clone https://github.com/youruser/coproxy-ai.git
cd coproxy-ai
./setup.sh
```

安装脚本将：
1. 创建 Python venv 并安装依赖
2. 启动 device code 认证（在 openai.com 输入代码）
3. 生成代理密钥（客户端的"API 密钥"）
4. 可选安装 systemd 服务

**完成。** 代理运行在 `http://127.0.0.1:8765/v1`。

## 使用方法

将任何 OpenAI 兼容客户端指向代理：

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8765/v1
export OPENAI_API_KEY=<.env 中的 COPROXY_SECRET>
```

### curl 示例

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"你好！"}]}'
```

### Python (openai SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    api_key="<您的 COPROXY_SECRET>",
)

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "你好！"}],
)
print(resp.choices[0].message.content)
```

## 配置

所有配置通过环境变量（`.env` 文件）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `COPROXY_SECRET` | *必需* | 客户端认证用的 Bearer 令牌 |
| `COPROXY_PORT` | `8765` | 端口 |
| `COPROXY_HOST` | `127.0.0.1` | 地址（保持 localhost！） |
| `COPROXY_AUTH_FILE` | `~/.codex/auth.json` | OAuth 令牌存储 |
| `COPROXY_RATE_LIMIT` | `30` | 最大请求数/分钟（0 = 无限制） |
| `COPROXY_LOG_LEVEL` | `info` | 日志级别 |
| `COPROXY_LOG_REQUESTS` | `false` | 记录每个代理请求 |

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 健康检查 + 令牌 TTL |
| `GET` | `/v1/models` | 可用模型列表 |
| `POST` | `/v1/chat/completions` | 聊天补全（支持流式传输） |

## 令牌生命周期

- OAuth 令牌存储在 `~/.codex/auth.json` **（静态加密）**
- 加密方式：Fernet (AES-128-CBC + HMAC-SHA256)，密钥来自 OS keyring 或机器绑定派生
- 令牌有效期约 8 天
- coproxy 在到期前 5 分钟自动刷新
- 令牌过期时：`.venv/bin/coproxy-login`

## 重新登录

会话过期或需要切换账户时：

```bash
.venv/bin/coproxy-login
sudo systemctl restart coproxy  # 如果使用 systemd
```

## 要求

- Python 3.12+
- ChatGPT Plus、Pro 或 Team 订阅
- Linux/macOS（systemd 可选）

## 安全性

coproxy 设计为在您自己的服务器上运行 — 密钥不会离开 localhost。

**网络**
- 仅绑定 `127.0.0.1` — 无法从网络访问
- 如果 `COPROXY_HOST` 被覆盖为非回环地址，会记录警告
- Swagger / OpenAPI / ReDoc 已禁用（`docs_url=None`）

**认证**
- 所有请求需要 Bearer 令牌（`COPROXY_SECRET`）
- 令牌比较使用 `secrets.compare_digest`（常量时间，防止计时攻击）
- 速率限制：内存滑动窗口（默认 30 请求/分钟）

**令牌存储（加密）**
- OAuth 令牌**静态加密**，使用 Fernet (AES-128-CBC + HMAC-SHA256)
- 桌面环境：加密密钥存储在操作系统密钥环中（GNOME Keyring、macOS Keychain 等）
- Headless 服务器：加密密钥从 `/etc/machine-id` + UID + 随机盐派生（PBKDF2，480K 次迭代）— **被盗文件在其他机器/用户上无法使用**
- 旧版明文 `auth.json` 在首次加载时自动迁移为加密格式
- 文件权限：`chmod 600`，启动时验证（group/other 可访问时警告）
- 原子写入：`tmp 文件 → os.replace()` — 崩溃时无部分读取
- 刷新时 `asyncio.Lock()` — 无并发竞争条件
- 可选：`pip install 'coproxy-ai[keyring]'` 启用操作系统密钥环后端

**日志策略**
- 记录：HTTP 方法、路径、模型、状态码、延迟
- 从不记录：提示词、回复、access/refresh/API 令牌（即使部分）、代理密钥、账户 ID
- 错误响应已清理 — 异常中无令牌片段

**其他**
- `--dry-run` 标志验证配置而不启动服务器
- 配置文件中无密钥 — 全部通过环境变量或 `.env`
- SIGTERM 优雅关闭（uvicorn）
- systemd 服务模板含 `NoNewPrivileges`、`PrivateTmp`、`ProtectSystem=strict`

## 许可证

MIT
