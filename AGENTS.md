# ClipFlow Codex Guide

## Working Style

- Trả lời thẳng vào bản chất vấn đề, không vòng vo.
- Tìm nguyên nhân từ core trước rồi mới xử lý UI hoặc chi tiết bề mặt.
- Sau mỗi lần sửa code, phải cập nhật file này nếu kiến trúc, trạng thái tính năng, hoặc quy ước code thay đổi.

## Architecture

- Entry point: `app.py`.
- App version: `app/version.py`.
- Startup updater: `app/updater.py`.
  - Check manifest từ `update.json` trên GitHub.
  - Không tự cập nhật khi chạy source bằng Python; self-update chỉ dùng cho bản `.exe` đã build.
  - Bản release tự cập nhật phải upload file `ClipFlow.zip` lên GitHub Releases theo `download_url` trong manifest.
- Update UI/gate: `app/update_ui.py`.
  - App check update khi khởi động và trước khi bắt đầu tải.
  - Nếu không kiểm tra được manifest GitHub thì chặn sử dụng để đảm bảo người dùng luôn ở bản mới nhất.
- Release gate: chạy `python check.py` trước khi push/release.
  - Script chạy unittest, tăng patch version trong `app/version.py`, đặt `latest_version` và `minimum_supported_version` trong `update.json` bằng version mới, build `dist/ClipFlow.exe`, rồi nén `release/ClipFlow.zip`.
  - Dùng `python check.py --publish` sau khi đã commit sạch code để script tự tạo release commit, tag, GitHub Release, upload `release/ClipFlow.zip`, rồi push branch.
  - `--publish` cần GitHub CLI (`gh`) đã đăng nhập bằng `gh auth login`; script sẽ từ chối chạy nếu working tree còn dirty để tránh publish nhầm.
  - Dùng `python check.py --no-bump --no-build` khi chỉ muốn kiểm tra nhanh mà không đổi version hoặc build.
- Main shell: `app/main_window.py`.
  - Chỉ quản lý cửa sổ chính, sidebar, menu nền tảng, theme, ngôn ngữ, icon, dialog thoát.
  - Không đặt logic tải video theo nền tảng trong file này.
- Theme tập trung: `app/themes.py`.
  - Toàn bộ mã màu nằm ở đây.
  - Mọi màu mới phải có đủ biến cho `light` và `dark`.
  - Không hard-code màu trong file view.
- Locale tập trung:
  - Tiếng Việt: `app/locales/vi.py`.
  - Tiếng Anh: `app/locales/en.py`.
  - Loader dịch: `app/locales/__init__.py`.
  - Không hard-code text UI trong widget/view.
- Platform modules:
  - Registry: `app/platforms/registry.py`.
  - Shared config/service types: `app/platforms/common.py`.
  - Shared platform page/download UI: `app/platforms/base_view.py`.
  - Mỗi nền tảng có folder riêng: `app/platforms/<platform>/service.py` và `app/platforms/<platform>/views.py`.

## Current Folder Structure

```text
app/
|-- main_window.py
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
- Sidebar có icon thương hiệu tự vẽ bằng `QPainter`, màu lấy từ `app/themes.py`.
- Có nút đổi sáng/tối và nút đổi Việt/Anh.
- Dialog thoát dùng text từ locale và màu từ theme.
- Tab `Tải video` / `Tải cả trang` dùng style dạng pill/segmented control.
- Không hiển thị thông tin giả trên trang nền tảng.
- Khu vực tải dùng progress bar và status text thân thiện, không hiển thị log kỹ thuật cho người dùng.

## Download Behavior

- Tất cả nền tảng dùng `yt-dlp` qua service riêng.
- Lỗi từ `yt-dlp` phải được map sang `UserFacingDownloadError` trong `app/platforms/common.py`, không để raw error kỹ thuật tràn ra UI.
- `BaseDownloadService` chỉ giữ vòng đời tải chung: tạo thư mục, progress hook, gọi `yt-dlp`, map lỗi, helper FFmpeg và lọc duration.
- Mỗi nền tảng tự định nghĩa `build_yt_dlp_options()` trong `app/platforms/<platform>/service.py`; không đặt format/codec policy theo nền tảng trong base.
- TikTok đang ưu tiên MP4 tương thích trình phát phổ biến: video H.264 và audio AAC; nếu là video thật thì fallback sang format video khác để không bỏ sót, và chỉ dùng stream video/audio tách rời khi tìm thấy FFmpeg.
- `Tải video`: dán một link video bất kỳ, tải một video riêng lẻ, không phân biệt ngắn/dài.
- `Tải cả trang`:
  - TikTok tải toàn bộ trang/profile theo link; post ảnh/slideshow không có video track sẽ bị bỏ qua để batch tiếp tục chạy.
  - Facebook, Instagram, YouTube có lựa chọn:
    - Tải video ngắn toàn trang.
    - Tải video dài toàn trang.
    - Tải cả video ngắn và dài toàn trang.
  - Mặc định là tải video ngắn toàn trang.
- Facebook:
  - Link `/people/...` không tải toàn trang ổn định bằng `yt-dlp`, đang được chặn sớm và hiển thị hướng dẫn người dùng.
  - Tải 1 video Facebook ưu tiên link dạng `watch`, `video.php`, `videos`, `reel`, `share/v`, `share/r`, `story.php`, `permalink.php`, `posts`, `groups`, hoặc `fb.watch`.
  - Facebook service tự dùng cookie từ Firefox/Edge/Chrome nếu tìm thấy profile browser để giảm lỗi login/anti-bot.
  - Nếu đọc cookie browser lỗi DPAPI, service tự retry không dùng cookie; nếu link vẫn không tải công khai được thì báo lỗi cookie thân thiện.
  - Link `share/r/<id>` và `share/v/<id>` có ID số được normalize sang `reel/<id>` hoặc `watch/?v=<id>` trước khi tải.
- Quy ước lọc duration hiện tại:
  - Video ngắn: `duration <= 180` giây.
  - Video dài: `duration >= 181` giây.

## Coding Rules

- Khi thêm nền tảng mới, tạo folder riêng trong `app/platforms/<platform>/`.
- Mỗi nền tảng phải có `service.py` và `views.py`.
- Thêm nền tảng mới vào `app/platforms/registry.py`.
- Khi thêm text UI mới, cập nhật cả `vi.py` và `en.py`.
- Khi thêm màu hoặc style state mới, cập nhật `app/themes.py`.
- Không thêm comment thừa. Chỉ comment khi logic thật sự khó hiểu.
