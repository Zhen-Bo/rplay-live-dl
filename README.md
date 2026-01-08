<p align="center">
    <h1 align="center">RPLAY-LIVE-DL</h1>
</p>
<p align="center">
    <em><code>❯ An automated Docker-based tool for downloading rPlay live streams with multi-stream monitoring and custom naming.</code></em>
</p>
<p align="center">
    <img src="https://img.shields.io/github/license/Zhen-Bo/rplay-live-dl?style=flat&logo=opensourceinitiative&logoColor=white&color=00BFFF" alt="license">
    <img src="https://img.shields.io/github/last-commit/Zhen-Bo/rplay-live-dl?style=flat&logo=git&logoColor=white&color=00BFFF" alt="last-commit">
    <img src="https://img.shields.io/github/languages/top/Zhen-Bo/rplay-live-dl?style=flat&color=00BFFF" alt="repo-top-language">
    <img src="https://img.shields.io/github/languages/count/Zhen-Bo/rplay-live-dl?style=flat&color=00BFFF" alt="repo-language-count">
</p>
<p align="center">Built with the tools and technologies:</p>
<p align="center">
    <img src="https://img.shields.io/badge/Docker-2496ED.svg?style=flat&logo=Docker&logoColor=white" alt="Docker">
    <img src="https://img.shields.io/badge/Python-3776AB.svg?style=flat&logo=Python&logoColor=ffdd54" alt="Python">
    <img src="https://img.shields.io/badge/Poetry-60A5FA.svg?style=flat&logo=Poetry&logoColor=0B3D8D" alt="Poetry">
    <img src="https://img.shields.io/badge/Pydantic-E92063.svg?style=flat&logo=Pydantic&logoColor=white" alt="Pydantic">
    <img src="https://shields.io/badge/FFmpeg-%23171717.svg?style=flat&logo=ffmpeg&logoColor=5cb85c" alt="ffmpeg">
</p>
<br>

---

## 📑 Table of Contents

