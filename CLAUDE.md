# ClipFlow Claude Code

## Working Style

- Trả lời thẳng vào bản chất vấn đề, không vòng vo.
- Tìm nguyên nhân từ core trước rồi mới xử lý UI hoặc chi tiết bề mặt.
- Sau mỗi lần sửa code, phải cập nhật file này nếu kiến trúc, trạng thái tính năng, hoặc quy ước code thay đổi.

## Architecture

### Entry & Version
- Entry point: `app.py`.
- App version: `app/version.py` — chứa `APP_VERSION` và `UPDATE_MANIFEST_URL`.
  - `UPDATE_MANIFEST_URL` trỏ tới `https://course-finder-3v7y.onrender.com/licenses/version/` (backend proxy), **không** trỏ trực tiếp GitHub raw để ẩn link repo khỏi traffic sniffing.

### Startup Flow
Thứ tự khởi động trong `app/main_window.py → main()`:
1. Tạo `QApplication`
2. Load app icon từ `assets/icon.ico`
3. **Single instance guard** (`app/single_instance.py`) — nếu đã có instance đang chạy thì focus cửa sổ cũ và thoát
4. **License gate** (`app/license_gate_ui.py → ensure_license_allowed()`) — nếu chưa activate hoặc hết hạn thì chặn
5. **Update gate** (`app/update_ui.py → ensure_update_allowed()`) — nếu version cũ hơn `minimum_supported_version` thì bắt buộc update
6. Hiện `MainWindow`

### License System
- **`app/license_manager.py`** — toàn bộ logic license, không phụ thuộc Qt:
  - `get_hwid()` — fingerprint máy bằng `sha256(node|machine|system|processor)[:32]`
  - `activate(key)` — gọi `POST /licenses/activate/` với `{key, hwid}`, nhận `device_token`, lưu cache local
  - `validate()` — kiểm tra license; dùng cache nếu còn hạn 24h, gọi server nếu hết hạn, grace period 1h nếu server offline
  - Cache lưu tại `%APPDATA%\ClipFlow\session.dat`, XOR-encrypt bằng HMAC-derived key từ HWID rồi base64. **Không lưu key gốc** — chỉ lưu `device_token`.
  - `_SIG_KEY` phải khớp với `LICENSE_HMAC_SECRET` env var trên Render. Mặc định `"cf-hmac-default-dev"` cho dev.
  - `_API_BASE` = `http://127.0.0.1:8000/licenses` khi dev local, đổi sang `https://course-finder-3v7y.onrender.com/licenses` khi production.
- **`app/license_gate_ui.py`** — PyQt6 dialog:
  - `ensure_license_allowed()` — gọi `validate()`, nếu `"not_found"` hiện dialog nhập key, nếu `"expired"`/`"revoked"`/`"device_mismatch"` hiện thông báo block.
  - Dialog nhập key: placeholder `CF-XXXX-XXXX-XXXX`, auto-uppercase, gọi `activate()` khi submit.

### Update System
- `app/updater.py` — fetch manifest, parse version, download zip, launch PowerShell self-update script.
- `app/update_ui.py` — UI layer: `ensure_update_allowed()` chặn nếu version cũ, `trigger_update_check()` cho nút manual.
- Manifest được proxy qua backend Render (`/licenses/version/`) thay vì gọi GitHub raw trực tiếp.
- Self-update chỉ chạy trên bản `.exe` đã build (`is_packaged_app()`), không chạy khi dev.
- Mỗi lần `check.py --publish` đều set cả `latest_version` lẫn `minimum_supported_version` bằng version mới → mọi bản cũ đều bị force-update.

### Release Gate (`check.py`)
```
python check.py              → test + bump patch + build exe + zip
python check.py --publish    → trên + git commit + tag + push + gh release + push branch
python check.py --no-bump    → giữ version, build lại
python check.py --no-build   → chỉ test + bump version, không build
python check.py --no-bump --no-build  → chỉ test nhanh
```
- Tự sinh `assets/icon.ico` bằng `generate_icon.py` nếu chưa có.
- `--publish` yêu cầu working tree sạch và `gh auth login` đã xong.

