# Plaything v2

**[한국어](README.ko.md) · English**

A modular Discord bot written in Python 3.13+ that combines server management, media playback, speech, and automation behind a clean, layered architecture.

It ships with:

- **Minecraft server management** — full lifecycle (create / start / stop / inspect), RCON command execution, Discord↔UUID linking, whitelist management, and automatic idle shutdown.
- **YouTube music playback** — videos, playlists, and search results with per-guild queues, loop mode, and FFmpeg streaming.
- **Text-to-speech** — joins a voice channel and reads messages aloud, with per-user voice preferences.
- **Automated meal announcements** — a scheduled job (07:00 Asia/Seoul) that pulls school lunch data from the NEIS open API and posts it to Discord.
- **Discord operations logging** — every log is mirrored to console, a rotating file, and a Discord channel as embeds (bounded async queue + rate-limit handling).

## Tech Stack

| Area | Technology |
| --- | --- |
| Language | Python 3.13+ |
| Discord | `discord.py` 2.4+ (slash / application commands) |
| Database | PostgreSQL + `asyncpg` + SQLAlchemy 2.0 + Alembic |
| Scheduling | APScheduler (async) |
| HTTP | `aiohttp` |
| Configuration | `pydantic-settings` (env-validated) |
| Logging | `structlog` |
| Audio | `yt-dlp`, `edge-tts`, FFmpeg, `PyNaCl` |
| Tooling | `ruff`, `black`, `pytest` |

## Architecture

The project follows a strict layered design. **Repositories only run SQL**; **services own the business logic**; **cogs (features) expose commands** to Discord. A dependency-injection container (`core/container.py`) composes the whole graph once at startup.

```mermaid
flowchart LR
    subgraph Discord
        Cmd[Slash Command]
    end
    Cmd --> Cog[Feature Cog<br/>features/*]
    Cog --> Svc[Service<br/>services/*]
    Svc --> Repo[Repository<br/>repository/*]
    Repo --> DB[(PostgreSQL)]
    Svc --> Voice[VoiceManager / AudioManager]
    Svc --> Sched[Scheduler<br/>APScheduler]
    Log[Logger<br/>core/logger] --> Q[Async Queue<br/>bounded 200] --> Worker[DiscordLogWorker] --> LChan[(Discord log channel)]
```

Lifecycle contract:

```text
Bot Started → Bot Ready → Scheduler Start
Scheduler Shutdown → Bot Close → DB Close
```

## Project Structure

```text
bot/                 process entry point (main.py)
config/              Settings — env validation (pydantic-settings)
core/                container, database, discord, logger, scheduler,
                     exceptions, discord_handler (log worker)
models/              SQLAlchemy ORM entities
repository/          data-access layer (SQL only, per feature)
services/            business logic (per feature)
features/            Discord cogs, one per feature
voice/               VoiceManager / AudioManager, music player, TTS providers
utils/               shared helpers
migrations/          Alembic migrations
tests/               pytest suite
docker/, Dockerfile, docker-compose.yml
```

## Prerequisites

- Python 3.13+
- PostgreSQL (or Docker)
- FFmpeg, `libopus`, `libsodium` — required for voice features
- Java 25+ — required to run Minecraft servers (Paper 26.1+)

## Getting Started (Local)

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Create your environment file:

```bash
cp .env.example .env
```

Set up a local PostgreSQL database and point `DATABASE_DSN` at it:

```bash
createdb plaything
```

Apply migrations (they also run automatically at bot startup):

```bash
alembic upgrade head
```

Run the bot:

```bash
source .venv/bin/activate
python -m bot.main
```

## Running with Docker

Fill in the real `DISCORD_TOKEN` (and any other values) in `.env`, then:

```bash
docker compose up --build
```

The bot waits for the PostgreSQL container to become healthy, then starts automatically. The container:

- publishes the Minecraft port range (`MC_PORT_START`–`MC_PORT_END`),
- bind-mounts the real host Minecraft folder (`MC_PARENT_DIRECTORY`) so server paths are genuine host paths,
- sends logs to `journald`.

## Configuration

All settings are read from environment variables (see `.env.example`). Required variables:

| Variable | Description |
| --- | --- |
| `DATABASE_DSN` | PostgreSQL connection string, e.g. `postgresql://user:pass@host:port/db` |
| `DISCORD_TOKEN` | Discord bot token |

Optional variables:

