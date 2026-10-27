# -*- coding: utf-8 -*-
"""
Фото-Сито - дисковый кэш миниатюр.

Кэш лежит в папке "cache" ВНУТРИ папки с фотографиями:

    D:\\Съёмка\\
        ALL_1249.CR2
        ALL_1250.CR2
        cache\\
            ALL_1249.CR2.200.jpg
            ALL_1250.CR2.200.jpg

Чтобы удалить кэш, достаточно удалить папку "cache". Программа создаст её заново.

Правила:
- один файл кэша на одну миниатюру, имя: <файл>.<размер миниатюры>.jpg
- кэш считается актуальным, если дата изменения файла кэша равна дате изменения
  исходного фото (при замене или правке фото миниатюра пересоздаётся сама)
- если папку нельзя записать (карта памяти с защитой, сетевой диск только для
  чтения), кэш молча отключается, программа работает как раньше
- папка "cache" не содержит поддерживаемых расширений, поэтому в сетку не попадает
"""

import os
from typing import Optional

from PySide6.QtGui import QImage

CACHE_DIR_NAME = "cache"
JPEG_QUALITY = 90


def _cache_file(photo_path: str, thumb_size: int) -> str:
    folder, name = os.path.split(photo_path)
    return os.path.join(folder, CACHE_DIR_NAME, f"{name}.{thumb_size}.jpg")


def load(photo_path: str, thumb_size: int) -> Optional[QImage]:
    """Возвращает миниатюру из кэша или None, если её нет или она устарела."""
    cache_path = _cache_file(photo_path, thumb_size)
    try:
        if int(os.stat(cache_path).st_mtime) != int(os.stat(photo_path).st_mtime):
            return None
        img = QImage(cache_path)
        return None if img.isNull() else img
    except OSError:
        return None


def save(photo_path: str, thumb_size: int, img: QImage) -> bool:
    """Сохраняет миниатюру в кэш. При любой ошибке возвращает False и не мешает работе."""
    cache_path = _cache_file(photo_path, thumb_size)
    tmp_path = cache_path + ".tmp"
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        if not img.save(tmp_path, "JPG", JPEG_QUALITY):
            return False
        mtime = int(os.stat(photo_path).st_mtime)
        os.utime(tmp_path, (mtime, mtime))
        os.replace(tmp_path, cache_path)
        return True
    except OSError:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False
