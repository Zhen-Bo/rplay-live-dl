<p align="center">
    <h1 align="center">RPLAY-LIVE-DL</h1>
</p>
<p align="center">
    <em><code>❯ An automated RPlay live recorder designed for long-running Docker deployments.</code></em>
</p>
<p align="center">
    <img src="https://img.shields.io/github/license/Zhen-Bo/rplay-live-dl?style=flat&logo=opensourceinitiative&logoColor=white&color=00BFFF" alt="license">
    <img src="https://img.shields.io/github/last-commit/Zhen-Bo/rplay-live-dl?style=flat&logo=git&logoColor=white&color=00BFFF" alt="last-commit">
    <img src="https://img.shields.io/github/languages/top/Zhen-Bo/rplay-live-dl?style=flat&color=00BFFF" alt="repo-top-language">
    <img src="https://img.shields.io/github/languages/count/Zhen-Bo/rplay-live-dl?style=flat&color=00BFFF" alt="repo-language-count">
    <a href="https://codecov.io/gh/Zhen-Bo/rplay-live-dl"><img src="https://codecov.io/gh/Zhen-Bo/rplay-live-dl/graph/badge.svg" alt="codecov"></a>
</p>
<p align="center">Built with Docker, Python, Poetry, Pydantic, yt-dlp, and FFmpeg.</p>

---

## 📑 Table of Contents

