from __future__ import annotations

import html as html_lib
import json
import os
from pathlib import Path
import re
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from yt_dlp.cookies import extract_cookies_from_browser
from yt_dlp.utils import DownloadError

from app.platforms.common import BaseDownloadService, PlatformConfig, ProgressCallback, UserFacingDownloadError


FACEBOOK_COMBINED_FORMAT = "b[ext=mp4]/b"
FACEBOOK_SPLIT_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/b"
FACEBOOK_MAX_SCAN_PAGES = 40
FACEBOOK_MAX_REELS_GRAPHQL_PAGES = 40
FACEBOOK_GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
FACEBOOK_REELS_PAGINATION_FRIENDLY_NAME = "ProfileCometAppCollectionReelsRendererPaginationQuery"
FACEBOOK_REELS_PAGINATION_DOC_ID = "26962700580026484"
FACEBOOK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}
FACEBOOK_BROWSER_COOKIE_PATHS = (
    ("firefox", ("APPDATA", "Mozilla", "Firefox", "Profiles")),
    ("edge", ("LOCALAPPDATA", "Microsoft", "Edge", "User Data")),
    ("chrome", ("LOCALAPPDATA", "Google", "Chrome", "User Data")),
)
FACEBOOK_REELS_PAGE_INFO_RE = re.compile(
    r'"aggregated_fb_shorts"\s*:\s*\{.*?"page_info"\s*:\s*\{\s*'
    r'"end_cursor"\s*:\s*"(?P<cursor>[^"]+)"\s*,\s*'
    r'"has_next_page"\s*:\s*(?P<has_next>true|false)\s*\}\s*\}\s*,\s*'
    r'"id"\s*:\s*"(?P<collection_id>[^"]+)"',
    re.DOTALL,
)


CONFIG = PlatformConfig(
    key="facebook",
    example_video_url="https://www.facebook.com/watch/?v=123456789",
    example_page_url="https://www.facebook.com/example.page",
    supports_manual_cookies=True,
)


