# Plaything v2

**한국어 · [English](README.md)**

Python 3.13+로 작성된 모듈형 Discord 봇입니다. 서버 관리, 미디어 재생, 음성 출력, 자동화 기능을 깔끔한 계층형 아키텍처 위에 결합합니다.

제공 기능:

- **Minecraft 서버 관리** — 전체 수명주기(생성/시작/종료/조회), RCON 명령 실행, Discord↔UUID 연동, 화이트리스트 관리, 유휴 시 자동 종료.
- **YouTube 음악 재생** — 동영상·플레이리스트·검색어 재생, 서버(guild)별 독립 큐, 반복 모드, FFmpeg 스트리밍.
- **텍스트 음성 변환(TTS)** — 음성 채널에 입장해 메시지를 읽어주며, 사용자별 음성(목소리) 설정 지원.
- **급식 자동 출력** — 스케줄러가 매일 07:00(Asia/Seoul)에 NEIS 공개 API에서 급식 정보를 받아 Discord로 게시.
- **Discord 운영 로깅 시스템** — 모든 로그를 콘솔·회전(rotating) 파일·Discord 채널(Embed)로 동시 출력 (유계 비동기 큐 + Rate Limit 대응).

## 기술 스택

| 영역 | 기술 |
| --- | --- |
| 언어 | Python 3.13+ |
| Discord | `discord.py` 2.4+ (슬래시 / 애플리케이션 커맨드) |
| 데이터베이스 | PostgreSQL + `asyncpg` + SQLAlchemy 2.0 + Alembic |
| 스케줄링 | APScheduler (async) |
| HTTP | `aiohttp` |
| 설정 | `pydantic-settings` (env 검증) |
| 로깅 | `structlog` |
| 오디오 | `yt-dlp`, `edge-tts`, FFmpeg, `PyNaCl` |
| 도구 | `ruff`, `black`, `pytest` |

## 아키텍처

이 프로젝트는 엄격한 계층 구조를 따릅니다. **Repository는 SQL만 수행**하고, **Service가 비즈니스 로직을 담당**하며, **Feature(Cog)가 Discord에 커맨드를 노출**합니다. 의존성 주입 컨테이너(`core/container.py`)가 시작 시 전체 의존성 그래프를 한 번에 구성합니다.

```mermaid
flowchart LR
    subgraph Discord
        Cmd[슬래시 커맨드]
    end
    Cmd --> Cog[Feature Cog<br/>features/*]
    Cog --> Svc[Service<br/>services/*]
    Svc --> Repo[Repository<br/>repository/*]
    Repo --> DB[(PostgreSQL)]
    Svc --> Voice[VoiceManager / AudioManager]
    Svc --> Sched[Scheduler<br/>APScheduler]
    Log[Logger<br/>core/logger] --> Q[Async Queue<br/>bounded 200] --> Worker[DiscordLogWorker] --> LChan[(Discord 로그 채널)]
```

라이프사이클 계약:

```text
Bot Started → Bot Ready → Scheduler Start
Scheduler Shutdown → Bot Close → DB Close
```

## 프로젝트 구조

```text
bot/                 프로세스 진입점 (main.py)
config/              Settings — 환경 변수 검증 (pydantic-settings)
core/                container, database, discord, logger, scheduler,
                     exceptions, discord_handler (로그 워커)
models/              SQLAlchemy ORM 엔티티
repository/          데이터 접근 계층 (SQL만 담당, 기능별 분리)
services/            비즈니스 로직 (기능별 분리)
features/            Discord Cog, 기능별 1개
voice/               VoiceManager / AudioManager, 음악 플레이어, TTS 프로바이더
utils/               공용 헬퍼
migrations/          Alembic 마이그레이션
tests/               pytest 테스트
docker/, Dockerfile, docker-compose.yml
```

## 요구 사항

- Python 3.13+
- PostgreSQL (또는 Docker)
- FFmpeg, `libopus`, `libsodium` — 음성 기능에 필요
- Java 25+ — Minecraft 서버 실행에 필요 (Paper 26.1+)

## 설치 (로컬)

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

환경 파일 생성:

```bash
cp .env.example .env
```

로컬 PostgreSQL을 띄우고 `DATABASE_DSN`을 설정합니다:

```bash
createdb plaything
```

마이그레이션 적용 (봇 시작 시에도 자동 실행됩니다):

```bash
alembic upgrade head
```

봇 실행:

```bash
source .venv/bin/activate
python -m bot.main
```

