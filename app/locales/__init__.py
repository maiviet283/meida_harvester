from __future__ import annotations

from app.locales.en import TEXT as EN_TEXT
from app.locales.vi import TEXT as VI_TEXT


LOCALES = {
    "vi": VI_TEXT,
    "en": EN_TEXT,
}


def translate(language: str, key: str, **kwargs: str) -> str:
    data = LOCALES.get(language, VI_TEXT)
    value: object = data
    for part in key.split("."):
        if not isinstance(value, dict):
            value = key
            break
        value = value.get(part, key)
    text = str(value)
    return text.format(**kwargs) if kwargs else text
