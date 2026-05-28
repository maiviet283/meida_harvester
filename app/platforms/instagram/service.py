from __future__ import annotations

import json
import os
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from yt_dlp.cookies import extract_cookies_from_browser
from yt_dlp.utils import DownloadError

from app.platforms.common import BaseDownloadService, PlatformConfig, ProgressCallback, UserFacingDownloadError


INSTAGRAM_SPLIT_MP4_FORMATS = (
    "bv*[ext=mp4][width<=1920][height<=1920][vcodec^=avc1]+ba[ext=m4a]",
    "bv*[ext=mp4][width<=1920][height<=1920][vcodec!=none]+ba[ext=m4a]",
)
INSTAGRAM_COMBINED_MP4_FORMATS = (
    "b[ext=mp4][width<=1920][height<=1920][vcodec^=avc1][acodec^=mp4a]",
    "b[ext=mp4][width<=1920][height<=1920][vcodec!=none][acodec!=none]",
)
INSTAGRAM_FALLBACK_VIDEO_FORMATS = (
    "b[width<=1920][height<=1920][vcodec!=none][acodec!=none]",
    "b[vcodec!=none][acodec!=none]",
)
INSTAGRAM_FALLBACK_SPLIT_FORMATS = (
    "bv*[width<=1920][height<=1920][vcodec!=none]+ba",
    "bv*[vcodec!=none]+ba",
)
INSTAGRAM_FEED_PAGE_SIZE = 12
INSTAGRAM_MAX_FEED_PAGES = 120
INSTAGRAM_WEB_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.instagram.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "X-IG-App-ID": "936619743392459",
}
INSTAGRAM_MOBILE_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Instagram 219.0.0.12.117 Android",
    "X-IG-App-ID": "936619743392459",
}
INSTAGRAM_BROWSER_COOKIE_PATHS = (
    ("firefox", ("APPDATA", "Mozilla", "Firefox", "Profiles")),
    ("edge", ("LOCALAPPDATA", "Microsoft", "Edge", "User Data")),
    ("chrome", ("LOCALAPPDATA", "Google", "Chrome", "User Data")),
)
INSTAGRAM_VIDEO_PATHS = {"p", "reel", "reels", "tv"}


CONFIG = PlatformConfig(
    key="instagram",
    example_video_url="https://www.instagram.com/reel/ABC123/",
    example_page_url="https://www.instagram.com/creator/",
    supports_manual_cookies=True,
)


