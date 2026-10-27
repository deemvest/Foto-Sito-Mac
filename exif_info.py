# -*- coding: utf-8 -*-
"""
RawCull - чтение краткой EXIF-информации для полноэкранного просмотра.

Читается через ExifTool (тот же exiftool.exe, что и для пометок):
- дата и время съёмки (DateTimeOriginal)
- диафрагма (FNumber), выдержка (ExposureTime), ISO

Результаты кэшируются в памяти, повторный вызов по тому же файлу бесплатный.
"""

import json
from datetime import datetime

from xmp_manager import _run_exiftool

_cache: dict[str, dict] = {}

EMPTY = {"datetime": None, "fnumber": None, "exposure": None, "iso": None, "resolution": None, "focal_length": None}


def _format_datetime(raw: str | None) -> str | None:
    """'2010:02:15 12:10:55' -> '15.02.2010 12:10:55'."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S").strftime("%d.%m.%Y %H:%M:%S")
    except ValueError:
        return raw


def read_summary(path: str) -> dict:
    """Возвращает словарь с datetime / fnumber / exposure / iso (значения или None)."""
    if path in _cache:
        return _cache[path]

    result = dict(EMPTY)
    try:
        proc = _run_exiftool(
            ["-j", "-DateTimeOriginal", "-FNumber", "-ExposureTime", "-ISO", 
             "-ImageWidth", "-ImageHeight", "-FocalLength", path]
        )
        data = json.loads(proc.stdout) if proc.stdout.strip() else []
        if data:
            tags = data[0]
            result["datetime"] = _format_datetime(tags.get("DateTimeOriginal"))
            fnumber = tags.get("FNumber")
            result["fnumber"] = f"f{fnumber}" if fnumber is not None else None
            exposure = tags.get("ExposureTime")
            result["exposure"] = str(exposure) if exposure is not None else None
            iso = tags.get("ISO")
            result["iso"] = str(iso) if iso is not None else None
            
            w = tags.get("ImageWidth")
            h = tags.get("ImageHeight")
            if w and h:
                result["resolution"] = f"{w} × {h}"
            
            fl = tags.get("FocalLength")
            if fl is not None:
                try:
                    fl_num = float(str(fl).replace(" mm", "").strip())
                    result["focal_length"] = f"{int(fl_num) if fl_num == int(fl_num) else fl_num}мм"
                except (ValueError, TypeError):
                    result["focal_length"] = str(fl)
    except Exception as e:
        print(f"[WARN] EXIF не прочитан для {path}: {e}")

    _cache[path] = result
    return result