## Docker 실행

`.env`에 실제 `DISCORD_TOKEN` 등을 채운 뒤:

```bash
docker compose up --build
```

PostgreSQL 컨테이너가 healthy 상태가 될 때까지 기다린 후 봇이 자동으로 시작됩니다. 컨테이너는:

- Minecraft 포트 범위(`MC_PORT_START`–`MC_PORT_END`)를 publish하고,
- 실제 호스트 Minecraft 폴더(`MC_PARENT_DIRECTORY`)를 bind-mount하여 서버 경로가 실제 호스트 경로가 되도록 하며,
- 로그를 `journald`로 보냅니다.

## 환경 변수

모든 설정은 환경 변수에서 읽습니다 (`.env.example` 참고).

필수 변수:

| 변수 | 설명 |
| --- | --- |
| `DATABASE_DSN` | PostgreSQL 연결 문자열, 예: `postgresql://user:pass@host:port/db` |
| `DISCORD_TOKEN` | Discord 봇 토큰 |

선택 변수:

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `MEAL_URL` | *(빈 값)* | NEIS `mealServiceDietInfo` base URL; 봇이 `&MLSV_YMD=<date>`를 자동으로 붙임 |
| `MEAL_CHANNEL_ID` | `0` | 급식 출력 채널 (`0`이면 `LOG_CHANNEL_ID`로 대체) |
| `TIMEZONE` | `Asia/Seoul` | 스케줄러 트리거와 로그 타임스탬프의 시간대 |
| `MC_PARENT_DIRECTORY` | *(빈 값)* | Minecraft 서버 폴더의 상위 경로 (실제 호스트 경로) |
| `MC_PORT_START` / `MC_PORT_END` | `25565` / `25620` | Minecraft 포트 범위 (자동 할당 및 검증) |
| `MC_PUBLIC_HOST` | *(빈 값)* | 플레이어가 접속할 공인 IP/호스트명 (`/마크_주소 external`) |
| `MC_INTERNAL_HOST` | *(빈 값)* | 같은 공유기(내부망) 사용자용 LAN IP (`/마크_주소 internal`) |
| `MC_JAVA_COMMAND` | `java` | 서버 실행에 사용할 Java 실행 파일 |
| `MC_SERVER_FLAVOR` | `paper` | 서버 jar 종류: `paper` 또는 `vanilla` |
| `MC_SERVER_VERSION` | `1.21.4` | 부트스트랩할 서버 버전 |
| `MC_MAX_MEMORY` | `1G` | 서버 JVM 최대 힙 메모리 |
| `MC_RCON_HOST` | `127.0.0.1` | RCON 호스트 |
| `MC_RCON_PORT` | `25575` | 기본 RCON 포트 |
| `MC_RCON_PASSWORD_SECRET` | `change-me` | 서버별 RCON 패스워드 파생용 시크릿 |
| `MC_MONITOR_INTERVAL_SECONDS` | `30` | 플레이어 수 확인 주기 |
| `MC_IDLE_SHUTDOWN_SECONDS` | `300` | 플레이어 0명 상태에서 자동 종료까지 대기(초) |
| `MC_BACKUP_DIRECTORY` | `./backups` | 서버별 월드 백업이 저장되는 루트 디렉토리 |
| `MC_BACKUP_RETENTION_DAYS` | `90` | 이 기간(일)보다 오래된 백업 삭제 (서버별 최신 1개는 항상 유지) |
| `MC_BACKUP_HOUR` / `MC_BACKUP_MINUTE` | `4` / `0` | 매일 자동 백업 시간(24h); 모든 서버 백업 후 오래된 백업 정리 |
| `FFMPEG_EXECUTABLE` | `ffmpeg` | 오디오 재생에 사용할 FFmpeg 실행 파일 |
| `LOG_CHANNEL_ID` | **필수** | 로그 Embed를 보낼 Discord 채널 (없으면 봇이 시작을 거부) |

## 명령어

모든 명령어는 **슬래시 커맨드**(애플리케이션 커맨드)이며, 봇 시작 시 자동으로 동기화됩니다. 텍스트 접두사는 비활성화되어 있으며 봇은 슬래시 커맨드에만 응답합니다.

### Minecraft — `/마크_*`

