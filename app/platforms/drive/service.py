from __future__ import annotations

import http.client
import http.cookiejar
import html as html_lib
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from yt_dlp.utils import DownloadCancelled

from app.platforms.common import BaseDownloadService, PlatformConfig, ProgressCallback, UserFacingDownloadError


CONFIG = PlatformConfig(
    key="drive",
    example_video_url="https://drive.google.com/file/d/FILE_ID/view",
    example_page_url="https://drive.google.com/drive/folders/FOLDER_ID",
    supports_manual_cookies=True,
)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_GOOGLE_ORIGIN = "https://drive.google.com"
_VIDEO_INFO_URL = "https://drive.google.com/u/0/get_video_info?docid={file_id}&drive_originator_app=303"
_TITLE_SUFFIX_RE = re.compile(r"\s*\(DRIVE\s+[\d.]+\)\s*$", re.IGNORECASE)
_TITLE_EXT_RE = re.compile(r"\.(mp4|mkv|mov|webm|avi|m4v|wmv)$", re.IGNORECASE)
_MIME_CODECS_RE = re.compile(r'codecs="([^"]+)"')
_CHUNK_SIZE = 10 << 20
_MAX_URL_REFRESH = 5


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("source_address", ("0.0.0.0", 0))
        super().__init__(*args, **kwargs)


class _IPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_IPv4HTTPSConnection, req, context=self._context)