### Single Instance
- `app/single_instance.py` — `SingleInstanceGuard` dùng `QLocalServer`/`QLocalSocket`.
- Instance thứ 2 gửi signal `"activate"` tới instance đầu rồi thoát.
- Instance đầu nhận signal → `window.raise_()` + `activateWindow()`.

### Main Shell
- `app/main_window.py` — quản lý cửa sổ chính, sidebar, menu nền tảng, theme, ngôn ngữ, icon, dialog thoát.
- Sidebar footer: hiện `ClipFlow {APP_VERSION}` + nút "Cập nhật" (gọi `trigger_update_check`).
- App icon tự vẽ bằng `QPainter` trong sidebar; icon file `.ico` load từ `assets/icon.ico` cho taskbar/exe.
- Không đặt logic tải video theo nền tảng trong file này.

### Theme & Locale
- Theme tập trung: `app/themes.py` — toàn bộ mã màu cho `light` và `dark`. Không hard-code màu trong view.
- Locale tập trung: `app/locales/vi.py` + `app/locales/en.py`. Không hard-code text UI.
- Loader: `app/locales/__init__.py → translate(language, key, **kwargs)`.
- Key locale mới thêm: `license.*` cho toàn bộ text liên quan đến license gate.

### Platform Modules
- Registry: `app/platforms/registry.py`.
- Shared config/service types: `app/platforms/common.py`.
  - `PlatformConfig.supports_manual_cookies=True` cho nền tảng cần ô cookie trong UI.
- Shared platform page/download UI: `app/platforms/base_view.py`.
  - `start_download()` kiểm tra **license** rồi **version** trước khi bắt đầu tải.
- Mỗi nền tảng: `app/platforms/<platform>/service.py` + `app/platforms/<platform>/views.py`.

## Current Folder Structure

```text
app/
|-- cookie_store.py
|-- license_manager.py       ← license validation, cache, HWID, HMAC
|-- license_gate_ui.py       ← PyQt6 dialog kích hoạt + block messages
|-- main_window.py
|-- single_instance.py
|-- updater.py
|-- update_ui.py
|-- version.py
|-- themes.py
|-- tests/
|   `-- test_tiktok_service.py
|-- locales/
|   |-- __init__.py
|   |-- vi.py
|   `-- en.py
`-- platforms/
    |-- common.py
    |-- base_view.py
    |-- registry.py
    |-- tiktok/
    |   |-- service.py
    |   `-- views.py
    |-- facebook/
    |   |-- service.py
    |   `-- views.py
    |-- instagram/
    |   |-- service.py
    |   `-- views.py
    `-- youtube/
        |-- service.py
        `-- views.py
```

## UI State

- App mở bằng `showMaximized()`.
- Màn hình license gate hiện trước cửa sổ chính nếu chưa kích hoạt.
- Sidebar có icon thương hiệu tự vẽ bằng `QPainter`, màu lấy từ `app/themes.py`.
- Sidebar footer: version label (`ClipFlow 0.1.x`) + nút "Cập nhật".
- Có nút đổi sáng/tối và nút đổi Việt/Anh.
- Dialog thoát dùng text từ locale và màu từ theme.
- Tab `Tải video` / `Tải cả trang` dùng style dạng pill/segmented control.
- Facebook và Instagram có ô dán cookie đăng nhập, ẩn nội dung nhập, nút hướng dẫn lấy cookie bằng CookiePeek, tự đồng bộ cookie giữa hai tab, lưu lại local cho lần sau.
- Tab `Tải cả trang` của mọi nền tảng có nút `Dừng` khi đang tải.
- Không hiển thị thông tin giả trên trang nền tảng.
- Khu vực tải dùng progress bar và status text thân thiện, không hiển thị log kỹ thuật.

## Download Behavior

