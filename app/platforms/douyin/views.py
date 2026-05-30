from __future__ import annotations

from typing import Callable

from app.platforms.base_view import PlatformPage
from app.platforms.douyin.service import CONFIG, DouyinService


def create_page(language_getter: Callable[[], str], theme_getter: Callable[[], str]) -> PlatformPage:
    return PlatformPage(CONFIG, DouyinService, language_getter, theme_getter)