| Variable | Default | Description |
| --- | --- | --- |
| `MEAL_URL` | *(empty)* | NEIS `mealServiceDietInfo` base URL; the bot appends `&MLSV_YMD=<date>` |
| `MEAL_CHANNEL_ID` | `0` | Channel for the daily meal output (`0` falls back to `LOG_CHANNEL_ID`) |
| `TIMEZONE` | `Asia/Seoul` | Timezone for scheduler triggers and log timestamps |
| `MC_PARENT_DIRECTORY` | *(empty)* | Parent directory that holds Minecraft server folders (real host path) |
| `MC_PORT_START` / `MC_PORT_END` | `25565` / `25620` | Minecraft port range (auto-assigned & validated) |
| `MC_PUBLIC_HOST` | *(empty)* | Public IP/hostname shown to players (`/마크_주소 external`) |
| `MC_INTERNAL_HOST` | *(empty)* | LAN IP for same-router players (`/마크_주소 internal`) |
| `MC_JAVA_COMMAND` | `java` | Java executable used to run servers |
| `MC_SERVER_FLAVOR` | `paper` | Server jar flavor: `paper` or `vanilla` |
| `MC_SERVER_VERSION` | `1.21.4` | Server version to bootstrap |
| `MC_MAX_MEMORY` | `1G` | Max JVM heap for servers |
| `MC_RCON_HOST` | `127.0.0.1` | RCON host |
| `MC_RCON_PORT` | `25575` | Default RCON port |
| `MC_RCON_PASSWORD_SECRET` | `change-me` | Secret used to derive per-server RCON passwords |
| `MC_MONITOR_INTERVAL_SECONDS` | `30` | Player-check interval |
| `MC_IDLE_SHUTDOWN_SECONDS` | `300` | Seconds at 0 players before auto shutdown |
| `MC_BACKUP_DIRECTORY` | `./backups` | Root directory for per-server world backups |
| `MC_BACKUP_RETENTION_DAYS` | `90` | Delete backups older than this many days (newest per server always kept) |
| `FFMPEG_EXECUTABLE` | `ffmpeg` | FFmpeg executable for audio playback |
| `LOG_CHANNEL_ID` | `0` | Discord channel for log embeds (`0` disables) |

## Commands

All commands are **slash commands** (application commands) and are synced automatically at bot startup. A legacy text prefix (`!`) is still configured as `BOT_PREFIX`.

### Minecraft — `/마크_*`

| Command | Description | Permission |
| --- | --- | --- |
| `/마크_생성 <alias> [port]` | Create a new server | Admin |
| `/마크_켜 <alias>` | Start a server | — |
| `/마크_꺼 <alias>` | Stop a server (RCON `stop`) | — |
| `/마크_주소 <alias> [external\|internal]` | Show the connection address | — |
| `/마크_유저확인 <alias>` | List online players | — |
| `/마크_상태 <alias>` | Show status / port / folder / players | — |
| `/마크_서버` | List all managed servers | — |
| `/마크_로그 <alias>` | Show the last 50 lines of `latest.log` | — |
| `/마크_명령어 <alias> <command>` | Run an RCON command | In-game OP |
| `/마크_uuid등록 <user> <uuid>` | Link a Discord user ↔ Minecraft UUID | Admin |
| `/마크_화이트리스트 <alias> <add\|remove> <nickname>` | Manage the whitelist | — |
| `/마크_맵가져오기 <alias> [port]` | Import an external server/map folder as a managed server (registered in PostgreSQL, whitelist/OP applied) | Admin |
| `/마크_백업 <alias>` | Create a server world backup | Admin |

Behavior notes:

- `/마크_생성` atomically creates the folder, writes the default files (`server.properties`, `eula.txt`, whitelist/ops), and inserts the DB row — rolling back (deleting the folder) on failure. `white-list=true` and RCON are enabled by default.
- `/마크_맵가져오기` (admin-only) finds an existing external server/map folder under `MC_PARENT_DIRECTORY`, registers it in the PostgreSQL `minecraft_servers` table, reads the port from the folder's `server.properties` (or auto-assigns one), enables RCON / whitelist enforcement, and applies every Discord↔UUID-registered member to the folder's `whitelist.json` / `ops.json`. Instead of uploading the map folder to Discord, just place the folder on the host.
- `/마크_백업` (admin-only) snapshots the server world (`world/`, `world_nether/`, `world_the_end/`), server config (`server.properties`, `bukkit.yml`, `spigot.yml`, `config/paper-global.yml`) and `plugins/` into `<MC_BACKUP_DIRECTORY>/<alias>/<alias>-YYYYMMDD-HHmmss.backup.zip`. While the server runs it uses `save-off` → `save-all flush` → `save-on`; a failed backup keeps existing archives and re-enables world saving. Backups older than `MC_BACKUP_RETENTION_DAYS` are pruned automatically (the newest backup per server is always kept).
- `/마크_명령어` is gated by the **server's `ops.json` OP status**, not by Discord permissions.
- Running servers are polled (`list` command) on an interval; the player count is recorded in `minecraft_sessions`, and an idle timer shuts the server down after `MC_IDLE_SHUTDOWN_SECONDS` with 0 players (the timer is cancelled when a player joins).

