# -*- coding: utf-8 -*-
"""
RawCull - разбор списков файлов для фильтра по списку.

Источники:
- текст из буфера обмена: имена через пробел, например "ALL_1249 ALL_1252"
- CSV-файл с колонкой File (например, список от клиента с JPEG-превью)

В обоих случаях берётся только базовое имя БЕЗ расширения, т.к. по списку
превью (JPG) нужно отобрать RAW-файлы с тем же именем.
"""

import csv
import unicodedata
from pathlib import PurePath


def _base_name(value: str) -> str:
    """'ALL_1249.jpg' -> 'ALL_1249', 'C:/x/ALL_1249.CR3' -> 'ALL_1249'."""
    value = value.strip().strip('"').strip()
    if not value:
        return ""
    return unicodedata.normalize("NFC", PurePath(value).stem).lower()


def parse_clipboard_text(text: str) -> set[str]:
    """Разбирает текст из буфера: имена разделены пробелами (или переносами строк)."""
    stems = set()
    for token in text.split():
        name = _base_name(token)
        if name:
            stems.add(name)
    return stems


def parse_csv_file(path: str) -> set[str]:
    """
    Читает CSV и берёт значения колонки File (регистр заголовка не важен).
    Строки без значения в File (например, пустая строка-шаблон) пропускаются.
    """
    stems = set()
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Файл пуст или не содержит заголовка")

        file_key = next(
            (name for name in reader.fieldnames if name and name.strip().lower() == "file"),
            None,
        )
        if file_key is None:
            raise ValueError("В CSV не найдена колонка 'File'")

        for row in reader:
            name = _base_name(row.get(file_key) or "")
            if name:
                stems.add(name)
    return stems


def file_stem(path: str) -> str:
    """Базовое имя файла без расширения, в нижнем регистре - для сопоставления."""
    return unicodedata.normalize("NFC", PurePath(path).stem).lower()
