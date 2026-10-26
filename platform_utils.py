# -*- coding: utf-8 -*-
"""Мелкие помощники для разных ОС: пути к ресурсам, размеры шрифтов."""

import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"


def resource_dir() -> Path:
    """Папка, где лежат assets и вложенные программы (в сборке PyInstaller и без неё)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent


def resource_path(*parts: str) -> Path:
    return resource_dir().joinpath(*parts)


def pt(size: int) -> int:
    """Размер шрифта в пунктах. На маке пункт = 1 пиксель (72 dpi), на Windows 96 dpi.
    Поэтому на маке увеличиваем, чтобы текст выглядел так же."""
    return round(size * 4 / 3) if IS_MAC else size