class FacebookService(BaseDownloadService):
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
            if exc.status_key != "facebook_cookie_failed":
                raise
            self.browser_cookie_failed = True
            self.use_browser_cookies = False
            try:
                super().download_urls(urls, folder, progress, single, page_filter, emit_initial_progress)
            except UserFacingDownloadError as retry_exc:
                if retry_exc.status_key in {"facebook_parse_failed", "login_required"}:
                    raise UserFacingDownloadError("facebook_cookie_unavailable") from retry_exc
                raise
        finally:
            self.use_browser_cookies = True
            self.browser_cookie_failed = False

    def download(
        self,
        url: str,
        folder: str,
        progress: ProgressCallback,
        single: bool,
        page_filter: str,
    ) -> None:
        self.use_browser_cookies = True
        self.browser_cookie_failed = False
        try:
            super().download(url, folder, progress, single, page_filter)
        except UserFacingDownloadError as exc:
            if exc.status_key != "facebook_cookie_failed":
                raise
            self.browser_cookie_failed = True
            self.use_browser_cookies = False
            try:
                super().download(url, folder, progress, single, page_filter)
            except UserFacingDownloadError as retry_exc:
                if retry_exc.status_key in {"facebook_parse_failed", "login_required"}:
                    raise UserFacingDownloadError("facebook_cookie_unavailable") from retry_exc
                raise
        finally:
            self.use_browser_cookies = True
            self.browser_cookie_failed = False

    def build_yt_dlp_options(self, folder: str, hook: Callable[[dict], None], single: bool) -> dict:
        has_ffmpeg = self.has_ffmpeg()
        options = self.base_yt_dlp_options(folder, hook, single)
        options.update(
            {
                "format": FACEBOOK_SPLIT_FORMAT if has_ffmpeg else FACEBOOK_COMBINED_FORMAT,
                "http_headers": {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            }
        )
        if has_ffmpeg:
            options["merge_output_format"] = "mp4"
        if self.apply_manual_cookies(options, (".facebook.com", ".fb.watch")):
            return options
        if getattr(self, "use_browser_cookies", True):
            cookies_from_browser = self.find_browser_cookies()
            if cookies_from_browser:
                options["cookiesfrombrowser"] = cookies_from_browser
        return options

    def download_single(self, url: str, folder: str, progress: ProgressCallback) -> None:
        if not self.is_supported_video_url(url):
            raise UserFacingDownloadError("facebook_single_link")
        url = self.normalize_video_url(url)
        super().download_single(url, folder, progress)

    def download_page(
        self,
        url: str,
        folder: str,
        progress: ProgressCallback,
        page_filter: str = "all",
    ) -> None:
        normalized = url.lower()
        if "/people/" in normalized:
            raise UserFacingDownloadError("facebook_people_page")
        if self.is_supported_video_url(url):
            super().download_page(url, folder, progress, "all")
            return

        progress("reading", 18, None)
        urls = self.collect_page_video_urls(url, progress)
        urls = [u for u in urls if self.is_supported_video_url(u)]
        if not urls:
            raise UserFacingDownloadError("facebook_page_no_videos")
        self.download_urls(urls, folder, progress, single=False, page_filter="all", emit_initial_progress=False)

    def to_user_error(self, exc: DownloadError) -> UserFacingDownloadError:
        message = str(exc)
        lower_message = message.lower()
        if "failed to decrypt with dpapi" in lower_message:
            return UserFacingDownloadError("facebook_cookie_failed")
        if "cannot parse data" in lower_message:
            if getattr(self, "browser_cookie_failed", False):
                return UserFacingDownloadError("facebook_cookie_unavailable")
            return UserFacingDownloadError("facebook_parse_failed")
        return super().to_user_error(exc)

    def normalize_video_url(self, url: str) -> str:
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) >= 3 and path_parts[0] == "share":
            share_type = path_parts[1]
            shared_id = path_parts[2]
            if shared_id.isdigit() and share_type == "r":
                return f"https://www.facebook.com/reel/{shared_id}/"
            if shared_id.isdigit() and share_type == "v":
                return f"https://www.facebook.com/watch/?v={shared_id}"
        return url

    def is_supported_video_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            path_parts = [part.lower() for part in parsed.path.split("/") if part]
            query = parse_qs(parsed.query)
        except Exception:
            return False

        if "fb.watch" in host:
            return bool(parsed.path.strip("/"))
        if "facebook.com" not in host or not path_parts:
            return False
        if path_parts[0] == "watch":
            return "v" in query or "video_id" in query
        if path_parts[0] in {"video.php", "video"}:
            return "v" in query or "video_id" in query
        if path_parts[0] == "reel":
            return len(path_parts) >= 2 and path_parts[1].isdigit()
        if path_parts[0] == "share" and len(path_parts) >= 3:
            return path_parts[1] in {"v", "r"}
        if path_parts[0] in {"story.php", "permalink.php", "photo.php"}:
            return bool({"story_fbid", "id", "fbid"} & set(query))
        if "videos" in path_parts:
            videos_index = path_parts.index("videos")
            return len(path_parts) > videos_index + 1 and path_parts[videos_index + 1] != "all"
        if "posts" in path_parts:
            posts_index = path_parts.index("posts")
            return len(path_parts) > posts_index + 1
        if "permalink" in path_parts:
            permalink_index = path_parts.index("permalink")
            return len(path_parts) > permalink_index + 1
        if path_parts[0] == "events":
            return len(path_parts) >= 2
        return False

    def collect_page_video_urls(self, url: str, progress: ProgressCallback | None = None) -> list[str]:
        scan_urls = self.build_page_scan_urls(url)
        if not scan_urls:
            raise UserFacingDownloadError("facebook_page_link")

        urls: list[str] = []
        seen_video_keys: set[str] = set()
        seen_scan_urls: set[str] = set()
        seen_reels_page_states: set[tuple[str, str]] = set()
        queue = list(scan_urls)
        scan_count = 0
        had_readable_page = False

        while queue and scan_count < FACEBOOK_MAX_SCAN_PAGES:
            self.raise_if_cancelled()
            scan_url = queue.pop(0)
            if scan_url in seen_scan_urls:
                continue
            seen_scan_urls.add(scan_url)
            scan_count += 1

            if progress:
                progress("reading", min(45, 18 + scan_count), None)

            try:
                page_html = self.fetch_page_html(scan_url)
            except UserFacingDownloadError:
                continue
            had_readable_page = True
            decoded_html = self.decode_facebook_html(page_html)
            self.add_video_urls_from_html(decoded_html, scan_url, urls, seen_video_keys)
            self.collect_reels_graphql_video_urls(
                decoded_html,
                scan_url,
                urls,
                seen_video_keys,
                seen_reels_page_states,
                progress,
            )

            for next_url in self.extract_next_scan_urls(decoded_html, scan_url):
                if next_url not in seen_scan_urls and next_url not in queue:
                    queue.append(next_url)

        if not urls and not had_readable_page:
            raise UserFacingDownloadError("facebook_page_failed")
        return urls

    def build_page_scan_urls(self, url: str) -> list[str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path_parts = [part for part in parsed.path.split("/") if part]
        if "facebook.com" not in host or not path_parts:
            return []

        first_part = path_parts[0].lower()

        if first_part == "profile.php":
            profile_id = parse_qs(parsed.query).get("id", [""])[0]
            if not profile_id.isdigit():
                return []
            return [
                f"https://www.facebook.com/profile.php?id={profile_id}&sk=reels_tab",
                f"https://m.facebook.com/profile.php?id={profile_id}&sk=reels_tab",
                f"https://mbasic.facebook.com/profile.php?id={profile_id}&sk=reels_tab",
                f"https://mbasic.facebook.com/profile.php?id={profile_id}&sk=videos",
                f"https://www.facebook.com/profile.php?id={profile_id}&sk=videos",
                f"https://m.facebook.com/profile.php?id={profile_id}&sk=videos",
            ]

        if first_part in {
            "watch",
            "video.php",
            "story.php",
            "permalink.php",
            "photo.php",
            "reel",
            "share",
            "groups",
            "events",
            "people",
        }:
            return []

        page_slug = path_parts[0]
        return [
            f"https://mbasic.facebook.com/{page_slug}/videos",
            f"https://mbasic.facebook.com/{page_slug}/reels",
            f"https://www.facebook.com/{page_slug}/videos",
            f"https://www.facebook.com/{page_slug}/reels",
            f"https://m.facebook.com/{page_slug}/videos",
            f"https://m.facebook.com/{page_slug}/reels",
            f"https://www.facebook.com/{page_slug}/",
        ]

    def fetch_page_html(self, url: str) -> str:
        headers = dict(FACEBOOK_HEADERS)
        cookie_header = self.get_manual_cookie_header() or self.get_browser_cookie_header(url)
        if cookie_header:
            headers["Cookie"] = cookie_header
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=25) as response:
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise UserFacingDownloadError("facebook_page_failed") from exc

    def collect_reels_graphql_video_urls(
        self,
        page_html: str,
        referer: str,
        urls: list[str],
        seen_video_keys: set[str],
        seen_page_states: set[tuple[str, str]],
        progress: ProgressCallback | None = None,
    ) -> None:
        page_states = self.extract_reels_pagination_states(page_html)
        if not page_states:
            return

        metadata = self.extract_graphql_metadata(page_html)
        doc_id = self.get_reels_pagination_doc_id(page_html, referer)
        pages_read = 0
        queue = list(page_states)

        while queue and pages_read < FACEBOOK_MAX_REELS_GRAPHQL_PAGES:
            self.raise_if_cancelled()
            cursor, collection_id = queue.pop(0)
            state_key = (collection_id, cursor)
            if state_key in seen_page_states:
                continue
            seen_page_states.add(state_key)

            if progress:
                progress("reading", min(85, 45 + pages_read), None)

            try:
                response_html = self.fetch_reels_graphql_page(collection_id, cursor, metadata, doc_id, referer)
            except UserFacingDownloadError:
                return

            decoded_response = self.decode_facebook_html(response_html)
            self.add_video_urls_from_html(decoded_response, referer, urls, seen_video_keys)
            pages_read += 1

            for next_cursor, next_collection_id in self.extract_reels_pagination_states(decoded_response):
                next_state = (next_cursor, next_collection_id)
                if (next_collection_id, next_cursor) not in seen_page_states and next_state not in queue:
                    queue.append(next_state)

    def extract_reels_pagination_states(self, page_html: str) -> list[tuple[str, str]]:
        states: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for match in FACEBOOK_REELS_PAGE_INFO_RE.finditer(page_html):
            if match.group("has_next") != "true":
                continue
            state = (match.group("cursor"), match.group("collection_id"))
            if state in seen:
                continue
            seen.add(state)
            states.append(state)
        return states

    def extract_graphql_metadata(self, page_html: str) -> dict[str, str]:
        patterns = {
            "lsd": r'"LSD",\[\],\{"token":"([^"]*)"',
            "fb_dtsg": r'"DTSGInitialData",\[\],\{"token":"([^"]*)"',
            "__spin_r": r'"__spin_r":(\d+)',
            "__spin_t": r'"__spin_t":(\d+)',
            "__hsi": r'"hsi":"([^"]+)"',
        }
        metadata: dict[str, str] = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, page_html)
            if match and match.group(1):
                metadata[key] = match.group(1)
        return metadata

    def get_reels_pagination_doc_id(self, page_html: str, base_url: str) -> str:
        cached_doc_id = getattr(self, "_reels_pagination_doc_id", "")
        if cached_doc_id:
            return cached_doc_id

        doc_id = self.extract_reels_pagination_doc_id(page_html)
        if not doc_id:
            for script_url in self.extract_script_urls(page_html, base_url):
                try:
                    script = self.fetch_static_resource(script_url)
                except UserFacingDownloadError:
                    continue
                doc_id = self.extract_reels_pagination_doc_id(script)
                if doc_id:
                    break

        self._reels_pagination_doc_id = doc_id or FACEBOOK_REELS_PAGINATION_DOC_ID
        return self._reels_pagination_doc_id

    def extract_reels_pagination_doc_id(self, text: str) -> str | None:
        pattern = (
            rf'{FACEBOOK_REELS_PAGINATION_FRIENDLY_NAME}_facebookRelayOperation"'
            r',\[\],\(function\([^)]*\)\{[^}]*exports="(\d+)"'
        )
        match = re.search(pattern, text)
        if match:
            return match.group(1)
        return None

    def extract_script_urls(self, page_html: str, base_url: str) -> list[str]:
        urls: list[str] = []
        for src in re.findall(r'''(?i)<script[^>]+\bsrc=["']([^"']+)["']''', page_html):
            script_url = urljoin(base_url, src)
            if not script_url.startswith("http") or script_url in urls:
                continue
            urls.append(script_url)
        return urls

    def fetch_static_resource(self, url: str) -> str:
        headers = {
            "User-Agent": FACEBOOK_HEADERS["User-Agent"],
            "Accept": "*/*",
            "Accept-Language": FACEBOOK_HEADERS["Accept-Language"],
        }
        try:
            with urlopen(Request(url, headers=headers), timeout=25) as response:
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise UserFacingDownloadError("facebook_page_failed") from exc

    def fetch_reels_graphql_page(
        self,
        collection_id: str,
        cursor: str,
        metadata: dict[str, str],
        doc_id: str,
        referer: str,
    ) -> str:
        variables = {
            "count": 10,
            "cursor": cursor,
            "id": collection_id,
            "renderLocation": "timeline",
            "scale": 1,
            "useDefaultActor": True,
        }
        payload = {
            "__a": "1",
            "__comet_req": "15",
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": FACEBOOK_REELS_PAGINATION_FRIENDLY_NAME,
            "variables": json.dumps(variables, separators=(",", ":")),
            "server_timestamps": "true",
            "doc_id": doc_id,
        }
        if metadata.get("lsd"):
            lsd = metadata["lsd"]
            payload["lsd"] = lsd
            payload["jazoest"] = f"2{sum(ord(char) for char in lsd)}"
        if metadata.get("fb_dtsg"):
            payload["fb_dtsg"] = metadata["fb_dtsg"]
        for key in ("__spin_r", "__spin_t", "__hsi"):
            if metadata.get(key):
                payload[key] = metadata[key]

        headers = dict(FACEBOOK_HEADERS)
        headers.update(
            {
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://www.facebook.com",
                "Referer": referer,
                "X-FB-Friendly-Name": FACEBOOK_REELS_PAGINATION_FRIENDLY_NAME,
            }
        )
        if metadata.get("lsd"):
            headers["X-FB-LSD"] = metadata["lsd"]
        cookie_header = self.get_manual_cookie_header() or self.get_browser_cookie_header(referer)
        if cookie_header:
            headers["Cookie"] = cookie_header

        try:
            request = Request(
                FACEBOOK_GRAPHQL_URL,
                data=urlencode(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise UserFacingDownloadError("facebook_page_failed") from exc

    def get_browser_cookie_header(self, url: str) -> str | None:
        if hasattr(self, "_browser_cookie_header"):
            return self._browser_cookie_header

        cookies_from_browser = self.find_browser_cookies()
        if not cookies_from_browser:
            self._browser_cookie_header = None
            return None

        browser = cookies_from_browser[0]
        try:
            cookie_header = extract_cookies_from_browser(browser).get_cookie_header(url)
        except Exception as exc:
            print(f"[Facebook] browser cookie unavailable from {browser}: {exc}")
        else:
            if cookie_header:
                self._browser_cookie_header = cookie_header
                return cookie_header

        self._browser_cookie_header = None
        return None

    def decode_facebook_html(self, page_html: str) -> str:
        return html_lib.unescape(page_html).replace("\\/", "/")

    def add_video_urls_from_html(
        self,
        page_html: str,
        base_url: str,
        urls: list[str],
        seen_video_keys: set[str],
    ) -> None:
        for href in re.findall(r'''(?i)\bhref=["']([^"']+)["']''', page_html):
            self.add_video_url(urljoin(base_url, href), urls, seen_video_keys)
        for direct_url in re.findall(r'''(?i)https?://(?:www\.|m\.)?facebook\.com/[^\s"'<>]+''', page_html):
            self.add_video_url(direct_url, urls, seen_video_keys)
        for video_id in re.findall(r'''(?i)["'](?:video_id|videoid|videoID)["']\s*[:=]\s*["']?(\d{5,})''', page_html):
            self.add_watch_url(video_id, urls, seen_video_keys)
        for video_id in re.findall(r'''(?i)(?:watch/\?v=|video\.php\?v=)(\d{5,})''', page_html):
            self.add_watch_url(video_id, urls, seen_video_keys)
        for video_id in re.findall(r'''(?i)/videos/(?:[^/?#]+/)?(\d{5,})''', page_html):
            self.add_watch_url(video_id, urls, seen_video_keys)
        for reel_id in re.findall(r'''(?i)/reel/(\d{5,})''', page_html):
            self.add_reel_url(reel_id, urls, seen_video_keys)

    def add_video_url(self, raw_url: str, urls: list[str], seen_video_keys: set[str]) -> None:
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").lower()
        path_parts = [part for part in parsed.path.split("/") if part]
        if "facebook.com" not in host or not path_parts:
            return

        query = parse_qs(parsed.query)
        if path_parts[0].lower() == "watch" and query.get("v"):
            self.add_watch_url(query["v"][0], urls, seen_video_keys)
            return
        if path_parts[0].lower() in {"video.php", "video"} and query.get("v"):
            self.add_watch_url(query["v"][0], urls, seen_video_keys)
            return
        if path_parts[0].lower() == "reel" and len(path_parts) >= 2:
            self.add_reel_url(path_parts[1], urls, seen_video_keys)
            return
        if "videos" in [part.lower() for part in path_parts]:
            lower_parts = [part.lower() for part in path_parts]
            videos_index = lower_parts.index("videos")
            candidates = path_parts[videos_index + 1 : videos_index + 3]
            for candidate in candidates:
                if candidate.isdigit():
                    self.add_watch_url(candidate, urls, seen_video_keys)
                    break

    def add_watch_url(self, video_id: str, urls: list[str], seen_video_keys: set[str]) -> None:
        if not video_id.isdigit():
            return
        if video_id in seen_video_keys:
            return
        seen_video_keys.add(video_id)
        urls.append(f"https://www.facebook.com/watch/?v={video_id}")

    def add_reel_url(self, reel_id: str, urls: list[str], seen_video_keys: set[str]) -> None:
        if not reel_id.isdigit():
            return
        if reel_id in seen_video_keys:
            return
        seen_video_keys.add(reel_id)
        urls.append(f"https://www.facebook.com/reel/{reel_id}/")

    def extract_next_scan_urls(self, page_html: str, base_url: str) -> list[str]:
        next_urls: list[str] = []
        for href in re.findall(r'''(?i)\bhref=["']([^"']+)["']''', page_html):
            resolved = urljoin(base_url, href)
            parsed = urlparse(resolved)
            if "facebook.com" not in (parsed.hostname or "").lower():
                continue
            lowered = resolved.lower()
            is_slug_video_page = "/videos" in lowered or "/reels" in lowered
            is_profile_video_page = "profile.php" in lowered and (
                "sk=videos" in lowered or "sk=reels" in lowered
            )
            if not is_slug_video_page and not is_profile_video_page:
                continue
            if not any(token in lowered for token in ("cursor", "after", "start=", "pagelet", "more", "sk=videos", "end_cursor")):
                continue
            next_urls.append(resolved)
        return next_urls

    def find_browser_cookies(self) -> tuple[str, str | None, str | None, str | None] | None:
        for browser, path_parts in FACEBOOK_BROWSER_COOKIE_PATHS:
            env_name, *relative_parts = path_parts
            root = os.environ.get(env_name)
            if root and (Path(root).joinpath(*relative_parts)).exists():
                return (browser, None, None, None)
        return None
