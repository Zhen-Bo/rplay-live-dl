<p align="center">
    <img src="https://rplay.live/img/rplay.0e1b89a7.svg" align="center" width="30%">
</p>
<p align="center"><h1 align="center">RPLAY-LIVE-DL</h1></p>
<p align="center">
	<em><code>❯ An automated Docker-based tool for downloading rPlay live streams with multi-stream monitoring and custom naming.</code></em>
</p>
<p align="center">
	<img src="https://img.shields.io/github/v/release/Zhen-Bo/rplay-live-dl?style=flat&logo=github&logoColor=white&color=brightgreen&label=Lates version" alt="Latest-version">
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

## 📜 Table of Contents

- [📜 Table of Contents](#-table-of-contents)
- [🔰 Description](#-description)
- [✨ Features](#-features)
- [🌡️ Known Issues](#️-known-issues)
- [📘 Usage Guide](#-usage-guide)
  - [Environment Setup](#environment-setup)
  - [Deployment Options](#deployment-options)
  - [Storage Structure](#storage-structure)
- [👀 Additional Information](#-additional-information)
  - [Obtaining Required Information](#obtaining-required-information)
  - [Important Notes](#important-notes)

## 🔰 Description

This project focuses on providing a stable and easily deployable solution for recording Rplay live stream content. The system regularly checks the streaming status based on a user-configured list of streamers and automatically initiates recording when streams begin. All recorded content is organized by creator name for efficient management and retrieval.

System Requirements:
- Docker environment
- Valid Rplay platform account
- Sufficient storage space
- Stable network connection

Development Requirements:
- Python environment installed
- Either `pip` or `poetry` for package management
- ffmpeg installed

## ✨ Features

- Automated live stream monitoring and downloading system built with Python
- Docker container deployment support for consistent runtime environment
- Customizable monitoring intervals for flexible resource management
- Simultaneous monitoring of multiple streamers
- Automated file management with creator-based organization
- Environment variable configuration for deployment flexibility

## 🌡️ Known Issues

- [ ] 💩 Doc
- [ ] 💩 & completely untested code
- [ ] Logger will 💩 around in logs folder
- [ ] Can't handle M3U8 404 Error (EX: useBonusCoinTicket, useSecretKey...)

## 📘 Usage Guide

### Environment Setup

Follow these steps to configure your environment:

1. Copy `.env.example` to `.env` and configure:
   ```
   INTERVAL=check interval in seconds
   USER_OID=your user number in https://rplay.live/myinfo/
   AUTH_TOKEN=JWT authentication token
   ```

2. Copy `config.yaml.example` to `config.yaml` and configure creators to monitor:
   ```yaml
   creators:
       - name: "Creator Nickname 1"
         id: "Creator ID 1"
       - name: "Creator Nickname 2"
         id: "Creator ID 2"
   ```

### Deployment Options

Using Docker Compose (recommended):

```bash
docker-compose up -d
```

Or using Docker directly:

```bash
docker run -d \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v $(pwd)/archive:/app/archive \
  -v $(pwd)/logs:/app/logs \
  rplay-live-dl
```

### Storage Structure

Downloaded content is automatically organized in the following structure:
```
archive/
    └── [Creator Nickname]/
        └── [Stream Recordings]
```

## 👀 Additional Information

### Obtaining Required Information

- USER_OID: Available at `https://rplay.live/myinfo/`
- AUTH_TOKEN: Find JWT token in browser developer tools during login process
- Creator ID: Search for "creatorOid" in developer tools on creator's profile page

### Important Notes

- AUTH_TOKEN has an expiration period and requires regular updates
- Regular monitoring of storage usage is recommended
- Docker deployment is recommended for environment consistency
