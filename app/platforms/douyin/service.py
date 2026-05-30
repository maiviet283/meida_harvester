from __future__ import annotations

import html as html_lib
import json
import re
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled, DownloadError

from app.platforms.common import BaseDownloadService, PlatformConfig, ProgressCallback, UserFacingDownloadError


DOUYIN_COMBINED_MP4_FORMATS = (
    "b[ext=mp4][vcodec=h264][acodec=aac]",
    "b[ext=mp4][vcodec^=avc1][acodec^=mp4a]",
    "b[ext=mp4][vcodec=h264][acodec!=none]",
    "b[ext=mp4][vcodec^=avc1][acodec!=none]",
)
DOUYIN_SPLIT_MP4_FORMATS = (
    "bv*[ext=mp4][vcodec=h264]+ba[ext=m4a]",
    "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]",
)
DOUYIN_FALLBACK_VIDEO_FORMATS = (
    "b[ext=mp4][vcodec!=none][acodec!=none]",
    "b[vcodec!=none][acodec!=none]",
)
DOUYIN_FALLBACK_SPLIT_FORMATS = (
    "bv*[ext=mp4][vcodec!=none]+ba[ext=m4a]",
    "bv*[vcodec!=none]+ba",
)
DOUYIN_POST_API_URL = "https://www.douyin.com/aweme/v1/web/aweme/post/"
DOUYIN_SHARE_URL = "https://www.iesdouyin.com/share/video/{video_id}/"
DOUYIN_SHARE_USER_URL = "https://www.iesdouyin.com/share/user/{sec_uid}"
DOUYIN_MAX_PROFILE_PAGES = 80
DOUYIN_PROFILE_PAGE_SIZE = 18
DOUYIN_WEB_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.douyin.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}
DOUYIN_SHARE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.iesdouyin.com/",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/16.0 Mobile/15E148 Safari/604.1"
    ),
}
DOUYIN_SHORT_URL_RE = re.compile(r"(?:https?://)?v\.douyin\.com/[^\s<>'\"]+", re.IGNORECASE)
DOUYIN_URL_RE = re.compile(
    r"(?:https?://)?(?:(?:www|m)\.)?(?:douyin|iesdouyin)\.com/[^\s<>'\"，。；！]+",
    re.IGNORECASE,
)
DOUYIN_ROUTER_DATA_RE = re.compile(
    r"<script[^>]*>\s*window\._ROUTER_DATA\s*=\s*({.*?})\s*</script>",
    re.DOTALL,
)


CONFIG = PlatformConfig(
    key="douyin",
    example_video_url="https://www.douyin.com/video/6961737553342991651",
    example_page_url="https://www.douyin.com/user/MS4wLjABAAAAEKnfa654JAJ_N5lgZDQluwsxmY0lhfmEYNQBBkwGG98",
)


