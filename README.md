# plaything_v2

Discord 봇 기반(Foundation) 프로젝트. 기존 plaything을 갈아엎고 날 것(Discord / PostgreSQL / Scheduler)만 남긴 뼈대다.

이번 단계 구현 범위:

- Discord 봇(prefix `!`, Cog 기반)
- PostgreSQL(asyncpg pool) + Alembic 마이그레이션
- Repository / Service 레이어 분리
- APScheduler
- Console/File/Discord 채널 로깅
- Voice Manager + Audio Manager 인터페이스 설계
- Minecraft / Music / TTS / 급식 Feature 명령 골격
- **Minecraft 서버 관리 (생성/실행/종료/RCON/UUID/화이트리스트/자동종료)**
- **YouTube 음악 재생 (영상/플레이리스트/루프/스킵 지원)**

## 기술 스택

- Python 3.13+
- discord.py, asyncpg, APScheduler, aiohttp, pydantic-settings, structlog, alembic

## 설치 (로컬)

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 환경 변수

`.env.example`을 복사해 `.env`로 만든다.

```bash
cp .env.example .env
```

| 변수 | 설명 | 필수 |
| --- | --- | --- |
| `DATABASE_DSN` | PostgreSQL 연결 문자열 (`postgresql://user:pass@host:port/db`) | ✅ |
| `DISCORD_TOKEN` | Discord 봇 토큰 | ✅ |
| `MEAL_URL` | 급식 API URL (이번 단계 미사용) | |
| `MC_PARENT_DIRECTORY` | Minecraft 서버 폴더 상위 경로 | |
| `MC_JAVA_COMMAND` | Java 실행 파일 (Paper 26.1+는 Java 25 필요) | |
| `MC_SERVER_VERSION` | 서버 부트스트랩용 바닐라 버전 | |
| `MC_RCON_PASSWORD_SECRET` | 서버별 RCON 패스워드 파생용 시크릿 | |
| `MC_IDLE_SHUTDOWN_SECONDS` | 플레이어 0명 시 자동종료 대기(초) | |
| `LOG_CHANNEL_ID` | 로그를 보낼 Discord 채널 id (0이면 비활성) | |

## PostgreSQL (로컬)

직접 PostgreSQL을 띄우고 `.env`의 `DATABASE_DSN`을 맞춘다.

```bash
createdb plaything
```

## 마이그레이션 (로컬)

```bash
alembic upgrade head
```

마이그레이션은 봇 시작 시에도 자동으로 실행된다.

## 로컬 실행

```bash
source .venv/bin/activate
python -m bot.main
```

## Docker 실행

`.env`에 실제 `DISCORD_TOKEN` 등을 채운 뒤:

```bash
docker compose up --build
```

PostgreSQL 컨테이너 준비를 기다린 후 봇이 자동으로 시작된다.

## 의존성 / 린트 / 테스트

```bash
pip install -e ".[dev]"
ruff check .
black --check .
pytest
```

## 프로젝트 구조

```text
bot/                 진입점(main.py)
config/              Settings 클래스(env 검증)
core/                logger/exceptions/scheduler/database/discord/container
models/              ORM 엔티티 + dataclass
repository/          SQL만 담당(feature별 분리)
services/            비즈니스 로직(feature별 분리)
features/            Cog 단위 Feature
voice/               VoiceManager + AudioManager 인터페이스
utils/               공용 헬퍼
migrations/          Alembic 마이그레이션
docker/, Dockerfile, docker-compose.yml
```

## 아키텍처

```text
Discord Event → Service → Repository → Database
```

Repository는 SQL만 수행한다. 비즈니스 로직은 Service가 담당한다.

## 빠른 참고

- 명령어 골격: `features/<feature>/commands.py`
- Feature 추가 시: `features/<name>/commands.py` 작성 후 `bot/main.py`의 `COGS`에 추가
- 로깅 개선: `core/logger.py`

## Minecraft 서버 관리 명령어

모든 명령어는 prefix `!` + `마크` 그룹으로 사용한다.

```text
!마크 생성 <맵이름> [포트]          서버 생성 (관리자)
!마크 켜 <맵이름>                  서버 시작
!마크 꺼 <맵이름>                  서버 종료 (RCON stop)
!마크 유저확인 <맵이름>            접속자 확인
!마크 상태 <맵이름>                상태/포트/폴더/접속자 확인
!마크 명령어 <맵이름> <명령어>     RCON 실행 (Minecraft OP 권한 필요)
!마크 UUID등록 <유저> <UUID>       Discord↔UUID 연결 (관리자)
!마크 화이트리스트 추가/제거 <맵이름> <닉네임>
```

동작 요약:

- `!마크 생성`은 폴더 생성 → 기본 파일(server.properties/eula/whitelist/ops) 작성 → DB Insert를 원자적으로 수행하며, 실패 시 폴더를 삭제하고 롤백한다. `white-list=true`, RCON이 기본 활성화된다.
- `!마크 명령어`는 Discord 권한이 아닌 **서버 ops.json 기준 OP 여부**로 검증한다.
- 실행 중인 서버는 주기적으로 `list`를 조회해 `minecraft_sessions`에 플레이어 수를 기록하고, 0명이 지속되면 자동 종료 타이머가 동작한다(플레이어 입장 시 타이머 해제).

## YouTube 음악 재생 명령어

모든 명령어는 prefix `!`로 사용한다.

```text
!재생해 <URL> [계속]         YouTube 영상/플레이리스트 재생
!스킵                      다음 곡으로 이동
!나가                      재생 중지 및 음성 채널 퇴장
!재생정보                   현재 재생 상태 조회
```

지원하는 URL:
- 개별 영상: `https://www.youtube.com/watch?v=xxxxx`
- 플레이리스트: `https://www.youtube.com/playlist?list=xxxxx`

사용 예:
```text
!재생해 https://www.youtube.com/watch?v=dQw4w9WgXcQ
!재생해 https://www.youtube.com/playlist?list=PL1234567890 계속
!스킵
!나가
!재생정보
```

동작 요약:

- 사용자가 음성 채널에 있어야 명령어 실행 가능 (없으면 에러)
- 각 서버(guild)별로 독립적인 Queue 관리 (한 서버의 재생이 다른 서버에 영향 없음)
- `계속` 파라미터로 Loop 모드 활성화 (현재 곡 반복 재생)
- YouTube 영상/플레이리스트는 yt-dlp로 추출되며, FFmpeg 스트림으로 재생
- TTS와 동시 사용 가능 (스피커가 말하는 동안에도 음악 재생됨)
- 에러 발생 시 LOG_CHANNEL_ID로 설정된 채널에 로그 전송