# ClipFlow

Universal Media Downloader for Creators & Power Users.

ClipFlow is a lightweight desktop application built with Python that allows downloading videos from multiple social media platforms through a clean and modern interface.

The project is designed as a personal productivity tool focused on fast media collection, batch downloading, and creator workflow support.

---

## Features

### Supported Platforms

* TikTok
* YouTube
* YouTube Shorts
* Facebook Videos
* Instagram Posts
* Instagram Reels

---

## Core Capabilities

### Single Video Download

Paste a video URL and download instantly.

Supported formats:

* MP4 Video
* Audio Extraction (future)
* HD / Best Available Quality

---

### Short Video Support

ClipFlow supports downloading short-form content such as:

* TikTok videos
* YouTube Shorts
* Instagram Reels
* Facebook Reels

---

### Channel / Profile Download

Download all available videos from:

* TikTok profiles
* YouTube channels
* Playlists
* Creator pages

Facebook, Instagram, and YouTube page downloads now download every video the app can discover from the page without a short/long filter.

Useful for:

* Content archiving
* Research
* Offline backup
* Editing workflows

---

### Batch Download

Queue multiple URLs and download them automatically.

---

### Modern Desktop UI

Built as a native desktop-style application with:

* Clean interface
* Dark mode support
* Vietnamese / English language toggle
* Local saved cookies for Facebook and Instagram
* Maximized startup window
* Progress tracking
* Thumbnail preview
* Download history
* Drag & drop support (planned)

---

## Tech Stack

### Core

* Python
* yt-dlp
* FFmpeg

### Desktop UI

* PyQt6

### Packaging

* PyInstaller

---

## Project Goals

ClipFlow is designed to be:

* Lightweight
* Fast
* Simple to maintain
* Local-first
* Independent from cloud infrastructure

The application focuses on usability and creator productivity rather than becoming a large-scale online service.

---

## Planned Features

* Download queue manager
* Auto-organized folders
* Video metadata viewer
* Smart filename generation
* Clipboard auto-detect
* Thumbnail preview
* Built-in media player
* Video conversion
* Audio extraction
* Subtitle extraction
* Cloudflare R2 upload integration
* AI subtitle generation
* Creator workflow tools

---

## Project Structure

```text
clipflow/
|-- app/
|   |-- __init__.py
|   |-- locales/
|   |   |-- __init__.py
|   |   |-- en.py
|   |   `-- vi.py
|   |-- platforms/
|   |   |-- base_view.py
|   |   |-- common.py
|   |   |-- registry.py
|   |   |-- facebook/
|   |   |   |-- service.py
|   |   |   `-- views.py
|   |   |-- instagram/
|   |   |   |-- service.py
|   |   |   `-- views.py
|   |   |-- tiktok/
|   |   |   |-- service.py
|   |   |   `-- views.py
|   |   `-- youtube/
|   |       |-- service.py
|   |       `-- views.py
|   |-- main_window.py
|   `-- themes.py
|-- downloads/
|-- assets/
|-- ffmpeg/
|-- AGENTS.md
|-- app.py
|-- requirements.txt
`-- README.md
```

---

## Current Implementation Notes

* Platform logic is split into `app/platforms/<platform>/service.py`.
* Platform views are split into `app/platforms/<platform>/views.py`.
* Shared platform UI lives in `app/platforms/base_view.py`.
* Shared download behavior lives in `app/platforms/common.py`.
* Colors are centralized in `app/themes.py`.
* UI text is centralized in `app/locales/vi.py` and `app/locales/en.py`.

---

## Installation

### Clone Repository

```bash
git clone <repository-url>
cd clipflow
```

---

### Install Dependencies

```bash
pip install -r requirements.txt
```

---

### Run Application

```bash
python app.py
```

---

## Build Executable

```bash
pyinstaller --onefile --windowed app.py
```

Generated executable:

```text
dist/ClipFlow.exe
```

---

## Disclaimer

ClipFlow is intended for personal use, content backup, and creator workflow support.

Users are responsible for complying with platform policies, copyright laws, and content ownership regulations when downloading media.

---

## License

MIT License.