class DouyinService(BaseDownloadService):
    def clean_input_url(self, url: str, mode: str = "") -> str:
        url = self.extract_douyin_url(url)
        if mode == "page":
            sec_uid = self.extract_profile_sec_uid(url)
            if sec_uid:
                return self.canonical_profile_url(sec_uid)
        return url

    def download_urls(
        self,
        urls: list[str],
        folder: str,
        progress: ProgressCallback,
        single: bool,
        page_filter: str,
        emit_initial_progress: bool = True,
    ) -> None:
        Path(folder).mkdir(parents=True, exist_ok=True)
        if emit_initial_progress:
            progress("preparing", 8, None)

        total = max(len(urls), 1)
        completed = 0

        def hook(info: dict) -> None:
            self.raise_if_cancelled()
            status = info.get("status")
            if status == "downloading":
                total_bytes = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
                downloaded = info.get("downloaded_bytes") or 0
                item_percent = downloaded * 100 / total_bytes if total_bytes else 35
                overall = 12 + int(((completed + item_percent / 100) / total) * 80)
                progress("downloading", max(12, min(92, overall)), None)
            elif status == "finished":
                overall = 12 + int(((completed + 1) / total) * 84)
                progress("processing", 95 if total == 1 else max(12, min(92, overall)), None)

        try:
            options = self.build_yt_dlp_options(folder, hook, single)
            match_filter = self.build_cancelable_match_filter(
                options.get("match_filter"),
                page_filter,
                force=not single,
            )
            if match_filter:
                options["match_filter"] = match_filter

            with YoutubeDL(options) as downloader:
                for index, url in enumerate(urls):
                    self.raise_if_cancelled()
                    if emit_initial_progress or not single:
                        progress("reading", min(85, 18 + int(index / total * 24)), None)
                    info = self.extract_video_info(url)
                    downloader.process_ie_result(info, download=True)
                    completed += 1
        except DownloadCancelled as exc:
            raise UserFacingDownloadError("download_cancelled") from exc
        except DownloadError as exc:
            raise self.to_user_error(exc) from exc
        finally:
            self.cleanup_manual_cookie_files()
        progress("finished", 100, None)

    def download_single(self, url: str, folder: str, progress: ProgressCallback) -> None:
        normalized_url = self.normalize_video_url(url)
        if not self.is_supported_video_url(normalized_url):
            raise UserFacingDownloadError("douyin_single_link")
        self.download(normalized_url, folder, progress, single=True, page_filter="all")

    def download_page(
        self,
        url: str,
        folder: str,
        progress: ProgressCallback,
        page_filter: str = "all",
    ) -> None:
        self.raise_if_cancelled()
        progress("reading", 18, None)
        urls = self.collect_profile_video_urls(url, progress)
        if not urls:
            raise UserFacingDownloadError("douyin_page_no_videos")
        self.raise_if_cancelled()
        self.download_urls(urls, folder, progress, single=False, page_filter="all", emit_initial_progress=False)

    def build_yt_dlp_options(self, folder: str, hook: Callable[[dict], None], single: bool) -> dict:
        formats = list(DOUYIN_COMBINED_MP4_FORMATS)
        has_ffmpeg = self.has_ffmpeg()
        if has_ffmpeg:
            formats.extend(DOUYIN_SPLIT_MP4_FORMATS)
        formats.extend(DOUYIN_FALLBACK_VIDEO_FORMATS)
        if has_ffmpeg:
            formats.extend(DOUYIN_FALLBACK_SPLIT_FORMATS)

        options = self.base_yt_dlp_options(folder, hook, single)
        options.update(
            {
                "format": "/".join(formats),
                "format_sort": ["vcodec:h264", "quality", "res", "fps", "acodec:aac"],
                "http_headers": {
                    "User-Agent": DOUYIN_WEB_HEADERS["User-Agent"],
                    "Referer": "https://www.douyin.com/",
                },
            }
        )
        if has_ffmpeg:
            options["merge_output_format"] = "mp4"
        if not single:
            options["noplaylist"] = True
            options["ignoreerrors"] = "only_download"
            options["match_filter"] = self.reject_non_video_post
        return options

    def extract_video_info(self, url: str) -> dict:
        normalized_url = self.normalize_video_url(url)
        video_id = self.extract_video_id(normalized_url)
        if not video_id:
            raise UserFacingDownloadError("douyin_single_link")
        item = self.fetch_share_video_item(video_id)
        return self.build_video_info(item, video_id)

    def fetch_share_video_item(self, video_id: str) -> dict:
        share_url = DOUYIN_SHARE_URL.format(video_id=video_id)
        request = Request(share_url, headers=DOUYIN_SHARE_HEADERS)
        try:
            with urlopen(request, timeout=25) as response:
                page_html = response.read().decode("utf-8", errors="replace")
            router_data = self.extract_router_data(page_html)
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UserFacingDownloadError("douyin_extract_failed") from exc

        loader_data = router_data.get("loaderData") if isinstance(router_data, dict) else None
        if not isinstance(loader_data, dict):
            raise UserFacingDownloadError("douyin_extract_failed")

        for page_data in loader_data.values():
            if not isinstance(page_data, dict):
                continue
            video_info = page_data.get("videoInfoRes")
            if not isinstance(video_info, dict):
                continue
            for item in video_info.get("item_list") or []:
                if isinstance(item, dict) and isinstance(item.get("video"), dict):
                    return item
        raise UserFacingDownloadError("douyin_extract_failed")

    def extract_router_data(self, page_html: str) -> dict:
        match = DOUYIN_ROUTER_DATA_RE.search(page_html)
        if not match:
            raise UserFacingDownloadError("douyin_extract_failed")
        return json.loads(html_lib.unescape(match.group(1)))

    def build_video_info(self, item: dict, fallback_video_id: str) -> dict:
        video = item.get("video") if isinstance(item.get("video"), dict) else {}
        play_addr = video.get("play_addr") if isinstance(video.get("play_addr"), dict) else {}
        play_urls = [url for url in play_addr.get("url_list") or [] if isinstance(url, str) and url.startswith("http")]
        direct_urls = self.build_direct_video_urls(play_urls)
        if not direct_urls:
            raise UserFacingDownloadError("douyin_extract_failed")

        video_id = str(item.get("aweme_id") or item.get("group_id_str") or fallback_video_id)
        title = str(item.get("desc") or f"Douyin {video_id}")
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        duration_ms = video.get("duration")
        duration = duration_ms / 1000 if isinstance(duration_ms, (int, float)) else None
        width = video.get("width") if isinstance(video.get("width"), int) else None
        height = video.get("height") if isinstance(video.get("height"), int) else None
        tags = [
            tag["hashtag_name"]
            for tag in item.get("text_extra") or []
            if isinstance(tag, dict) and isinstance(tag.get("hashtag_name"), str)
        ]

        formats = []
        for index, direct_url in enumerate(direct_urls):
            is_clean_url = "/playwm/" not in direct_url
            formats.append(
                {
                    "url": direct_url,
                    "format_id": f"mp4-{index + 1}",
                    "ext": "mp4",
                    "vcodec": "h264",
                    "acodec": "aac",
                    "preference": 10 if is_clean_url else 0,
                    "quality": 10 if is_clean_url else 0,
                    "width": width,
                    "height": height,
                    "http_headers": {
                        "User-Agent": DOUYIN_SHARE_HEADERS["User-Agent"],
                        "Referer": DOUYIN_SHARE_URL.format(video_id=video_id),
                    },
                }
            )

        return {
            "_type": "video",
            "id": video_id,
            "title": title,
            "description": title,
            "extractor": "Douyin",
            "extractor_key": "Douyin",
            "webpage_url": f"https://www.douyin.com/video/{video_id}",
            "uploader": author.get("nickname") or author.get("short_id") or "Douyin",
            "uploader_id": author.get("short_id") or author.get("uid"),
            "channel_id": author.get("sec_uid"),
            "duration": duration,
            "timestamp": item.get("create_time") if isinstance(item.get("create_time"), int) else None,
            "thumbnail": self.first_url(video.get("cover")),
            "tags": tags,
            "view_count": stats.get("play_count"),
            "like_count": stats.get("digg_count"),
            "comment_count": stats.get("comment_count"),
            "repost_count": stats.get("share_count"),
            "formats": formats,
        }

    def build_direct_video_urls(self, play_urls: list[str]) -> list[str]:
        direct_urls: list[str] = []
        seen: set[str] = set()
        for play_url in play_urls:
            candidates = [play_url.replace("/playwm/", "/play/"), play_url] if "/playwm/" in play_url else [play_url]
            for candidate in candidates:
                if candidate in seen:
                    continue
                seen.add(candidate)
                direct_urls.append(candidate)
        return direct_urls

    def first_url(self, resource: object) -> str | None:
        if not isinstance(resource, dict):
            return None
        for url in resource.get("url_list") or []:
            if isinstance(url, str) and url.startswith("http"):
                return url
        return None

    def collect_profile_video_urls(self, url: str, progress: ProgressCallback | None = None) -> list[str]:
        url = self.normalize_profile_url(url)
        sec_uid = self.extract_profile_sec_uid(url)
        if not sec_uid:
            raise UserFacingDownloadError("douyin_page_link")

        urls: list[str] = []
        seen_video_ids: set[str] = set()
        last_error: UserFacingDownloadError | None = None

        try:
            self.collect_profile_share_video_urls(sec_uid, urls, seen_video_ids, progress)
        except UserFacingDownloadError as exc:
            last_error = exc

        if not urls:
            try:
                self.collect_profile_api_video_urls(sec_uid, url, urls, seen_video_ids, progress)
            except UserFacingDownloadError as exc:
                last_error = exc

        if not urls:
            try:
                page_html = self.fetch_profile_html(url)
                self.add_video_urls_from_html(page_html, urls, seen_video_ids)
            except UserFacingDownloadError as exc:
                last_error = exc

        if not urls and last_error is not None:
            raise last_error
        return urls

    def collect_profile_share_video_urls(
        self,
        sec_uid: str,
        urls: list[str],
        seen_video_ids: set[str],
        progress: ProgressCallback | None = None,
    ) -> None:
        share_url = DOUYIN_SHARE_USER_URL.format(sec_uid=sec_uid)
        request = Request(share_url, headers=DOUYIN_SHARE_HEADERS)
        try:
            with urlopen(request, timeout=25) as response:
                page_html = response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise UserFacingDownloadError("douyin_profile_failed") from exc

        try:
            router_data = self.extract_router_data(page_html)
            self._collect_aweme_ids_from_json(router_data, urls, seen_video_ids)
        except Exception:
            pass

        self.add_video_urls_from_html(page_html, urls, seen_video_ids)

    def _collect_aweme_ids_from_json(self, data: object, urls: list[str], seen_video_ids: set[str]) -> None:
        if isinstance(data, dict):
            for key in ("aweme_id", "awemeId", "group_id_str", "item_id", "itemId"):
                value = data.get(key)
                if isinstance(value, (str, int)):
                    video_id = str(value)
                    if video_id.isdigit() and len(video_id) >= 5:
                        self.add_video_url(video_id, urls, seen_video_ids)
            for child in data.values():
                self._collect_aweme_ids_from_json(child, urls, seen_video_ids)
        elif isinstance(data, list):
            for item in data:
                self._collect_aweme_ids_from_json(item, urls, seen_video_ids)

    def collect_profile_api_video_urls(
        self,
        sec_uid: str,
        profile_url: str,
        urls: list[str],
        seen_video_ids: set[str],
        progress: ProgressCallback | None = None,
    ) -> None:
        cursor = "0"
        seen_cursors = {cursor}
        for page in range(DOUYIN_MAX_PROFILE_PAGES):
            self.raise_if_cancelled()
            if progress:
                progress("reading", min(85, 18 + page), None)

            response = self.fetch_profile_json(sec_uid, cursor, profile_url)
            for item in response.get("aweme_list") or response.get("awemeList") or []:
                self.add_aweme_video_url(item, urls, seen_video_ids)

            has_more = response.get("has_more", response.get("hasMore"))
            next_cursor = response.get("max_cursor", response.get("maxCursor"))
            if not self.is_truthy_flag(has_more) or next_cursor is None:
                break

            cursor = str(next_cursor)
            if cursor in seen_cursors:
                break
            seen_cursors.add(cursor)

    def fetch_profile_json(self, sec_uid: str, cursor: str, profile_url: str) -> dict:
        query = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "sec_user_id": sec_uid,
            "max_cursor": cursor,
            "count": str(DOUYIN_PROFILE_PAGE_SIZE),
            "publish_video_strategy_type": "2",
            "version_code": "170400",
            "version_name": "17.4.0",
            "cookie_enabled": "true",
            "platform": "PC",
            "downlink": "10",
            "effective_type": "4g",
        }
        request = Request(
            f"{DOUYIN_POST_API_URL}?{urlencode(query, quote_via=quote)}",
            headers=self.build_headers(profile_url),
        )
        try:
            with urlopen(request, timeout=25) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise UserFacingDownloadError("douyin_profile_failed") from exc

    def fetch_profile_html(self, url: str) -> str:
        request = Request(url, headers=self.build_headers(url))
        try:
            with urlopen(request, timeout=25) as response:
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise UserFacingDownloadError("douyin_profile_failed") from exc

    def build_headers(self, referer: str = "https://www.douyin.com/") -> dict[str, str]:
        headers = dict(DOUYIN_WEB_HEADERS)
        headers["Referer"] = referer
        manual_cookie = self.get_manual_cookie_header()
        if manual_cookie:
            headers["Cookie"] = manual_cookie
        return headers

    def extract_douyin_url(self, text: str) -> str:
        raw = text.strip()
        for pattern in (DOUYIN_SHORT_URL_RE, DOUYIN_URL_RE):
            match = pattern.search(raw)
            if match:
                url = match.group(0).rstrip(".,，。;；:：!！)]}）】'\"")
                return url if url.startswith(("http://", "https://")) else f"https://{url}"
        return raw

    def is_short_douyin_url(self, url: str) -> bool:
        try:
            return (urlparse(url).hostname or "").lower() == "v.douyin.com"
        except Exception:
            return False

    def resolve_douyin_redirect(self, url: str) -> str:
        request = Request(url, headers=self.build_headers(url))
        try:
            with urlopen(request, timeout=15) as response:
                return response.geturl()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise UserFacingDownloadError("douyin_resolve_failed") from exc

    def add_aweme_video_url(self, item: object, urls: list[str], seen_video_ids: set[str]) -> None:
        if not isinstance(item, dict):
            return
        video_id = str(item.get("aweme_id") or item.get("awemeId") or item.get("group_id") or "")
        if not video_id.isdigit():
            return
        if item.get("images") and not isinstance(item.get("video"), dict):
            return
        self.add_video_url(video_id, urls, seen_video_ids)

    def add_video_urls_from_html(self, page_html: str, urls: list[str], seen_video_ids: set[str]) -> None:
        decoded_html = html_lib.unescape(page_html).replace("\\/", "/")
        patterns = (
            r"https?://(?:www\.)?douyin\.com/video/(\d{5,})",
            r"/video/(\d{5,})",
            r"[\"'](?:aweme_id|awemeId|group_id|groupId|item_id|itemId)[\"']\s*:\s*[\"']?(\d{5,})",
            r"(?:modal_id|aweme_id)=(\d{5,})",
        )
        for pattern in patterns:
            for video_id in re.findall(pattern, decoded_html):
                self.add_video_url(video_id, urls, seen_video_ids)

    def add_video_url(self, video_id: str, urls: list[str], seen_video_ids: set[str]) -> None:
        if video_id in seen_video_ids:
            return
        seen_video_ids.add(video_id)
        urls.append(f"https://www.douyin.com/video/{video_id}")

    def extract_video_id(self, url: str) -> str | None:
        url = self.extract_douyin_url(url)
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            path_parts = [part for part in parsed.path.split("/") if part]
            query = parse_qs(parsed.query)
        except Exception:
            return None

        if "douyin.com" not in host and "iesdouyin.com" not in host:
            return None
        lowered_parts = [part.lower() for part in path_parts]
        if "video" in lowered_parts:
            video_index = lowered_parts.index("video")
            if len(path_parts) > video_index + 1 and path_parts[video_index + 1].isdigit():
                return path_parts[video_index + 1]
        modal_id = (query.get("modal_id") or [""])[0]
        if modal_id.isdigit():
            return modal_id
        return None

    def normalize_video_url(self, url: str) -> str:
        url = self.extract_douyin_url(url)
        video_id = self.extract_video_id(url)
        if video_id:
            return f"https://www.douyin.com/video/{video_id}"
        if self.is_short_douyin_url(url):
            resolved_url = self.resolve_douyin_redirect(url)
            video_id = self.extract_video_id(resolved_url)
            if video_id:
                return f"https://www.douyin.com/video/{video_id}"
        return url

    def is_supported_video_url(self, url: str) -> bool:
        return self.extract_video_id(url) is not None

    def normalize_profile_url(self, url: str) -> str:
        url = self.extract_douyin_url(url)
        sec_uid = self.extract_profile_sec_uid(url)
        if sec_uid:
            return self.canonical_profile_url(sec_uid)
        if self.is_short_douyin_url(url):
            resolved_url = self.resolve_douyin_redirect(url)
            sec_uid = self.extract_profile_sec_uid(resolved_url)
            if sec_uid:
                return self.canonical_profile_url(sec_uid)
            return resolved_url
        return url

    def canonical_profile_url(self, sec_uid: str) -> str:
        return f"https://www.douyin.com/user/{sec_uid}"

    def extract_profile_sec_uid(self, url: str) -> str | None:
        url = self.extract_douyin_url(url)
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            path_parts = [part for part in parsed.path.split("/") if part]
        except Exception:
            return None

        if "douyin.com" not in host or len(path_parts) < 2:
            return None
        if path_parts[0].lower() != "user":
            return None
        if path_parts[1].lower() == "self":
            return None
        return path_parts[1]

    def reject_non_video_post(self, info: dict, *args, **kwargs) -> str | None:
        if kwargs.get("incomplete"):
            return None

        formats = info.get("formats") or []
        if not formats:
            return None if self.has_video_track(info) else "Skipped Douyin image/slideshow post"
        if any(self.has_video_track(format_info) for format_info in formats):
            return None
        return "Skipped Douyin image/slideshow post"

    def has_video_track(self, format_info: dict) -> bool:
        vcodec = format_info.get("vcodec")
        if vcodec and vcodec != "none":
            return True
        return vcodec is None and format_info.get("ext") in {"mp4", "mov", "webm", "mkv"}

    def to_user_error(self, exc: DownloadError) -> UserFacingDownloadError:
        message = str(exc)
        lower_message = message.lower()
        if "fresh cookies" in lower_message or "s_v_web_id" in lower_message:
            return UserFacingDownloadError("douyin_extract_failed")
        return super().to_user_error(exc)

    def is_truthy_flag(self, value: object) -> bool:
        return str(value).strip().lower() not in {"", "0", "false", "none"}