class DriveDownloadService(BaseDownloadService):
    def download_single(self, url: str, folder: str, progress: ProgressCallback) -> None:
        file_url = self._normalize_file_url(url)
        if not file_url:
            raise UserFacingDownloadError("drive_single_link")
        progress("preparing", 8, None)
        progress("reading", 18, None)
        self._download_one(file_url, folder, progress)
        progress("finished", 100, None)

    def download_page(
        self,
        url: str,
        folder: str,
        progress: ProgressCallback,
        page_filter: str = "all",
    ) -> None:
        folder_url = self._normalize_folder_url(url)
        if not folder_url:
            raise UserFacingDownloadError("drive_folder_link")
        progress("preparing", 5, None)
        opener = self._build_opener()
        progress("reading", 10, None)
        file_urls = self._list_folder(folder_url, opener)
        if not file_urls:
            raise UserFacingDownloadError("drive_no_videos")

        total = len(file_urls)
        succeeded = 0
        for i, file_url in enumerate(file_urls):
            self.raise_if_cancelled()

            def _sub_progress(key: str, pct: int, data: object, _i: int = i, _t: int = total) -> None:
                mapped_key = "processing" if key == "finished" and _i + 1 < _t else key
                progress(mapped_key, 10 + int((_i + pct / 100) * 85 / _t), data)

            try:
                self._download_one(file_url, folder, _sub_progress)
                succeeded += 1
            except UserFacingDownloadError as exc:
                if exc.status_key == "download_cancelled":
                    raise

        if succeeded == 0:
            raise UserFacingDownloadError("drive_all_failed")
        progress("finished", 100, None)

    def clean_input_url(self, url: str, mode: str = "") -> str:
        url = url.strip()
        try:
            if mode == "single":
                file_url = self._normalize_file_url(url)
                if file_url:
                    return file_url
            if mode == "page":
                folder_url = self._normalize_folder_url(url)
                if folder_url:
                    return folder_url
        except Exception:
            pass
        return url

    def _download_one(
        self,
        file_url: str,
        dest_folder: str,
        progress: ProgressCallback,
    ) -> None:
        file_id = self._extract_file_id(file_url)
        if not file_id:
            raise UserFacingDownloadError("drive_single_link")
        self.raise_if_cancelled()
        progress("reading", 22, None)
        info = self._fetch_video_metadata(file_id, file_url)
        self._download_streams(info, dest_folder, progress)

    def _download_streams(self, info: dict, dest_folder: str, progress: ProgressCallback) -> None:
        progressive, video, audio = self._select_formats(info["formats"])
        target_dir = Path(dest_folder) / "GoogleDrive"
        target_dir.mkdir(parents=True, exist_ok=True)
        basename = self._build_output_basename(info["title"], info["id"])

        if progressive is not None:
            self._download_to_file(info["id"], progressive, target_dir / f"{basename}.{progressive['ext']}", progress, 25, 100)
            return

        if video is not None and audio is not None and self.has_ffmpeg():
            video_path = target_dir / f"{basename}.video.{video['ext']}"
            audio_path = target_dir / f"{basename}.audio.{audio['ext']}"
            try:
                self._download_to_file(info["id"], video, video_path, progress, 25, 68)
                self._download_to_file(info["id"], audio, audio_path, progress, 68, 90)
                progress("processing", 93, None)
                self._merge_streams(video_path, audio_path, target_dir / f"{basename}.mp4")
            finally:
                video_path.unlink(missing_ok=True)
                audio_path.unlink(missing_ok=True)
            return

        if video is not None:
            self._download_to_file(info["id"], video, target_dir / f"{basename}.{video['ext']}", progress, 25, 100)
            return

        raise UserFacingDownloadError("drive_no_video")

    def _download_to_file(
        self,
        file_id: str,
        fmt: dict,
        path: Path,
        progress: ProgressCallback,
        start_pct: int,
        end_pct: int,
    ) -> None:
        self.raise_if_cancelled()
        opener = urllib.request.build_opener(_IPv4HTTPSHandler())
        span = max(1, end_pct - start_pct)
        url = fmt["url"]
        format_id = fmt.get("format_id")
        position = 0
        total: int | None = None
        refreshes = 0
        try:
            with open(path, "wb") as output:
                while True:
                    self.raise_if_cancelled()
                    headers = {
                        "User-Agent": _UA,
                        "Referer": f"{_GOOGLE_ORIGIN}/",
                        "Range": f"bytes={position}-{position + _CHUNK_SIZE - 1}",
                    }
                    request = urllib.request.Request(url, headers=headers)
                    try:
                        with opener.open(request, timeout=60) as response:
                            if total is None:
                                total = self._content_range_total(response.headers.get("Content-Range"))
                            data = response.read()
                    except urllib.error.HTTPError as exc:
                        if exc.code not in (401, 403, 410) or refreshes >= _MAX_URL_REFRESH:
                            raise
                        fresh_url = self._refresh_stream_url(file_id, format_id)
                        if not fresh_url:
                            raise
                        url = fresh_url
                        refreshes += 1
                        continue
                    if not data:
                        break
                    output.write(data)
                    position += len(data)
                    ratio = position / total if total else 0.5
                    progress("downloading", start_pct + int(min(1.0, ratio) * span), None)
                    if (total and position >= total) or len(data) < _CHUNK_SIZE:
                        break
        except DownloadCancelled:
            path.unlink(missing_ok=True)
            raise UserFacingDownloadError("download_cancelled")
        except urllib.error.HTTPError as exc:
            path.unlink(missing_ok=True)
            if exc.code in (401, 403, 410):
                raise UserFacingDownloadError("drive_permission_denied") from exc
            raise UserFacingDownloadError("drive_no_video") from exc
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise UserFacingDownloadError("download_failed", {"error": str(exc)}) from exc

    def _refresh_stream_url(self, file_id: str, format_id: str | None) -> str | None:
        if not format_id:
            return None
        try:
            info = self._fetch_video_metadata(file_id, self._file_url(file_id))
        except UserFacingDownloadError:
            return None
        for fmt in info["formats"]:
            if fmt.get("format_id") == format_id:
                return fmt.get("url")
        return None

    @staticmethod
    def _content_range_total(content_range: str | None) -> int | None:
        if content_range and "/" in content_range:
            return DriveDownloadService._int_or_none(content_range.rsplit("/", 1)[1])
        return None

    def _merge_streams(self, video_path: Path, audio_path: Path, output_path: Path) -> None:
        location = self.find_bundled_ffmpeg_location()
        ffmpeg = "ffmpeg"
        if location:
            for candidate in (Path(location) / "ffmpeg.exe", Path(location) / "ffmpeg"):
                if candidate.is_file():
                    ffmpeg = str(candidate)
                    break
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        result = subprocess.run(
            [ffmpeg, "-y", "-i", str(video_path), "-i", str(audio_path),
             "-c", "copy", "-movflags", "+faststart", str(output_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags,
        )
        if result.returncode != 0 or not output_path.is_file():
            output_path.unlink(missing_ok=True)
            raise UserFacingDownloadError("drive_no_video")

    @staticmethod
    def _select_formats(formats: list[dict]) -> tuple[dict | None, dict | None, dict | None]:
        def height(fmt: dict) -> int:
            return DriveDownloadService._int_or_none(fmt.get("height")) or 0

        def tbr(fmt: dict) -> int:
            return DriveDownloadService._int_or_none(fmt.get("tbr")) or 0

        progressive = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")]
        video_only = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none")]
        audio_only = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
        best_progressive = max(progressive, key=lambda f: (height(f), tbr(f)), default=None)
        best_video = max(video_only, key=lambda f: (height(f), tbr(f)), default=None)
        best_audio = max(audio_only, key=tbr, default=None)
        return best_progressive, best_video, best_audio

    @staticmethod
    def _build_output_basename(title: str, file_id: str) -> str:
        safe_title = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", title).strip()[:90].strip()
        return f"{safe_title or file_id} [{file_id}]"

    def _fetch_video_metadata(self, file_id: str, file_url: str) -> dict:
        info = urllib.parse.parse_qs(self._fetch_video_info(file_id))
        status = (info.get("status") or [""])[0]
        if status != "ok":
            raise self._video_info_status_error((info.get("reason") or [""])[0])

        formats = self._extract_playback_formats(info)
        if not formats:
            raise UserFacingDownloadError("drive_no_video")

        title = self._clean_video_title((info.get("title") or [""])[0]) or file_id
        return {
            "id": file_id,
            "title": title,
            "duration": self._int_or_none((info.get("length_seconds") or [None])[0]),
            "webpage_url": file_url,
            "extractor": "GoogleDrive",
            "extractor_key": "GoogleDrive",
            "formats": formats,
        }

    def _fetch_video_info(self, file_id: str) -> str:
        url = _VIDEO_INFO_URL.format(file_id=urllib.parse.quote(file_id, safe=""))
        request = urllib.request.Request(url, headers=self._build_google_request_headers(accept="text/plain,*/*"))
        opener = urllib.request.build_opener(_IPv4HTTPSHandler())
        try:
            with opener.open(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise UserFacingDownloadError("drive_permission_denied") from exc
            raise UserFacingDownloadError("drive_no_video") from exc
        except Exception as exc:
            raise UserFacingDownloadError("drive_no_video") from exc

    @staticmethod
    def _video_info_status_error(reason: str) -> UserFacingDownloadError:
        lower_reason = (reason or "").lower()
        if "too many" in lower_reason or "quota" in lower_reason or "limit" in lower_reason:
            return UserFacingDownloadError("drive_quota_exceeded")
        if "permission" in lower_reason or "private" in lower_reason or "cannot" in lower_reason or "can't" in lower_reason:
            return UserFacingDownloadError("drive_permission_denied")
        return UserFacingDownloadError("drive_no_video")

    @staticmethod
    def _extract_playback_formats(info: dict) -> list[dict]:
        formats: list[dict] = []
        player_response = (info.get("player_response") or [None])[0]
        if player_response:
            try:
                streaming = json.loads(player_response).get("streamingData") or {}
            except (TypeError, ValueError):
                streaming = {}
            for item in streaming.get("formats") or []:
                DriveDownloadService._append_player_format(formats, item, progressive=True)
            for item in streaming.get("adaptiveFormats") or []:
                DriveDownloadService._append_player_format(formats, item, progressive=False)
        if not formats:
            formats = DriveDownloadService._formats_from_stream_map(info)
        return formats

    @staticmethod
    def _append_player_format(formats: list[dict], item: dict, progressive: bool) -> None:
        media_url = item.get("url")
        if not media_url:
            return
        mime_type = item.get("mimeType") or ""
        vcodec, acodec = DriveDownloadService._codecs_from_mime(mime_type, progressive)
        bitrate = DriveDownloadService._int_or_none(item.get("bitrate"))
        format_info = {
            "url": media_url,
            "format_id": str(item.get("itag") or len(formats)),
            "ext": DriveDownloadService._extension_from_mime(mime_type),
            "width": DriveDownloadService._int_or_none(item.get("width")),
            "height": DriveDownloadService._int_or_none(item.get("height")),
            "fps": DriveDownloadService._int_or_none(item.get("fps")),
            "filesize": DriveDownloadService._int_or_none(item.get("contentLength")),
            "tbr": round(bitrate / 1000) if bitrate else None,
            "vcodec": vcodec,
            "acodec": acodec,
        }
        formats.append({key: value for key, value in format_info.items() if value is not None})

    @staticmethod
    def _formats_from_stream_map(info: dict) -> list[dict]:
        raw = (info.get("url_encoded_fmt_stream_map") or info.get("fmt_stream_map") or [""])[0]
        formats: list[dict] = []
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if entry.startswith("http") and "|" in entry:
                itag, media_url = entry.split("|", 1)
                fields = {"itag": itag, "url": media_url}
            else:
                fields = {name: values[0] for name, values in urllib.parse.parse_qs(entry).items()}
            media_url = fields.get("url")
            if not media_url:
                continue
            mime_type = fields.get("type") or ""
            vcodec, acodec = DriveDownloadService._codecs_from_mime(mime_type, progressive=True)
            format_info = {
                "url": media_url,
                "format_id": str(fields.get("itag") or len(formats)),
                "ext": DriveDownloadService._extension_from_mime(mime_type) if mime_type else "mp4",
                "vcodec": vcodec,
                "acodec": acodec,
            }
            formats.append({key: value for key, value in format_info.items() if value is not None})
        return formats

    @staticmethod
    def _codecs_from_mime(mime_type: str, progressive: bool) -> tuple[str, str]:
        match = _MIME_CODECS_RE.search(mime_type)
        codec_list = [codec.strip() for codec in match.group(1).split(",")] if match else []
        if mime_type.split("/", 1)[0].strip().lower() == "audio":
            return "none", (codec_list[0] if codec_list else "mp4a.40.2")
        vcodec = codec_list[0] if codec_list else "unknown"
        if progressive:
            return vcodec, (codec_list[1] if len(codec_list) > 1 else "mp4a.40.2")
        return vcodec, "none"

    @staticmethod
    def _clean_video_title(title: str) -> str:
        title = _TITLE_SUFFIX_RE.sub("", title.strip())
        return _TITLE_EXT_RE.sub("", title).strip()

    @staticmethod
    def _extension_from_mime(mime_type: str) -> str:
        mime_type = mime_type.split(";", 1)[0].strip().lower()
        if mime_type == "audio/mp4":
            return "m4a"
        if "/" in mime_type:
            return mime_type.rsplit("/", 1)[1] or "mp4"
        return "mp4"

    @staticmethod
    def _int_or_none(value: object) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _build_opener(self) -> urllib.request.OpenerDirector:
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        opener.addheaders = list(self._build_google_request_headers(
            accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ).items())
        for name, value in self.parse_cookie_header(self.get_manual_cookie_header()):
            cookie = http.cookiejar.Cookie(
                version=0, name=name, value=value,
                port=None, port_specified=False,
                domain=".google.com", domain_specified=True, domain_initial_dot=True,
                path="/", path_specified=True,
                secure=True, expires=None, discard=True,
                comment=None, comment_url=None, rest={},
            )
            cj.set_cookie(cookie)
        return opener

    def _build_google_request_headers(self, accept: str = "application/json,text/plain,*/*") -> dict[str, str]:
        headers = {
            "User-Agent": _UA,
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{_GOOGLE_ORIGIN}/",
        }
        cookie_header = self.get_manual_cookie_header()
        if cookie_header:
            headers["Cookie"] = cookie_header
        return headers

    def _list_folder(self, folder_url: str, opener: urllib.request.OpenerDirector) -> list[str]:
        folder_id = self._extract_folder_id(folder_url)
        if not folder_id:
            return []
        resourcekey = self._extract_resource_key(folder_url)
        embedded_query = {"id": folder_id}
        if resourcekey:
            embedded_query["resourcekey"] = resourcekey
        urls_to_try = [
            f"https://drive.google.com/embeddedfolderview?{urllib.parse.urlencode(embedded_query)}#list",
            folder_url,
        ]
        for url in urls_to_try:
            try:
                response = opener.open(url, timeout=30)
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    continue
                html = response.read().decode("utf-8", errors="replace")
                lower_html = html.lower()
                if "accounts.google.com" in html or ("sign in" in lower_html and "google" in lower_html):
                    raise UserFacingDownloadError("drive_permission_denied")
                file_urls = self._extract_file_urls_from_html(html)
                if file_urls:
                    return file_urls
            except UserFacingDownloadError:
                raise
            except Exception:
                continue
        return []

    @staticmethod
    def _extract_file_urls_from_html(html: str) -> list[str]:
        text = DriveDownloadService._decode_drive_html(html)
        seen: dict[str, str] = {}

        def add(file_id: str, resourcekey: str | None = None) -> None:
            if file_id not in seen or (resourcekey and "resourcekey=" not in seen[file_id]):
                seen[file_id] = DriveDownloadService._file_url(file_id, resourcekey)

        file_path_pattern = r'(?:https://drive\.google\.com)?/file/d/([a-zA-Z0-9_-]{20,})([^"\'<\s]*)'
        for file_id, tail in re.findall(file_path_pattern, text, flags=re.IGNORECASE):
            resourcekey = DriveDownloadService._extract_resource_key(
                f"https://drive.google.com/file/d/{file_id}{tail}"
            )
            add(file_id, resourcekey)

        open_pattern = r'(?:https://drive\.google\.com)?/(?:open|uc)\?[^"\'<>\s]+'
        for raw_url in re.findall(open_pattern, text, flags=re.IGNORECASE):
            url = raw_url if raw_url.startswith("http") else f"https://drive.google.com{raw_url}"
            file_id = DriveDownloadService._extract_file_id(url)
            if file_id:
                add(file_id, DriveDownloadService._extract_resource_key(url))

        for file_id in DriveDownloadService._extract_file_ids_from_html(html):
            add(file_id)

        return list(seen.values())

    @staticmethod
    def _extract_file_ids_from_html(html: str) -> list[str]:
        text = DriveDownloadService._decode_drive_html(html)
        seen: dict[str, None] = {}
        patterns = [
            r'/file/d/([a-zA-Z0-9_-]{20,})',
            r'href="/open\?id=([a-zA-Z0-9_-]{20,})"',
            r'"(?:id|doc_id|fileId)"\s*:\s*"([a-zA-Z0-9_-]{20,})"',
            r'\["([a-zA-Z0-9_-]{20,})","[^"]+\.(?:mp4|mov|mkv|webm|avi|m4v|wmv)"',
        ]
        attr_patterns = (
            r'data-id="([a-zA-Z0-9_-]{20,})"',
            r'entry-([a-zA-Z0-9_-]{20,})"',
        )
        for pattern in (*attr_patterns, *patterns):
            for fid in re.findall(pattern, text, flags=re.IGNORECASE):
                seen[fid] = None
        return list(seen.keys())

    @staticmethod
    def _decode_drive_html(html: str) -> str:
        return html_lib.unescape(html).replace("\\u0026", "&").replace("\\u003d", "=").replace("\\/", "/")

    @staticmethod
    def _file_url(file_id: str, resourcekey: str | None = None) -> str:
        url = f"https://drive.google.com/file/d/{file_id}/view"
        if resourcekey:
            url = f"{url}?{urllib.parse.urlencode({'resourcekey': resourcekey})}"
        return url

    @staticmethod
    def _folder_url(folder_id: str, resourcekey: str | None = None) -> str:
        url = f"https://drive.google.com/drive/folders/{folder_id}"
        if resourcekey:
            url = f"{url}?{urllib.parse.urlencode({'resourcekey': resourcekey})}"
        return url

    def _normalize_file_url(self, url: str) -> str | None:
        file_id = self._extract_file_id(url)
        if not file_id:
            return None
        return self._file_url(file_id, self._extract_resource_key(url))

    def _normalize_folder_url(self, url: str) -> str | None:
        folder_id = self._extract_folder_id(url)
        if not folder_id:
            return None
        return self._folder_url(folder_id, self._extract_resource_key(url))

    @staticmethod
    def _extract_resource_key(url: str) -> str | None:
        try:
            keys = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("resourcekey", [])
            return keys[0] if keys else None
        except Exception:
            return None

    @staticmethod
    def _extract_file_id(url: str) -> str | None:
        try:
            p = urllib.parse.urlparse(url)
            host = (p.hostname or "").lower()
            if not (
                host == "drive.google.com"
                or host == "docs.google.com"
                or host == "drive.usercontent.google.com"
            ):
                return None
            parts = [x for x in p.path.split("/") if x]
            if "d" in parts:
                idx = parts.index("d")
                if idx + 1 < len(parts):
                    return parts[idx + 1]
            ids = urllib.parse.parse_qs(p.query).get("id", [])
            if ids:
                return ids[0]
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_folder_id(url: str) -> str | None:
        try:
            p = urllib.parse.urlparse(url)
            if "drive.google.com" not in (p.hostname or "").lower():
                return None
            parts = [x for x in p.path.split("/") if x]
            if "folders" in parts:
                idx = parts.index("folders")
                return parts[idx + 1] if idx + 1 < len(parts) else None
        except Exception:
            pass
        return None