- Tất cả nền tảng dùng `yt-dlp` qua service riêng.
- Lỗi từ `yt-dlp` phải được map sang `UserFacingDownloadError` trong `app/platforms/common.py`.
- `BaseDownloadService` giữ vòng đời tải chung: tạo thư mục, progress hook, gọi `yt-dlp`, map lỗi, helper FFmpeg và lọc duration.
- `BaseDownloadService` giữ cờ hủy tải chung; nút `Dừng` gọi service cancel, raise `DownloadCancelled`.
- Cookie người dùng dán trong UI lưu bởi `app/cookie_store.py` theo nền tảng (DPAPI-encrypted trên Windows). Service chuyển thành file tạm Netscape cho `yt-dlp`, xóa sau khi tải xong.
- Mỗi nền tảng tự định nghĩa `build_yt_dlp_options()` trong service; không đặt format/codec policy trong base.
- TikTok: ưu tiên H.264+AAC, fallback format khác nếu không có MP4, chỉ dùng stream tách rời khi có FFmpeg.
- YouTube: yêu cầu FFmpeg, ưu tiên M4A/AAC, không fallback về stream gộp sẵn.
- `Tải video`: 1 link, 1 video, không phân biệt ngắn/dài.
- `Tải cả trang`:
  - TikTok: tải toàn profile; bỏ qua slideshow không có video track.
  - Instagram: dùng API profile + clips + feed riêng; dùng Clips API (`/api/v1/clips/user/`) làm nguồn chính cho Reels; bỏ qua carousel/ảnh/post giới hạn audience; ưu tiên Full HD ≤ 1920px, H.264+AAC/M4A; ưu tiên cookie UI → browser (Firefox/Edge/Chrome); retry không cookie nếu DPAPI lỗi.
  - Facebook: quét HTML trang/videos/reels để gom link trước khi gọi `yt-dlp`; tự đọc cookie browser; ưu tiên cookie UI; retry không cookie nếu DPAPI lỗi; chặn link `/people/...`.
  - YouTube: tải toàn kênh/playlist.
- Lọc duration: ngắn `≤ 180s`, dài `≥ 181s`. Logic nằm trong `BaseDownloadService` nhưng UI hiện không bật lựa chọn ngắn/dài cho nền tảng nào.

## License & Monetization

- Mô hình: bán key 49k/tháng, admin tạo key thủ công sau khi nhận thanh toán.
- Key format: `CF-XXXX-XXXX-XXXX` (uppercase alphanumeric).
- Backend validate tại: `https://course-finder-3v7y.onrender.com/licenses/`.
- **Bảo vệ 4 lớp:**
  1. Key chỉ dùng 1 lần (activate), sau đó app dùng `device_token`.
  2. `device_token` gắn HWID — không dùng được trên máy khác.
  3. Cache local mã hóa bằng HWID-derived key.
  4. Server-side kill switch — admin revoke/expire key có hiệu lực trong ≤ 24h.
- **`_SIG_KEY` trong `app/license_manager.py` phải khớp `LICENSE_HMAC_SECRET` env var trên Render trước khi build exe production.**
- Cache file: `%APPDATA%\ClipFlow\session.dat`.

## Coding Rules

- Khi thêm nền tảng mới, tạo folder riêng trong `app/platforms/<platform>/`.
- Mỗi nền tảng phải có `service.py` và `views.py`.
- Thêm nền tảng mới vào `app/platforms/registry.py`.
- **TUYỆT ĐỐI KHÔNG hard-code bất kỳ chuỗi UI nào** (button, label, title, hint, message, placeholder...). Mọi text hiển thị cho người dùng phải lấy qua `self.t("key")` / `translate(lang, "key")` từ `vi.py` và `en.py`.
- Khi thêm text UI mới: thêm key vào **cả hai** `vi.py` và `en.py` trước, rồi mới dùng trong code.
- Không dùng chuỗi tiếng Anh hoặc tiếng Việt trực tiếp trong `setText()`, `setPlaceholderText()`, `setWindowTitle()`, `addButton()`, hay bất kỳ widget nào — trừ `objectName()` (dùng cho CSS) và giá trị kỹ thuật không phải UI.
- Khi thêm màu hoặc style state mới, cập nhật `app/themes.py`.
- Không thêm comment thừa. Chỉ comment khi logic thật sự khó hiểu.
- Không hard-code URL, màu trong file view.
