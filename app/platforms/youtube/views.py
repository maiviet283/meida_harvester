from __future__ import annotations

from typing import Callable

from app.platforms.base_view import PlatformPage
from app.platforms.youtube.service import CONFIG, YouTubeService


def create_page(language_getter: Callable[[], str], theme_getter: Callable[[], str]) -> PlatformPage:
    return PlatformPage(CONFIG, YouTubeService, language_getter, theme_getter)