### Music — YouTube playback

| Command | Description |
| --- | --- |
| `/재생해 <url\|search> [loop]` | Play a YouTube video / playlist, or search (presets available via autocomplete) |
| `/스킵` | Skip to the next track |
| `/나가` | Stop playback and leave the voice channel |
| `/재생정보` | Show the current track and queue |

Behavior notes:

- The caller must be in a voice channel.
- Queues are **per-guild** — playback in one server never affects another.
- `loop` repeats the current track.
- Tracks are resolved with `yt-dlp` and streamed via FFmpeg.
- Music and TTS can play **simultaneously** through the shared `AudioManager` mixer.
- Errors are logged to `LOG_CHANNEL_ID`.

### Text-to-Speech

| Command | Description |
| --- | --- |
| `/tts_입장` | Join the caller's voice channel and start reading messages |
| `/tts_나가기` | Leave the voice channel and disable TTS |
| `/voice <voice_id>` | Set the caller's TTS voice preference (autocomplete with language flags) |

While enabled, any non-command message in the target text channel is read aloud (messages from bots are ignored).

### Meal — school lunch (debug / integration only)

The daily meal is published **automatically** by the scheduler — no user command is needed. The following slash commands exist purely to trigger and verify the pipeline on demand:

| Command | Description |
| --- | --- |
| `/급식` | Fetch and publish today's meal |
| `/급식날짜 <year> <month> <day>` | Fetch and publish a specific date's meal |

### Help

| Command | Description |
| --- | --- |
| `/help [category]` | Show an overview of all commands, or the details of one category (`minecraft`, `music`, `tts`, `meal`) |

## Scheduling

- **Daily meal job** — cron trigger at `07:00` in `Asia/Seoul` (`features/meal/scheduler.py`). It fetches `MEAL_URL` with `&MLSV_YMD=<yyyyMMdd>`, applies the NEIS parser (`INFO-000` success / `INFO-200` no meal), and posts to `MEAL_CHANNEL_ID` (falls back to `LOG_CHANNEL_ID`). If the time is past 18:00 it targets tomorrow's meal. All failures (HTTP / timeout / parser / Discord send) are caught and logged — the bot never crashes.
- **Heartbeat** — a one-minute interval job that logs `Scheduler Alive`.
- Scheduler job failures and misses are logged as structured error/warning events.

Meal message example:

```text
📅 **오늘의 급식 (20260806)**
🍱 **메뉴:**
🍚 쌀밥
🍲 된장국
🍖 불고기
🍎 사과
🔥 **칼로리:** 612.4 Kcal
```

## Logging System

Every log is written to three sinks:

1. **Console**
2. **File** — `logs/bot.log` (rotating)
3. **Discord log channel** — `LOG_CHANNEL_ID` (embeds)

Levels: `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`

Pipeline (`core/discord_handler.py`):

```text
Logger
   ↓
Async Queue (bounded, maxsize=200 — drops records when overloaded)
   ↓
DiscordLogWorker (async, order-preserving, batched, min send interval)
   ↓
Discord channel embed
```

- `DiscordLogHandler` enqueues a `LogEvent` (level / time / logger / message / event / details) without blocking.
- `DiscordLogWorker` drains the queue and renders embeds; it batches up to 10 embeds per message with a minimum 1s gap between messages to respect rate limits.
- Order is guaranteed via a FIFO queue and a single sequential worker.
- Logger-side failures (deleted channel, missing permissions, API timeout) never kill the bot.

Structured events are emitted with `core.logger.log_event(logger, "Event Name", level=..., **details)`. Tracked events include:

- **Bot**: Started / Ready / Shutdown / Reconnect / Exception
- **Scheduler**: Started / Stopped / job error / job missed
- **Meal**: Fetch Started / Fetch Success / Fetch Failed
- **Minecraft / TTS / Music**: regular `log.info` / `log.error` calls are reflected as embeds automatically.

## Tests & Linting

```bash
pip install -e ".[dev]"
ruff check .
black --check .
pytest
pytest --cov=core --cov=features --cov-report=term-missing   # coverage
```

Test files:

- `tests/test_meal.py` — success / no-meal / API failure / parser failure / Discord send
- `tests/test_meal_commands.py` — `/급식` and `/급식날짜` success, failure, and date validation
- `tests/test_scheduler.py` — job registration (07:00, Asia/Seoul), execution, and exception handling
- `tests/test_logger.py` — console / file / Discord handlers, queue behavior, embed rendering, worker
- `tests/test_minecraft_connection.py`, `tests/test_music.py` — connection and music pipeline tests

## License

MIT