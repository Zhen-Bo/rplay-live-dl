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
- [🆕 What's New in v2](#whats-new-in-v2)
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

<a id="whats-new-in-v2"></a>

## 🆕 What's New in v2

Version `2.0.0-vibe` introduces a session-aware download pipeline:

- each live session is tracked by `creator_oid + stream oid`
- raw media is downloaded into a per-session staging directory as `.ts`
- completed raw outputs are merged into a final `.mp4`
- the visible final filename stays compatible with older releases
- a new live session can start even while the previous session is still merging
- startup now fails fast when the app detects the legacy `./config.yaml` path
- detailed runtime behavior is documented below in [Download and Merge Flow](#download-and-merge-flow)
- `apiBaseUrl` now lives in `config/config.yaml`, is reloaded every poll, and is auto-written with `https://api.rplay.live` if missing

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
- raw `.ts` staging plus final `.mp4` output for better HLS stability
- immediate merge queueing after raw download completion
- legacy-compatible final filenames such as `#Creator 2026-03-06 Title.mp4`
- duplicate title protection with suffixed outputs like `_1`, `_2`, and so on
- paid/private stream detection with blocked-session handling
- failed merge preservation under `archive/<creator>/_failed/`
- fail-fast startup validation for legacy config path upgrades
- Docker-first deployment for long-running operation

---

<a id="quick-start"></a>

## 🚀 Quick Start

1. Create your environment file.
   - Local / Poetry run: copy `.env.example` to `.env`
   - Included `docker-compose.yaml`: copy `.env.example` to `env`, or update the compose volume to mount `.env` instead
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

If you use the stock `docker-compose.yaml` without modification, replace step 1 with:

```bash
cp .env.example env
```

---

<a id="usage-guide"></a>

## 📘 Usage Guide

### System Requirements

Production:

- Docker
- valid RPlay account credentials
- stable network connectivity
- enough disk space for raw `.ts` staging and final `.mp4` files

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

Required values:

```dotenv
INTERVAL=60
USER_OID=your_user_oid
AUTH_TOKEN=your_auth_token
```

Optional log rotation values:

```dotenv
LOG_LEVEL=INFO
LOG_MAX_SIZE_MB=5
LOG_BACKUP_COUNT=5
LOG_RETENTION_DAYS=30
```

Notes:

- `INTERVAL` is in seconds
- `LOG_LEVEL` supports standard names such as `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`
- the application validates required variables on startup
- local runs load from `.env` or process environment variables
- the bundled Docker Compose file maps a host file named `env` to `/app/.env`

#### Creator configuration

Copy `config.yaml.example` to `config/config.yaml` and edit it like this:

```yaml
apiBaseUrl: https://api.rplay.live

creators:
    - name: "Creator Nickname 1"
      id: "Creator ID 1"
    - name: "Creator Nickname 2"
      id: "Creator ID 2"
```

Notes:

- `apiBaseUrl` defaults to `https://api.rplay.live`
- if `apiBaseUrl` is missing, the app keeps using that default and writes the key back into `config/config.yaml`
- the monitor re-reads `config/config.yaml` on every poll, so updating `apiBaseUrl` in a running Docker deployment does not require a container restart
- an invalid `apiBaseUrl` is treated as a config error and the current poll is skipped until the file is fixed

### Download and Merge Flow

The v2 runtime uses a two-step download pipeline.

1. **Poll**
   - the monitor loads `config/config.yaml`
   - it refreshes `apiBaseUrl` from config before calling the API
   - it checks live status for all configured creators

2. **Create a session**
   - each live stream gets a session key based on `creator_oid` and the API `oid`
   - the raw download is isolated in `archive/<creator>/.staging/<session_dir>/`
   - `session_dir` is the filesystem-safe form of the session key, not the raw key string

3. **Download raw transport stream files**
   - yt-dlp writes raw outputs as `.ts`
   - raw staging names keep the familiar visible format, for example:
     - `#Creator 2026-03-06 Title.ts`
     - `#Creator 2026-03-06 Title_1.ts`

4. **Queue merge immediately after download completes**
   - as soon as raw download finishes, the merge job is submitted to the merge executor
   - the control loop can move on quickly, so a new live session from the same creator can be picked up without waiting for the old merge to finish

5. **Merge into final `.mp4`**
   - all `.ts` files in that session staging directory are merged into one final `.mp4`
   - even if only one raw `.ts` file exists, the final visible output is still `.mp4`

6. **Clean up or preserve for recovery**
   - on success, the merged `.ts` files are deleted and the session staging directory is removed
   - on merge failure or timeout, the whole session staging directory is moved to `archive/<creator>/_failed/<session_dir>/`

7. **Observe lifecycle logs**
   - set `LOG_LEVEL=DEBUG` in `.env` to see stream-candidate evaluation and skip reasons
   - the default `INFO` level keeps routine output readable for long-running Docker deployments

#### Final filename rules

Visible final outputs intentionally keep the old naming style:

- first session: `#Creator 2026-03-06 Title.mp4`
- second session on the same day with the same title: `#Creator 2026-03-06 Title_1.mp4`
- later duplicates continue as `_2`, `_3`, and so on

This means:

- users still see the same filename pattern as previous releases
- collisions are resolved only at the final visible output layer
- raw session isolation happens inside `.staging`, not in the archive root

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

- `./env` → `/app/.env`
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
│       ├── .staging/
│       │   └── creator_oid_2026-03-06T12_00_00/
│       │       └── #Creator 2026-03-06 Title.ts
│       └── _failed/
│           └── creator_oid_2026-03-06T13_00_00/
│               └── #Creator 2026-03-06 Title.ts
├── config/
│   ├── .gitkeep
│   └── config.yaml
├── env                  # used by the bundled docker-compose.yaml
├── .env                 # used for local runs or custom docker run usage
├── logs/
└── docker-compose.yaml
```

Notes:

- `.staging` is an internal working area for active or just-finished sessions
- `_failed` keeps raw files visible for manual inspection and recovery
- `.staging` and `_failed` appear only when there is active or failed session data
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

#### 3. Paid/private stream cannot be accessed

Behavior:

- the app logs `🔒 CreatorName: Cannot access stream (likely paid content)`
- the current session is marked blocked
- the same blocked session is not retried repeatedly
- a later new session from that creator can still be retried normally

#### 4. Merge failed

Behavior:

- raw files are preserved under `archive/<creator>/_failed/<session_dir>/`
- the app does not silently delete the session fragments

Check:

- FFmpeg availability in the runtime image
- file-system permissions
- available disk space
- the preserved raw `.ts` files for manual recovery

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