| 명령어 | 설명 | 권한 |
| --- | --- | --- |
| `/마크_생성 <alias> [port]` | 새 서버 생성 | 관리자 |
| `/마크_켜 <alias>` | 서버 시작 | — |
| `/마크_꺼 <alias>` | 서버 종료 (RCON `stop`) | — |
| `/마크_주소 <alias> [external\|internal]` | 접속 주소 표시 | — |
| `/마크_유저확인 <alias>` | 접속 중인 플레이어 확인 | — |
| `/마크_상태 <alias>` | 상태 / 포트 / 폴더 / 접속자 확인 | — |
| `/마크_서버` | 관리 중인 모든 서버 목록 | — |
| `/마크_로그 <alias>` | `latest.log` 최근 50줄 표시 | — |
| `/마크_명령어 <alias> <command>` | RCON 명령 실행 | 게임 내 OP |
| `/마크_uuid등록 <user> <uuid>` | Discord 유저 ↔ Minecraft UUID 연결 | 관리자 |
| `/마크_화이트리스트 <alias> <add\|remove> <nickname>` | 화이트리스트 관리 | — |
| `/마크_맵가져오기 <alias> [port]` | 외부 서버 폴더를 가져와 관리 서버로 등록 (Psql 반영 + 화이트리스트/OP 적용) | 관리자 |
| `/마크_백업 <alias>` | 서버 월드 백업 생성 | 관리자 |

동작 요약:

- `/마크_생성`은 폴더 생성 → 기본 파일(`server.properties`, `eula.txt`, whitelist/ops) 작성 → DB Insert를 원자적으로 수행하며, 실패 시 폴더를 삭제하고 롤백합니다. `white-list=true`와 RCON이 기본 활성화됩니다.
- `/마크_맵가져오기`는 이미 디스크에 존재하는 외부 서버/맵 폴더를 `MC_PARENT_DIRECTORY`에서 찾아 PostgreSQL `minecraft_servers`에 등록합니다(관리자 전용). 등록 시 폴더의 `server.properties`에서 포트를 읽고(없으면 자동 할당), RCON·whitelist 강제를 활성화한 뒤, `/마크_uuid등록`으로 연결된 모든 유저를 `whitelist.json`/`ops.json`에 반영합니다. 맵 폴더를 Discord로 올릴 필요 없이 호스트에 폴더만 두면 됩니다.
- `/마크_백업`(관리자 전용)은 서버 월드(`world/`, `world_nether/`, `world_the_end/`), 서버 설정(`server.properties`, `bukkit.yml`, `spigot.yml`, `config/paper-global.yml`), 플러그인 데이터(`plugins/`)를 `<MC_BACKUP_DIRECTORY>/<alias>/<alias>-YYYYMMDD-HHmmss.backup.zip`로 압축합니다. 실행 중인 서버는 `save-off` → `save-all flush` → `save-on` 순서로 안전하게 백업하며, 실패 시 기존 백업을 유지하고 저장을 다시 켭니다. `MC_BACKUP_RETENTION_DAYS`보다 오래된 백업은 자동 삭제되며 서버별 최신 백업 1개는 항상 유지됩니다.
- `/마크_명령어`는 Discord 권한이 아닌 **서버 `ops.json`의 OP 여부**로 검증합니다.
- 실행 중인 서버는 주기적으로(`list` 명령) 폴링되어 `minecraft_sessions`에 플레이어 수가 기록되며, `MC_IDLE_SHUTDOWN_SECONDS` 동안 플레이어가 0명이면 자동 종료됩니다(플레이어 입장 시 타이머 해제).

### Music — YouTube 재생

| 명령어 | 설명 |
| --- | --- |
| `/재생해 <url\|검색어> [loop]` | YouTube 동영상/플레이리스트 재생 또는 검색 (자동완성 프리셋 제공) |
| `/스킵` | 다음 곡으로 이동 |
| `/나가` | 재생 중지 및 음성 채널 퇴장 |
| `/재생정보` | 현재 곡과 큐 정보 표시 |

동작 요약:

- 호출자가 음성 채널에 있어야 합니다.
- 큐는 **서버(guild)별로 독립**되어 있어, 한 서버의 재생이 다른 서버에 영향을 주지 않습니다.
- `loop`는 현재 곡을 반복 재생합니다.
- 곡은 `yt-dlp`로 해석되고 FFmpeg 스트림으로 재생됩니다.
- 공유 `AudioManager` 믹서를 통해 음악과 TTS를 **동시에 재생**할 수 있습니다.
- 오류는 `LOG_CHANNEL_ID`로 로깅됩니다.

### TTS (텍스트 음성 변환)