- [📝 Description](#description)
- [⚠️ v2 Upgrade Notes](#v2-upgrade-notes)
- [✨ Features](#features)
- [🚀 Quick Start](#quick-start)
- [📘 Usage Guide](#usage-guide)
  - [System Requirements](#system-requirements)
  - [Obtaining Credentials](#obtaining-credentials)
  - [Configuration](#configuration)
  - [Download and Merge Flow](#download-and-merge-flow)
  - [Deployment](#deployment)
  - [Directory Structure](#directory-structure)
  - [Troubleshooting](#troubleshooting)
- [🛠️ Development](#development)
- [🔧 Project Structure](#project-structure)
- [👥 Contributing](#contributing)
- [📜 License](#license)

---

<a id="description"></a>

## 📝 Description

`rplay-live-dl` monitors a configured list of RPlay creators, starts recording automatically when a stream goes live, and stores finished recordings under `archive/<creator>/`. It is designed for long-running Docker deployments where configuration, archive files, and logs are mounted from the host.

> [!WARNING]
> **Vibe Coding Notice**: versions with the `-vibe` suffix (for example `2.0.0-vibe`) are AI-assisted releases. They pass automated tests, but you should still review breaking changes before upgrading production deployments.

---

<a id="v2-upgrade-notes"></a>

## ⚠️ v2 Upgrade Notes

`2.0.0-vibe` contains a breaking config-path change.

| Before v2 | Since `2.0.0-vibe` |
| --- | --- |
| `./config.yaml` | `./config/config.yaml` |
| mount one file | mount the whole `./config` directory |

Upgrade checklist:

1. Move your config file from `./config.yaml` to `./config/config.yaml`.
2. Update Docker volume mounts to use `./config:/app/config`.
3. Restart the container.

Startup protection:

- if `./config/config.yaml` is missing
- and legacy `./config.yaml` still exists
- the app exits early with a migration error instead of silently starting with the wrong mount layout

---

<a id="features"></a>

## ✨ Features

- automated live monitoring for multiple creators
- session-aware download tracking to avoid creator-level blocking
- flat archive layout with timestamp-prefixed `.ts` files per session
- immediate merge queueing after raw download completion
- legacy-compatible final filenames such as `#Creator 2026-03-06 Title.mp4`
- duplicate title protection with suffixed outputs like `_1`, `_2`, and so on
- paid/private stream detection with blocked-session handling
- failed merge leaves raw `.ts` files in place for manual recovery
- fail-fast startup validation for legacy config path upgrades
- Docker-first deployment for long-running operation

---

<a id="quick-start"></a>

## 🚀 Quick Start

1. Create your environment file: copy `.env.example` to `.env`.
2. Create `config/config.yaml` from `config.yaml.example`.
3. Fill in your RPlay credentials, creator list, and optionally `apiBaseUrl`.
4. Optionally set `LOG_LEVEL=DEBUG` when you want verbose lifecycle logs.
5. Start the service with Docker Compose.
6. Watch logs until the first polling cycle succeeds.

```bash
# 1) Prepare config files
cp .env.example .env
mkdir -p config
cp config.yaml.example config/config.yaml

# 2) Start the service
docker compose up -d

# 3) Follow logs
docker compose logs -f
```

---

<a id="usage-guide"></a>

## 📘 Usage Guide

### System Requirements

Production:

- Docker
- valid RPlay account credentials
- stable network connectivity
- enough disk space for `.ts` recordings and final `.mp4` files

Development:

- Python 3.11+
- Poetry
- FFmpeg

### Obtaining Credentials

#### `AUTH_TOKEN`

1. Log in to `rplay.live`
2. Open browser DevTools (`F12`)
3. Execute `localStorage.getItem('_AUTHORIZATION_')`
4. Copy the returned token

![auth_token](https://github.com/Zhen-Bo/rplay-live-dl/blob/main/images/auth_token.png?raw=true)

#### `USER_OID`

1. Visit `https://rplay.live/myinfo/`
2. Copy your `User Number`

![user_oid](https://github.com/Zhen-Bo/rplay-live-dl/blob/main/images/user_oid.png?raw=true)

#### Creator ID

1. Visit the creator profile page
2. Open DevTools → Network
3. Refresh the page and search for `CreatorOid`
4. Copy the creator ID

![creator_oid](https://github.com/Zhen-Bo/rplay-live-dl/blob/main/images/creator_oid.png?raw=true)

### Configuration

#### Environment file

Copy `.env.example` to `.env`. Both local runs and the bundled `docker-compose.yaml` use `.env`.

Full example:

```dotenv
# Required: RPlay account credentials
AUTH_TOKEN=your_auth_token
USER_OID=your_user_oid

# Optional: monitor poll interval in seconds (10-3600)
INTERVAL=60

# Optional: application log level
LOG_LEVEL=INFO

# Optional: surface yt-dlp internal debug chatter (`1`, `true`, `yes`, `on`)
LOG_YTDLP_INTERNAL=false

# Optional: log rotation settings
LOG_MAX_SIZE_MB=5
LOG_BACKUP_COUNT=5
LOG_RETENTION_DAYS=30

# Optional: startup metadata shown as Git SHA in logs
# Usually injected automatically during Docker image builds
APP_GIT_SHA=
```

Environment variables:

| Variable | Required | Default | Validation / accepted values | Purpose |
| --- | --- | --- | --- | --- |
| `AUTH_TOKEN` | yes | none | non-empty | RPlay auth token used for API and stream access |
| `USER_OID` | yes | none | non-empty | Your RPlay user identifier |
| `INTERVAL` | no | `60` | integer `10`-`3600` | Poll interval in seconds |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`; invalid values fall back to `INFO` | Console and file log verbosity |
| `LOG_YTDLP_INTERNAL` | no | `false` | truthy values: `1`, `true`, `yes`, `on` | Enables noisy yt-dlp internal debug lines |
| `LOG_MAX_SIZE_MB` | no | `5` | integer `1`-`100` | Maximum size of each log file before rotation |
| `LOG_BACKUP_COUNT` | no | `5` | integer `1`-`50` | Number of rotated log files to keep |
| `LOG_RETENTION_DAYS` | no | `30` | integer `1`-`365` | Age-based cleanup window for old logs |
| `APP_GIT_SHA` | no | empty | free-form string | Startup version metadata shown in logs; usually injected by Docker/image builds |

Notes:

- local runs load from `.env` or process environment variables
- the bundled Docker Compose file mounts `.env` to `/app/.env`
- `LOG_YTDLP_INTERNAL=true` is only for deep diagnosis; it is intentionally noisy

#### Creator configuration

Copy `config.yaml.example` to `config/config.yaml` and edit it like this:

```yaml
# Optional. If missing, the app keeps using the default and writes it back.
apiBaseUrl: https://api.rplay.live

creators:
    - name: "Creator Nickname 1"
      id: "Creator OID 1"
    - name: "Creator Nickname 2"
      id: "Creator OID 2"
```

Configuration keys:

| Key | Required | Default | Validation | Purpose |
| --- | --- | --- | --- | --- |
| `apiBaseUrl` | no | `https://api.rplay.live` | absolute URL; surrounding whitespace is trimmed and trailing `/` is removed | Base URL for the RPlay API |
| `creators` | no | empty list | YAML list | Creators to monitor |
| `creators[].name` | yes | none | non-empty, max `100` characters | Display name used in logs, folder names, and final filenames |
| `creators[].id` | yes | none | non-empty | Creator OID from the RPlay profile/network requests |

Notes:

- if `apiBaseUrl` is missing, the app keeps using the default and writes the key back into `config/config.yaml`
- the monitor re-reads `config/config.yaml` on every poll, so updating `apiBaseUrl` in a running Docker deployment does not require a container restart
- an invalid `apiBaseUrl` is treated as a config error and the current poll is skipped until the file is fixed
- you can temporarily leave `creators: []` while validating a deployment

### Download and Merge Flow

The v2 runtime uses a session-aware download pipeline.

1. **Poll**
   - the monitor loads `config/config.yaml`
   - it refreshes `apiBaseUrl` from config before calling the API
   - it checks live status for all configured creators

2. **Create a session**
   - each live stream gets a session key based on `creator_oid` and the API `oid`
   - a timestamp prefix (`YYYYMMDD_HHMMSS_`) is derived from `recording_started_at` in machine local time
   - raw files are written directly to `archive/<creator>/` using this prefix for isolation

3. **Download raw transport stream files**
   - yt-dlp writes raw outputs as `.ts` directly into `archive/<creator>/`
   - each download task uses a `10`-second socket timeout
   - transient task failures automatically retry up to `3` attempts total with exponential backoff
   - `HTTP 404` on the stream playlist is retried with exponential backoff before the session is marked blocked
   - `HTTP 403` is still treated as immediate blocked/private access
   - `HTTP 401` is treated as an authentication failure instead of a blocked session
   - raw filenames carry the session timestamp prefix, for example:
     - `20260306_120000_#Creator 2026-03-06 Title.ts`
     - `20260306_120000_#Creator 2026-03-06 Title_1.ts`

4. **Queue merge immediately after download completes**
   - as soon as raw download finishes, the merge job is submitted to the merge executor
   - the control loop can move on quickly, so a new live session from the same creator can be picked up without waiting for the old merge to finish

5. **Merge into final `.mp4`**
   - all `.ts` files in `archive/<creator>/` matching the session prefix are merged into one final `.mp4`
   - even if only one raw `.ts` file exists, the final visible output is still `.mp4`

6. **Clean up or preserve for recovery**
   - on success, the `.ts` files matching the session prefix are deleted from `archive/<creator>/`
   - on merge failure, the `.ts` files remain in `archive/<creator>/` for manual inspection and recovery

7. **Observe lifecycle logs**
   - set `LOG_LEVEL=DEBUG` in `.env` to see stream-candidate evaluation and skip reasons
   - set `LOG_YTDLP_INTERNAL=true` only when you need raw yt-dlp internal chatter in addition to app logs
   - the default `INFO` level keeps routine output readable for long-running Docker deployments

#### Final filename rules

Visible final outputs use a clean naming style:

- first session: `#Creator 2026-03-06 Title.mp4`
- second session on the same day with the same title: `#Creator 2026-03-06 Title_1.mp4`
- later duplicates continue as `_2`, `_3`, and so on

Raw `.ts` files carry a timestamp prefix for session isolation:

- `20260306_120000_#Creator 2026-03-06 Title.ts`
- prefix format is `YYYYMMDD_HHMMSS_` in machine local time
- prefix uniquely identifies the recording session; files from different sessions never collide

### Deployment

#### Docker Compose (recommended)

```bash
# Start recording
docker compose up -d

# View logs
docker compose logs -f

# Stop recording
docker compose down

# Update image and restart
docker compose pull
docker compose up -d
```

The bundled `docker-compose.yaml` mounts:

- `./.env` → `/app/.env`
- `./config` → `/app/config`
- `./archive` → `/app/archive`
- `./logs` → `/app/logs`

#### Docker directly

```bash
docker run -d \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/archive:/app/archive \
  -v $(pwd)/logs:/app/logs \
  paverz/rplay-live-dl:latest
```

### Directory Structure

Typical runtime layout:

```text
rplay-live-dl/
├── archive/
│   └── Creator/
│       ├── #Creator 2026-03-06 Title.mp4
│       ├── #Creator 2026-03-06 Title_1.mp4
│       ├── 20260306_120000_#Creator 2026-03-06 Title.ts    ← active or failed session
│       └── 20260306_130000_#Creator 2026-03-06 Title.ts    ← active or failed session
├── config/
│   ├── .gitkeep
│   └── config.yaml
├── .env                 # credentials and runtime settings
├── logs/
└── docker-compose.yaml
```

Notes:

- `.ts` files with a timestamp prefix are either active downloads or unmerged fragments from a failed session
- on successful merge the matching `.ts` files are deleted automatically
- on merge failure the `.ts` files remain in place for manual inspection and recovery
- final user-facing recordings live directly under `archive/<creator>/`

### Troubleshooting

#### 1. Startup fails after upgrading to v2

Symptom:

- the app exits with a config migration error

Cause:

- `./config.yaml` still exists, but `./config/config.yaml` does not

Fix:

- move `./config.yaml` to `./config/config.yaml`
- update Docker to mount `./config:/app/config`

#### 2. Stream is not recording

Check:

- `AUTH_TOKEN` is still valid
- `USER_OID` is correct
- creator ID is correct
- there is enough free disk space
- logs do not show API or connection failures

#### 3. Stream access fails (`401` / `403` / `404`)

Behavior:

- `401` usually means `AUTH_TOKEN` is missing, expired, or invalid
- `403` is treated as immediate blocked/private/paid access
- `404` can appear for a few seconds right after stream start; the downloader retries automatically before marking the current session blocked
- timeout-like transport errors also retry automatically within the same download task
- after retries are exhausted, the current session is marked blocked or failed according to the final error
- a later new session from that creator can still be retried normally

Check:

- refresh `AUTH_TOKEN` if logs show `401`
- if repeated `403` persists, confirm the stream is not paid/private for your account
- if repeated `404` persists after the automatic retries, wait a few seconds and confirm the stream actually remained live

#### 4. Merge failed

Behavior:

- the `.ts` files matching the failed session prefix remain in `archive/<creator>/`
- the app does not silently delete the session fragments

Check:

- FFmpeg availability in the runtime image
- file-system permissions
- available disk space
- the preserved raw `.ts` files in `archive/<creator>/` for manual recovery

#### 5. Shutdown takes time

Behavior:

- graceful shutdown may wait for active merge work to finish
- this is expected for a long-running recorder that prioritizes keeping completed raw work recoverable

---

<a id="development"></a>

## 🛠️ Development

Install dependencies:

```bash
poetry install --with dev
```

Run locally:

```bash
poetry run python main.py
```

Run tests:

```bash
poetry run pytest
```

Run tests with coverage:

```bash
poetry run pytest --cov --cov-report=xml
```

---

<a id="project-structure"></a>

## 🔧 Project Structure

```text
rplay-live-dl/
├── .github/
│   └── workflows/
│       ├── coverage.yml
│       ├── main.yaml
│       └── test.yml
├── core/
│   ├── config.py
│   ├── constants.py
│   ├── download_merge_executor.py
│   ├── downloader.py
│   ├── env.py
│   ├── live_stream_monitor.py
│   ├── logger.py
│   ├── rplay.py
│   ├── scheduler.py
│   └── utils.py
├── models/
│   ├── config.py
│   ├── download.py
│   ├── env.py
│   └── rplay.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_download_merge_executor.py
│   ├── test_download_models.py
│   ├── test_downloader.py
│   ├── test_env.py
│   ├── test_live_stream_monitor.py
│   ├── test_logger.py
│   ├── test_merge_flow.py
│   ├── test_models.py
│   ├── test_monitor_events.py
│   ├── test_rplay.py
│   ├── test_scheduler.py
│   └── test_utils.py
├── images/
│   ├── auth_token.png
│   ├── creator_oid.png
│   └── user_oid.png
├── config/
│   └── .gitkeep
├── .dockerignore
├── .env.example
├── .gitignore
├── LICENSE
├── config.yaml.example
├── docker-compose.yaml
├── Dockerfile
├── main.py
├── poetry.lock
├── pyproject.toml
├── requirements.txt
└── README.md
```

---

<a id="contributing"></a>

## 👥 Contributing

- **💬 [Join the Discussions](https://github.com/Zhen-Bo/rplay-live-dl/discussions)**: Share ideas, ask questions, or discuss operational trade-offs.
- **🐛 [Report Issues](https://github.com/Zhen-Bo/rplay-live-dl/issues)**: Submit bugs, regressions, or feature requests.
- **💡 [Submit Pull Requests](https://github.com/Zhen-Bo/rplay-live-dl/pulls)**: Review open PRs and contribute improvements.

<details closed>
<summary>Contribution Workflow</summary>

1. **Fork the repository** to your own GitHub account.
2. **Clone locally**:
   ```bash
   git clone https://github.com/Zhen-Bo/rplay-live-dl
   ```
3. **Create a focused branch**:
   ```bash
   git checkout -b your-change
   ```
4. **Make your changes** and keep the scope tight.
5. **Run the relevant verification** before opening a PR:
   ```bash
   poetry run pytest
   ```
6. **Commit with a clear message** using Conventional Commit style when possible.
7. **Push your branch** and open a pull request.
8. **Describe the change clearly** with test evidence and any config or operational impact.

PR checklist:

1. Follows the existing project style and naming conventions.
2. Uses Conventional Commit style for commit messages when practical.
3. Includes tests for behavior changes, or clearly explains why tests were not needed.
4. Updates documentation and example config files when user-facing behavior changes.
5. Calls out any breaking change, migration step, or deployment impact.
</details>

### Contributor Graph

<p align="left">
   <a href="https://github.com/Zhen-Bo/rplay-live-dl/graphs/contributors">
      <img src="https://contrib.rocks/image?repo=Zhen-Bo/rplay-live-dl" alt="Contributor graph">
   </a>
</p>

---

<a id="license"></a>

## 📜 License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