-   [📝 Description](#-description)
-   [✨ Features](#-features)
-   [❗ Known Issues](#-known-issues)
-   [📘 Usage Guide](#-usage-guide)
    -   [System Requirements](#system-requirements)
    -   [Obtaining Credentials](#obtaining-credentials)
        -   [AUTH_TOKEN](#auth_token)
        -   [USER_OID](#user_oid)
        -   [Creator ID](#creator-id)
    -   [Configuration](#configuration)
    -   [Deployment](#deployment)
    -   [Directory Structure](#directory-structure)
    -   [Troubleshooting](#troubleshooting)
-   [🔧 Project Structure](#-project-structure)
-   [👥 Contributing](#-contributing)
-   [📜 License](#-license)

---

## 📝 Description

rplay-live-dl is a easily deployable solution for recording Rplay live stream content. The system regularly checks streaming status based on a user-configured list of content creators and automatically initiates recording when streams begin. All recorded content is organized by creator name for efficient management and retrieval.

---

## ✨ Features

-   Automated live stream monitoring and downloading system built with Python
-   Docker container deployment support for consistent runtime environment
-   Customizable monitoring intervals
-   Simultaneous monitoring of multiple streamers
-   Automated file management with creator-based organization
-   Environment variable configuration for deployment flexibility

---

## ❗ Known Issues

-   [ ] Can't handle M3U8 404 Error (EX: useBonusCoinTicket, useSecretKey...) - paid content is not supported

---

## 📘 Usage Guide

### System Requirements

Production Requirements:

-   Docker environment
-   Valid Rplay platform account
-   Sufficient storage space
-   Stable network connection

Development Requirements:

-   Python environment
-   Package manager (pip or poetry)
-   FFmpeg installed

### Obtaining Credentials

#### AUTH_TOKEN

1. Log into Rplay.live
2. Open browser DevTools (F12)
3. Execute: `localStorage.getItem('_AUTHORIZATION_')`
4. Copy the returned token
   ![auth_token](https://github.com/Zhen-Bo/rplay-live-dl/blob/main/images/auth_token.png?raw=true)

#### USER_OID

1. Visit `https://rplay.live/myinfo/`
2. Copy your `User Number`
   ![user_oid](https://github.com/Zhen-Bo/rplay-live-dl/blob/main/images/user_oid.png?raw=true)

#### Creator ID

1. Visit creator's profile
2. Open DevTools > Network
3. Refresh page and search for "CreatorOid"
4. Copy the creator's ID
   ![creator_oid](https://github.com/Zhen-Bo/rplay-live-dl/blob/main/images/creator_oid.png?raw=true)

### Configuration

1. Environment Setup (`.env`):

    ```
    INTERVAL=check interval in seconds
    USER_OID=your user number
    AUTH_TOKEN=JWT authentication token
    ```

2. Creator Configuration (`config.yaml`):
    ```yaml
    creators:
        - name: "Creator Nickname 1"
          id: "Creator ID 1"
        - name: "Creator Nickname 2"
          id: "Creator ID 2"
    ```

### Deployment

Using Docker Compose (recommended):

```bash
# Start recording
docker-compose up -d

# View logs
docker-compose logs -f

# Stop recording
docker-compose down

# Update application
docker-compose pull
docker-compose up -d
```

Using Docker directly:

```bash
docker run -d \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v $(pwd)/archive:/app/archive \
  -v $(pwd)/logs:/app/logs \
  rplay-live-dl
```

### Directory Structure

```
rplay-live-dl/
├── archive/              # Recorded streams
│   └── [Creator Name]/   # Organized by creator
├── config.yaml           # Creator configuration
├── .env                  # Environment variables
└── logs/                 # Application logs
```

### Troubleshooting

#### Common Issues

1. **Stream Not Recording**

    - Verify `AUTH_TOKEN` is valid
    - Check `creatorOid` is correct
    - Ensure sufficient disk space

2. **M3U8 404 Error**

    - Check if stream uses bonus coins/secret keys...etc

3. **Container Crashes**
    - Check logs: `docker-compose logs`
    - Verify environment configuration
    - Ensure network connectivity

---

## 🔧 Project Structure

```sh
└── rplay-live-dl/
    ├── core/
    │   ├── config.py
    │   ├── downloader.py
    │   ├── env.py
    │   ├── live_stream_monitor.py
    │   ├── logger.py
    │   └── rplay.py
    ├── models/
    │   ├── config.py
    │   ├── env.py
    │   └── rplay.py
    ├── images/
    │   ├── auth_toke.png
    │   ├── user_oid.png
    │   └── creator_oid.png
    ├── LICENSE
    ├── config.yaml.example
    ├── .env.example
    ├── docker-compose.yaml
    ├── dockerfile
    ├── main.py
    ├── poetry.lock
    ├── pyproject.toml
    ├── requirements.txt
    ├── .gitignore
    └── README.md
```

---

## 👥 Contributing

-   **💬 [Join the Discussions](https://github.com/Zhen-Bo/rplay-live-dl/discussions)**: Share your insights, provide feedback, or ask questions.
-   **🐛 [Report Issues](https://github.com/Zhen-Bo/rplay-live-dl/issues)**: Submit bugs found or log feature requests for the `rplay-live-dl` project.
-   **💡 [Submit Pull Requests](https://github.com/Zhen-Bo/rplay-live-dl/pulls)**: Review open PRs, and submit your own PRs.

<details closed>
<summary>Contributing Guidelines</summary>

1. **Fork the Repository**: Start by forking the project repository to your github account.
2. **Clone Locally**: Clone the forked repository to your local machine using a git client.
    ```sh
    git clone https://github.com/Zhen-Bo/rplay-live-dl
    ```
3. **Create a New Branch**: Always work on a new branch, giving it a descriptive name.
    ```sh
    git checkout -b new-feature-x
    ```
4. **Make Your Changes**: Develop and test your changes locally.
5. **Commit Your Changes**: Commit with a clear message describing your updates.
    ```sh
    git commit -m 'Implemented new feature x.'
    ```
6. **Push to github**: Push the changes to your forked repository.
    ```sh
    git push origin new-feature-x
    ```
7. **Submit a Pull Request**: Create a PR against the original project repository. Clearly describe the changes and their motivations.
8. **Review**: Once your PR is reviewed and approved, it will be merged into the main branch. Congratulations on your contribution!

Notice! Please ensure your PR:

1. Follows the existing code style (black + flake8 + isort).
2. Use [conventional commit messages format](https://www.conventionalcommits.org/en/v1.0.0/)
3. Updates documentation.
4. Describes the changes made.
 </details>

### Contributor Graph

<br>
<p align="left">
   <a href="https://github.com{/Zhen-Bo/rplay-live-dl/}graphs/contributors">
      <img src="https://contrib.rocks/image?repo=Zhen-Bo/rplay-live-dl">
   </a>
</p>

## 📜 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---