| 명령어 | 설명 |
| --- | --- |
| `/tts_입장` | 호출자의 음성 채널에 입장하고 메시지 읽기 시작 |
| `/tts_나가기` | 음성 채널에서 퇴장하고 TTS 비활성화 |
| `/voice <voice_id>` | 호출자의 TTS 목소리 설정 (언어 플래그 자동완성 제공) |

활성화된 동안 대상 텍스트 채널의 커맨드가 아닌 메시지는 모두 읽어줍니다(봇 메시지는 무시).

### Meal — 급식 (디버깅/통합 테스트 전용)

급식은 **스케줄러가 자동으로만 게시**합니다 — 사용자 명령어가 필요 없습니다. 아래 슬래시 커맨드는 파이프라인을 수동으로 트리거·검증하기 위한 것입니다:

| 명령어 | 설명 |
| --- | --- |
| `/급식` | 오늘의 급식 조회·출력 |
| `/급식날짜 <년> <월> <일>` | 특정 날짜의 급식 조회·출력 |

### Help

| 명령어 | 설명 |
| --- | --- |
| `/help [category]` | 전체 명령어 개요 또는 특정 카테고리(`minecraft`, `music`, `tts`, `meal`) 상세 도움말 |

### 관리자 / 운영

| 명령어 | 설명 | 권한 |
| --- | --- | --- |
| `/봇_종료` | 봇을 안전하게 종료 | 관리자 |
| `/봇_재시작` | 봇을 안전하게 재시작 (컨테이너/systemd가 재실행) | 관리자 |
| `/봇_상태` | 상태 확인: 봇, 핑, DB, 스케줄러, 음성, 업타임 | 모두 |

## 스케줄링

- **급식 일일 작업** — `Asia/Seoul` 기준 매일 `07:00` cron 트리거 (`features/meal/scheduler.py`). `MEAL_URL`에 `&MLSV_YMD=<yyyyMMdd>`를 붙여 조회하고, NEIS 파서(`INFO-000` 성공 / `INFO-200` 급식 없음)를 적용해 `MEAL_CHANNEL_ID`(없으면 `LOG_CHANNEL_ID`)로 게시합니다. 18시 이후에는 "내일" 급식을 기준으로 날짜를 계산합니다. 모든 실패(HTTP / 타임아웃 / 파서 / Discord 전송)는 예외를 잡아 로그로 기록하며 봇은 크래시하지 않습니다.
- **Heartbeat** — 1분 간격 작업으로 `Scheduler Alive` 로그를 남깁니다.
- 스케줄러 작업 실패/누락은 구조화된 에러/경고 이벤트로 기록됩니다.

급식 메시지 예:

```text
📅 **오늘의 급식 (20260806)**
🍱 **메뉴:**
🍚 쌀밥
🍲 된장국
🍖 불고기
🍎 사과
🔥 **칼로리:** 612.4 Kcal
```

## 로깅 시스템

모든 로그는 세 곳으로 출력됩니다.

1. **콘솔**
2. **파일** — `logs/bot.log` (Rotating)
3. **Discord 로그 채널** — `LOG_CHANNEL_ID` (Embed)

레벨: `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`

파이프라인 (`core/discord_handler.py`):

```text
Logger
   ↓
Async Queue (bounded, maxsize=200 — 폭주 시 신규 레코드 drop)
   ↓
DiscordLogWorker (비동기, 순서 보장, 배치 + 최소 전송 간격)
   ↓
Discord 채널 Embed
```

- `DiscordLogHandler`는 `LogEvent`(level / time / logger / message / event / details)를 블로킹 없이 큐에 넣습니다.
- `DiscordLogWorker`가 큐를 소비해 Embed로 렌더링하고, 메시지당 최대 10개 Embed를 배치하며 메시지 간 최소 1초 간격을 두어 Rate Limit에 대응합니다.
- FIFO 큐 + 단일 순차 워커로 순서를 보장합니다.
- 로거 자체 오류(채널 삭제 / 권한 없음 / API 타임아웃)는 봇을 종료하지 않습니다.

구조화 이벤트는 `core.logger.log_event(logger, "Event Name", level=..., **details)`로 남깁니다. 기록되는 이벤트:

- **Bot**: Started / Ready / Shutdown / Reconnect / Exception / Unhandled Loop Exception
- **Scheduler**: Started / Stopped / job error / job missed
- **Meal**: Fetch Started / Fetch Success / Fetch Failed
- **Minecraft / TTS / Music**: 일반 `log.info` / `log.error` 호출도 자동으로 Embed에 반영됩니다.

