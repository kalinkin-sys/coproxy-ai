# coproxy-ai

[English](../README.md) | [Русский](README.ru.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Français](README.fr.md) | [Українська](README.uk.md) | [Ελληνικά](README.el.md) | [中文](README.zh.md) | [日本語](README.ja.md)

**ChatGPT Plus/Pro/Team 구독**을 OpenAI API로 사용할 수 있는 로컬 프록시 — API 크레딧 불필요.

```
내 앱 → coproxy (localhost:8765) → api.openai.com
           ↑ ChatGPT OAuth 토큰 사용
```

## 작동 방식

1. ChatGPT 계정으로 로그인 (일회성 device code 플로우)
2. coproxy가 OAuth 세션에서 API 키를 획득하고 자동 갱신
3. OpenAI 호환 클라이언트에서 `http://127.0.0.1:8765/v1`을 base URL로 사용 가능

지원 모델: GPT-4o, GPT-4o-mini, GPT-4.1, o3-mini 및 구독의 모든 모델.

## 빠른 시작

```bash
git clone https://github.com/youruser/coproxy-ai.git
cd coproxy-ai
./setup.sh
```

설치 스크립트 동작:
1. Python venv 생성 및 의존성 설치
2. device code 인증 시작 (openai.com에서 코드 입력)
3. 프록시 시크릿 생성 (클라이언트용 "API 키")
4. 선택적으로 systemd 서비스 설치

**완료.** 프록시가 `http://127.0.0.1:8765/v1`에서 실행 중.

## 사용법

OpenAI 호환 클라이언트를 프록시로 연결:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8765/v1
export OPENAI_API_KEY=<.env의 COPROXY_SECRET>
```

### curl 예제

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"안녕하세요!"}]}'
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
    messages=[{"role": "user", "content": "안녕하세요!"}],
)
print(resp.choices[0].message.content)
```

## 설정

모든 설정은 환경 변수(`.env` 파일)로:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `COPROXY_SECRET` | *필수* | 클라이언트 인증용 Bearer 토큰 |
| `COPROXY_PORT` | `8765` | 포트 |
| `COPROXY_HOST` | `127.0.0.1` | 주소 (localhost 유지!) |
| `COPROXY_AUTH_FILE` | `~/.codex/auth.json` | OAuth 토큰 저장소 |
| `COPROXY_RATE_LIMIT` | `30` | 최대 요청/분 (0 = 무제한) |
| `COPROXY_LOG_LEVEL` | `info` | 로그 레벨 |
| `COPROXY_LOG_REQUESTS` | `false` | 각 프록시 요청 로깅 |

## 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/health` | 헬스 체크 + 토큰 TTL |
| `GET` | `/v1/models` | 사용 가능한 모델 목록 |
| `POST` | `/v1/chat/completions` | 채팅 완성 (스트리밍 지원) |

## 토큰 수명 주기

- OAuth 토큰은 `~/.codex/auth.json`에 저장 **(저장 시 암호화)**
- 암호화: Fernet (AES-128-CBC + HMAC-SHA256), OS keyring 또는 머신 바인딩 파생에서 키 획득
- 토큰 유효 기간 약 8일
- coproxy가 만료 5분 전에 자동 갱신
- 만료 시: `.venv/bin/coproxy-login`

## 재로그인

세션 만료 또는 계정 전환 시:

```bash
.venv/bin/coproxy-login
sudo systemctl restart coproxy  # systemd 사용 시
```

## 요구사항

- Python 3.12+
- ChatGPT Plus, Pro 또는 Team 구독
- Linux/macOS (systemd 선택)

## 보안

coproxy는 자체 서버에서 실행되도록 설계 — 시크릿이 localhost를 벗어나지 않음.

**네트워크**
- `127.0.0.1`에만 바인딩 — 네트워크에서 접근 불가
- `COPROXY_HOST`를 비루프백 주소로 변경 시 경고
- Swagger / OpenAPI / ReDoc 비활성화 (`docs_url=None`)

**인증**
- 모든 요청에 Bearer 토큰 (`COPROXY_SECRET`) 필요
- `secrets.compare_digest`로 토큰 비교 (상수 시간, 타이밍 공격 방지)
- 속도 제한: 메모리 슬라이딩 윈도우 (기본 30 요청/분)

**토큰 저장 (암호화)**
- OAuth 토큰은 Fernet (AES-128-CBC + HMAC-SHA256)으로 **저장 시 암호화**
- 데스크톱: 암호화 키가 OS 키링에 저장 (GNOME Keyring, macOS Keychain 등)
- 헤드리스 서버: 암호화 키가 `/etc/machine-id` + UID + 랜덤 솔트에서 파생 (PBKDF2, 480K 반복) — **도난된 파일은 다른 머신/사용자에서 무용지물**
- 레거시 평문 `auth.json`은 첫 로드 시 암호화 형식으로 자동 마이그레이션
- 파일 권한: `chmod 600`, 시작 시 검증 (group/other 접근 가능 시 경고)
- 원자적 쓰기: `tmp 파일 → os.replace()` — 크래시 시 부분 읽기 없음
- 갱신 시 `asyncio.Lock()` — 경쟁 조건 없음
- 선택 사항: `pip install 'coproxy-ai[keyring]'`로 OS 키링 백엔드 활성화

**로깅 정책**
- 기록: HTTP 메서드, 경로, 모델, 상태 코드, 지연 시간
- 기록 안 함: 프롬프트, 응답, access/refresh/API 토큰 (부분적으로도), 프록시 시크릿, 계정 ID
- 오류 응답 정제 — 예외에 토큰 조각 없음

**기타**
- `--dry-run` 플래그로 서버 시작 없이 설정 검증
- 설정 파일에 시크릿 없음 — 모두 환경 변수 또는 `.env`로
- SIGTERM 그레이스풀 셧다운 (uvicorn)
- systemd 서비스 템플릿: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`

## 라이선스

MIT