class InstagramService(BaseDownloadService):
    def download_urls(
        self,
        urls: list[str],
        folder: str,
        progress: ProgressCallback,
        single: bool,
        page_filter: str,
        emit_initial_progress: bool = True,
    ) -> None:
        self.use_browser_cookies = True
        self.browser_cookie_failed = False
        try:
            super().download_urls(urls, folder, progress, single, page_filter, emit_initial_progress)
        except UserFacingDownloadError as exc:
            if exc.status_key != "instagram_cookie_failed":
                raise
            self.browser_cookie_failed = True
            self.use_browser_cookies = False
            try:
                super().download_urls(urls, folder, progress, single, page_filter, emit_initial_progress)
            except UserFacingDownloadError as retry_exc:
                if retry_exc.status_key in {"instagram_restricted", "login_required"}:
                    raise UserFacingDownloadError("instagram_cookie_unavailable") from retry_exc
                raise
        finally:
            self.use_browser_cookies = True
            self.browser_cookie_failed = False

    def download_single(self, url: str, folder: str, progress: ProgressCallback) -> None:
        if not self.is_supported_video_url(url):
            raise UserFacingDownloadError("instagram_single_link")
        self.download(self.normalize_video_url(url), folder, progress, single=True, page_filter="all")

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
            raise UserFacingDownloadError("instagram_page_no_videos")
        self.raise_if_cancelled()
        self.download_urls(urls, folder, progress, single=False, page_filter="all", emit_initial_progress=False)

    def build_yt_dlp_options(self, folder: str, hook: Callable[[dict], None], single: bool) -> dict:
        has_ffmpeg = self.has_ffmpeg()
        formats = list(INSTAGRAM_COMBINED_MP4_FORMATS)
        if has_ffmpeg:
            formats = list(INSTAGRAM_SPLIT_MP4_FORMATS) + formats
        formats.extend(INSTAGRAM_FALLBACK_VIDEO_FORMATS)
        if has_ffmpeg:
            formats.extend(INSTAGRAM_FALLBACK_SPLIT_FORMATS)

        options = self.base_yt_dlp_options(folder, hook, single)
        options.update(
            {
                "format": "/".join(formats),
                "format_sort": ["quality", "res", "fps", "vcodec:h264", "acodec:aac"],
            }
        )
        if has_ffmpeg:
            options["merge_output_format"] = "mp4"
        if not single:
            options["ignoreerrors"] = True
            options["match_filter"] = self.reject_non_video_post
        if self.apply_manual_cookies(options, (".instagram.com",)):
            return options
        if getattr(self, "use_browser_cookies", True):
            cookies_from_browser = self.find_browser_cookies()
            if cookies_from_browser:
                options["cookiesfrombrowser"] = cookies_from_browser
        return options

    def to_user_error(self, exc: DownloadError) -> UserFacingDownloadError:
        message = str(exc)
        lower_message = message.lower()
        if "failed to decrypt with dpapi" in lower_message:
            return UserFacingDownloadError("instagram_cookie_failed")
        if "no video formats found" in lower_message:
            return UserFacingDownloadError("instagram_no_video")
        if "isn't available to everyone" in lower_message or "can't be seen by certain audiences" in lower_message:
            if getattr(self, "browser_cookie_failed", False):
                return UserFacingDownloadError("instagram_cookie_unavailable")
            return UserFacingDownloadError("instagram_restricted")
        if "unable to extract data" in lower_message and "instagram:user" in lower_message:
            return UserFacingDownloadError("instagram_profile_failed")
        return super().to_user_error(exc)

    def collect_profile_video_urls(self, url: str, progress: ProgressCallback | None = None) -> list[str]:
        self.raise_if_cancelled()
        username = self.extract_profile_username(url)
        if not username:
            raise UserFacingDownloadError("instagram_page_link")

        profile = self.fetch_json(
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={quote(username)}"
        )
        self.raise_if_cancelled()
        user = ((profile.get("data") or {}).get("user") or {})
        user_id = str(user.get("id") or user.get("pk") or "")
        if not user_id:
            raise UserFacingDownloadError("instagram_profile_failed")
        if user.get("is_private"):
            raise UserFacingDownloadError("instagram_private")

        urls: list[str] = []
        seen_shortcodes: set[str] = set()
        self.add_profile_edges(user, urls, seen_shortcodes)

        max_id: str | None = None
        seen_cursors: set[str] = set()
        for page in range(INSTAGRAM_MAX_FEED_PAGES):
            self.raise_if_cancelled()
            if progress:
                progress("reading", min(40, 18 + page), None)

            params = {"count": str(INSTAGRAM_FEED_PAGE_SIZE)}
            if max_id:
                params["max_id"] = max_id
            try:
                feed = self.fetch_json(
                    f"https://i.instagram.com/api/v1/feed/user/{quote(user_id)}/?{urlencode(params)}",
                    INSTAGRAM_MOBILE_HEADERS,
                )
            except UserFacingDownloadError:
                if urls:
                    break
                raise

            for item in feed.get("items") or []:
                self.raise_if_cancelled()
                self.add_feed_item(item, urls, seen_shortcodes)

            next_max_id = feed.get("next_max_id")
            if not feed.get("more_available") or not next_max_id:
                break
            if next_max_id in seen_cursors:
                break
            seen_cursors.add(str(next_max_id))
            max_id = str(next_max_id)

        return urls

    def fetch_json(self, url: str, headers: dict[str, str] | None = None) -> dict:
        self.raise_if_cancelled()
        active_headers = dict(headers or INSTAGRAM_WEB_HEADERS)
        manual_cookie_header = self.get_manual_cookie_header()
        if manual_cookie_header:
            active_headers["Cookie"] = manual_cookie_header
        try:
            response = self.open_json(url, active_headers)
            self.raise_if_cancelled()
            return response
        except HTTPError as exc:
            if exc.code not in {401, 403}:
                raise UserFacingDownloadError("instagram_profile_failed") from exc
            cookie_header = self.get_browser_cookie_header(url)
            if not cookie_header:
                raise UserFacingDownloadError("instagram_profile_failed") from exc
            try:
                response = self.open_json(url, {**active_headers, "Cookie": cookie_header})
                self.raise_if_cancelled()
                return response
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as retry_exc:
                raise UserFacingDownloadError("instagram_profile_failed") from retry_exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise UserFacingDownloadError("instagram_profile_failed") from exc

    def open_json(self, url: str, headers: dict[str, str]) -> dict:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_browser_cookie_header(self, url: str) -> str | None:
        if hasattr(self, "_browser_cookie_header"):
            return self._browser_cookie_header

        for browser in self.available_cookie_browsers():
            try:
                cookie_header = extract_cookies_from_browser(browser).get_cookie_header(url)
            except Exception as exc:
                print(f"[Instagram] browser cookie unavailable from {browser}: {exc}")
                continue
            if cookie_header:
                self._browser_cookie_header = cookie_header
                return cookie_header

        self._browser_cookie_header = None
        return None

    def available_cookie_browsers(self) -> list[str]:
        browsers: list[str] = []
        for browser, path_parts in INSTAGRAM_BROWSER_COOKIE_PATHS:
            env_name, *relative_parts = path_parts
            root = os.environ.get(env_name)
            if root and os.path.exists(os.path.join(root, *relative_parts)):
                browsers.append(browser)
        return browsers

    def find_browser_cookies(self) -> tuple[str, str | None, str | None, str | None] | None:
        browsers = self.available_cookie_browsers()
        if browsers:
            return (browsers[0], None, None, None)
        return None

    def add_profile_edges(self, user: dict, urls: list[str], seen_shortcodes: set[str]) -> None:
        for container_name in ("edge_felix_video_timeline", "edge_owner_to_timeline_media"):
            container = user.get(container_name) or {}
            for edge in container.get("edges") or []:
                node = edge.get("node") or {}
                if self.profile_node_has_video(node):
                    self.add_shortcode_url(node.get("shortcode"), urls, seen_shortcodes, node.get("product_type"))

    def profile_node_has_video(self, node: dict) -> bool:
        if node.get("is_video"):
            return True
        if node.get("__typename") == "GraphVideo":
            return True
        children = ((node.get("edge_sidecar_to_children") or {}).get("edges") or [])
        return any((edge.get("node") or {}).get("is_video") for edge in children)

    def add_feed_item(self, item: dict, urls: list[str], seen_shortcodes: set[str]) -> None:
        if self.feed_item_has_video(item):
            self.add_shortcode_url(item.get("code"), urls, seen_shortcodes, item.get("product_type"))

    def feed_item_has_video(self, item: dict) -> bool:
        if item.get("media_type") == 2:
            return True
        if item.get("video_versions"):
            return True
        if item.get("product_type") == "clips":
            return True
        return any((child.get("media_type") == 2 or child.get("video_versions")) for child in item.get("carousel_media") or [])

    def add_shortcode_url(
        self,
        shortcode: object,
        urls: list[str],
        seen_shortcodes: set[str],
        product_type: object = None,
    ) -> None:
        if not isinstance(shortcode, str):
            return
        shortcode_text = shortcode.strip()
        shortcode_key = shortcode_text.lower()
        if not shortcode_text or shortcode_key in seen_shortcodes:
            return
        seen_shortcodes.add(shortcode_key)
        path = "reel" if product_type == "clips" else "p"
        urls.append(f"https://www.instagram.com/{path}/{shortcode_text}/")

    def reject_non_video_post(self, info: dict, *args, **kwargs) -> str | None:
        if kwargs.get("incomplete"):
            return None

        formats = info.get("formats") or []
        if not formats:
            return None if self.has_video_track(info) else "Skipped Instagram non-video post"
        if any(self.has_video_track(format_info) for format_info in formats):
            return None
        return "Skipped Instagram non-video post"

    def has_video_track(self, format_info: dict) -> bool:
        vcodec = format_info.get("vcodec")
        if vcodec and vcodec != "none":
            return True
        return vcodec is None and format_info.get("ext") in {"mp4", "mov", "webm", "mkv"}

    def normalize_video_url(self, url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path_parts = [part for part in parsed.path.split("/") if part]
        if "instagram.com" not in host or len(path_parts) < 2:
            return url
        if path_parts[0].lower() not in INSTAGRAM_VIDEO_PATHS:
            return url

        kind = "reel" if path_parts[0].lower() == "reels" else path_parts[0].lower()
        return f"https://www.instagram.com/{kind}/{path_parts[1]}/"

    def extract_profile_username(self, url: str) -> str | None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path_parts = [part for part in parsed.path.split("/") if part]
        if "instagram.com" not in host or not path_parts:
            return None
        if path_parts[0].lower() in INSTAGRAM_VIDEO_PATHS:
            return None
        return path_parts[0]

    def is_supported_video_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            path_parts = [part for part in parsed.path.split("/") if part]
        except Exception:
            return False

        if "instagram.com" not in host or len(path_parts) < 2:
            return False
        return path_parts[0].lower() in INSTAGRAM_VIDEO_PATHS
