#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RawCull - модуль работы с XMP sidecar-файлами через ExifTool.

Пометка "выбрано/не выбрано" хранится в теге xmpDM:pick (в ExifTool
доступен под коротким именем "Pick"), который является стандартным
Lightroom-флагом Pick/Unflagged:
    Pick = 1  -> фото помечено как выбранное
    Pick = 0  -> фото не помечено (нейтральное)

Метаданные ВСЕГДА пишутся в sidecar-файл рядом с оригиналом
(например ALL_1249.CR2 -> ALL_1249.xmp), оригинал никогда не меняется.
"""

import os
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path


def get_exiftool_cmd() -> list[str]:
    """
    Возвращает команду запуска ExifTool в виде списка.

    Windows: exiftool.exe рядом с программой (или рядом со скриптом).
    macOS:   папка exiftool_mac (скрипт exiftool + папка lib из дистрибутива
             Image-ExifTool) запускается через системный Perl.
    Запасной вариант: exiftool из PATH или из Homebrew.
    """
    if sys.platform == "win32":
        if getattr(sys, "frozen", False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).parent
        for name in ("exiftool.exe", "exiftool"):
            candidate = base_dir / name
            if candidate.exists():
                return [str(candidate)]
    else:
        if getattr(sys, "frozen", False):
            base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        else:
            base_dir = Path(__file__).parent
        script = base_dir / "exiftool_mac" / "exiftool"
        perl = shutil.which("perl") or "/usr/bin/perl"
        if script.exists():
            return [perl, str(script)]

        # Приложение из Finder получает урезанный PATH, поэтому смотрим Homebrew вручную
        found = shutil.which("exiftool")
        if not found:
            for p in ("/opt/homebrew/bin/exiftool", "/usr/local/bin/exiftool"):
                if os.path.exists(p):
                    found = p
                    break
        if found:
            return [found]

    return ["exiftool"]


EXIFTOOL_CMD = get_exiftool_cmd()
EXIFTOOL_PATH = " ".join(EXIFTOOL_CMD)  # для сообщений


def _nfc(text: str) -> str:
    """macOS может отдавать имена в другой Unicode-форме (NFD). Приводим к одной."""
    return unicodedata.normalize("NFC", text)


def _sidecar_path(media_path: str) -> str:
    """ALL_1249.CR2 -> ALL_1249.xmp (расширение заменяется, не добавляется)."""
    return str(Path(media_path).with_suffix(".xmp"))


def _run_exiftool(args: list[str]) -> subprocess.CompletedProcess:
    """Запускает exiftool с заданными аргументами, скрывая консольное окно на Windows."""
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    result = subprocess.run(
        EXIFTOOL_CMD + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    return result


def read_pick_status(media_path: str) -> bool:
    """
    Читает текущий статус пометки для файла.
    Источник - sidecar .xmp, если он существует. Если sidecar нет - считаем
    файл непомеченным (False).
    """
    sidecar = _sidecar_path(media_path)
    if not os.path.exists(sidecar):
        return False

    result = _run_exiftool(["-Pick", "-n", "-s3", sidecar])
    value = result.stdout.strip()
    return value == "1"


def read_pick_status_batch(media_paths: list[str]) -> dict[str, bool]:
    """
    Пакетное чтение статусов пометки для списка файлов - один вызов exiftool
    на все существующие sidecar-файлы сразу (быстрее, чем по одному).
    """
    # Несколько файлов могут делить один sidecar (RAW+JPG: ALL_1249.CR2 и ALL_1249.jpg
    # оба используют ALL_1249.xmp), поэтому храним список медиафайлов на sidecar.
    sidecar_map = {}  # sidecar_path -> [media_path, ...]
    for media_path in media_paths:
        sidecar = _sidecar_path(media_path)
        if os.path.exists(sidecar):
            sidecar_map.setdefault(sidecar, []).append(media_path)

    statuses = {media_path: False for media_path in media_paths}

    if not sidecar_map:
        return statuses

    sidecar_list = list(sidecar_map.keys())

    # Формат -T (tab-separated) с явным именем файла: надёжно при нескольких файлах.
    result = _run_exiftool(
        ["-Pick", "-n", "-T", "-FileName", "-f"] + sidecar_list
    )
    lines = result.stdout.strip().splitlines()
    for line in lines:
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        pick_value, filename = parts
        # Находим соответствующий sidecar по имени файла
        for sidecar, media_list in sidecar_map.items():
            if _nfc(Path(sidecar).name) == _nfc(filename):
                for media_path in media_list:
                    statuses[media_path] = (pick_value.strip() == "1")
                break

    return statuses


def set_pick_status(media_path: str, picked: bool) -> bool:
    """
    Устанавливает статус пометки для одного файла, записывая в sidecar .xmp.
    Если sidecar не существует - создаётся новый.
    Возвращает True при успехе.
    """
    sidecar = _sidecar_path(media_path)
    value = "1" if picked else "0"

    if os.path.exists(sidecar):
        args = [f"-Pick={value}", "-overwrite_original", sidecar]
    else:
        # Создание нового sidecar с нуля
        args = ["-o", sidecar, f"-Pick={value}"]

    result = _run_exiftool(args)
    return result.returncode == 0


def set_pick_status_batch(media_paths: list[str], picked: bool) -> tuple[int, list[str]]:
    """
    Массовая установка статуса пометки для списка файлов.
    Возвращает (количество_успешных, список_путей_с_ошибкой).
    """
    success_count = 0
    failed = []
    for media_path in media_paths:
        if set_pick_status(media_path, picked):
            success_count += 1
        else:
            failed.append(media_path)
    return success_count, failed


def check_exiftool_available() -> tuple[bool, str]:
    """Проверяет, что exiftool найден и запускается. Возвращает (ok, сообщение)."""
    try:
        result = _run_exiftool(["-ver"])
        if result.returncode == 0:
            version = result.stdout.strip()
            return True, f"ExifTool найден, версия {version} ({EXIFTOOL_PATH})"
        else:
            return False, f"ExifTool вернул ошибку: {result.stderr}"
    except FileNotFoundError:
        return False, (
            f"ExifTool не найден по пути '{EXIFTOOL_PATH}'. "
            "Положите ExifTool рядом с программой (на Windows exiftool.exe) или установите его в систему."
        )
    except Exception as e:
        return False, f"Ошибка запуска ExifTool: {e}"