## 운영 (관리자)

### 권한 구조

- **일반 사용자** — TTS, YouTube, 음악 제어, 음성 종료.
- **관리자** (Discord `Administrator` 권한 또는 서버 소유자) — 봇 종료/재시작, Minecraft UUID 관리, 시스템 관리.

모든 관리자 권한 게이트는 `core/permissions.py`의 공용 `@admin_only()` 데코레이터로 구현됩니다 (슬래시·접두사 명령 모두 동작).

### 안전 종료 (Graceful Shutdown)

`/봇_종료`는 `bot/main.py`에서 전체 정리 절차를 실행합니다:

```text
명령 → 권한 검사 → 스케줄러 중지 → 백그라운드 작업 취소
→ 음성 연결 종료 → Minecraft 서버 저장·종료 → 급식 서비스 종료
→ "Bot Shutdown" 로그 → 로그 워커 중지 → Discord 로그아웃 → DB 풀 종료 → exit(0)
```

### 안전 재시작 (Graceful Restart)

`/봇_재시작`은 동일한 정리 후 **코드 42**로 프로세스를 종료합니다. Docker의 `restart: unless-stopped` 정책(또는 `Restart=on-failure`를 쓴 systemd 유닛)이 자동으로 프로세스를 다시 띄웁니다.

### 상태 확인 (Health Check)

`/봇_상태`는 봇 온라인 / 핑(ms) / 데이터베이스(ok|error) / 스케줄러(running|stopped) / 음성 연결 수 / 업타임 / 버전을 표시합니다.

### 백그라운드 작업

`core/task_manager.py`가 모든 장기 실행 작업을 추적하며, `shutdown()`은 종료 시 작업을 취소·대기하여 고아 코루틴이 남지 않게 합니다.

### 설정 검증

`DATABASE_DSN`과 `DISCORD_TOKEN`은 필수입니다 (pydantic). `LOG_CHANNEL_ID`도 시작 시 필수입니다 — 없으면 운영 로깅이 불가능하므로 명확한 오류와 함께 시작을 중단합니다.

## 문제 해결 (Troubleshooting)

- **슬래시 커맨드가 안 보이거나 자동완성이 안 됨** — 커맨드는 프로세스 시작 시 1회 동기화됩니다. 배포 후 봇을 재시작하고 Discord 클라이언트를 새로고침하세요. 전역 커맨드 변경은 반영에 시간이 걸릴 수 있습니다.
- **`LOG_CHANNEL_ID` 없음** — 봇이 시작을 거부합니다. 봇이 Embed를 보낼 수 있는 Discord 채널로 설정하세요.
- **컨테이너가 새 코드를 실행하지 않음** — 이미지를 반드시 재빌드해야 합니다: `docker compose up -d --build`. 소스가 바뀌어도 옛 이미지는 이전 동작을 유지합니다.
- **Minecraft 서버가 시작 안 됨** — `MC_PARENT_DIRECTORY`가 컨테이너에 마운트됐는지, Paper/vanilla flavor에 맞는 `MC_JAVA_COMMAND` / `MC_SERVER_VERSION`인지 확인하세요.
- **봇이 계속 재연결됨** — `LOG_CHANNEL_ID`와 Discord 토큰/intents가 올바른지 확인하고 `logs/bot.log` 또는 컨테이너 journal을 살펴보세요.
- **데이터베이스 오류** — Postgres 컨테이너가 healthy인지(`docker compose ps`), `DATABASE_DSN`이 올바른 host/port를 가리키는지 확인하세요.

## 테스트 & 린트

```bash
pip install -e ".[dev]"
ruff check .
black --check .
pytest                        # unit + integration + e2e
pytest --cov --cov-report=term-missing   # 커버리지
```

테스트 구조:

- `tests/unit/` — 단위 테스트 (급식, 마인크래프트, 음악, 로거, 스케줄러, 권한, 태스크 매니저, DB ping, 음성 매니저)
- `tests/integration/` — 서비스 + 컨테이너 결합 (시스템 서비스 상태/수명주기, 컨테이너 조립)
- `tests/e2e/` — 커맨드 플로우 테스트 (관리자 코그 슬래시 + 접두사)

CI (GitHub Actions): `.github/workflows/test.yml` (PostgreSQL 서비스 → 마이그레이션 → pytest + 커버리지), `.github/workflows/lint.yml` (ruff + black).

## 라이선스

MIT
