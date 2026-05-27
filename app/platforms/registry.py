from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtWidgets import QWidget

from app.platforms.common import PlatformConfig
from app.platforms.facebook.service import CONFIG as FACEBOOK_CONFIG
from app.platforms.facebook.views import create_page as create_facebook_page
from app.platforms.instagram.service import CONFIG as INSTAGRAM_CONFIG
from app.platforms.instagram.views import create_page as create_instagram_page
from app.platforms.tiktok.service import CONFIG as TIKTOK_CONFIG
from app.platforms.tiktok.views import create_page as create_tiktok_page
from app.platforms.youtube.service import CONFIG as YOUTUBE_CONFIG
from app.platforms.youtube.views import create_page as create_youtube_page


@dataclass(frozen=True)
class PlatformModule:
    config: PlatformConfig
    create_page: Callable[[Callable[[], str], Callable[[], str]], QWidget]


PLATFORM_MODULES = [
    PlatformModule(TIKTOK_CONFIG, create_tiktok_page),
    PlatformModule(FACEBOOK_CONFIG, create_facebook_page),
    PlatformModule(INSTAGRAM_CONFIG, create_instagram_page),
    PlatformModule(YOUTUBE_CONFIG, create_youtube_page),
]
