# -*- coding: utf-8 -*-
"""
RawCull - настройки программы.

Хранятся в QSettings (на Windows - реестр, ветка RawCull).
Единый объект prefs используется и главным окном, и просмотрщиком.
После сохранения настроек prefs.changed сообщает всем окнам, что нужно обновиться.

Горячие клавиши: для каждого действия список клавиш (можно несколько),
хранятся в переносимом виде QKeySequence ("Left", "PgDown", "Ctrl+A").
"""

import json

from PySide6.QtCore import QObject, Signal, QSettings, Qt, QKeyCombination
from PySide6.QtGui import QKeySequence, QPalette, QColor

ORG = "RawCull"
APP = "RawCull"

# Действия: id -> (подпись в окне настроек, где работает)
ACTIONS = {
    "pick":            ("Пометить / снять пометку", "сетка и просмотр"),
    "next":            ("Следующее фото", "просмотр"),
    "prev":            ("Предыдущее фото", "просмотр"),
    "info":            ("Информация EXIF", "просмотр"),
    "settings":        ("Открыть настройки", "просмотр"),
    "close":           ("Закрыть просмотр", "просмотр"),
    "select_all":      ("Выделить все", "сетка"),
    "clear_selection": ("Снять выделение", "сетка"),
}

DEFAULT_KEYS = {
    "pick": ["1"],
    "next": ["Right"],
    "prev": ["Left"],
    "info": ["I"],
    "settings": ["S"],
    "close": ["Esc"],
    "select_all": ["Ctrl+A"],
    "clear_selection": ["Ctrl+D"],
}



KEY_SYMBOLS = {
    "Left": "←", "Right": "→", "Up": "↑", "Down": "↓",
    "Space": "Пробел", "Return": "Enter", "Enter": "Enter",
}

DEFAULT_THUMB_SIZE = 200
DEFAULT_MARK_COLOR = "#2e7d32"
DEFAULT_BG_COLOR = "#f0f0f0"



def normalize_key(text: str) -> str:
    """Приводит запись клавиши к единому виду ('esc' -> 'Esc')."""
    return QKeySequence(text).toString(QKeySequence.PortableText)


def event_to_text(event) -> str:
    """Нажатие клавиши -> текст ('Ctrl+A', 'Left'). Пустая строка для одиночных модификаторов."""
    key = event.key()
    if key in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta,
               Qt.Key_AltGr, Qt.Key_unknown, 0):
        return ""
    mods = event.modifiers() & ~Qt.KeypadModifier
    return QKeySequence(QKeyCombination(mods, Qt.Key(key))).toString(QKeySequence.PortableText)


def _to_bool(value, default=False) -> bool:
    if value is None:
        return default
    return str(value).lower() in ("true", "1", "yes")


class Prefs(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._s = QSettings(ORG, APP)

    # --- клавиши -------------------------------------------------------
    def keys(self, action: str) -> list[str]:
        raw = self._s.value(f"keys/{action}")
        if raw is not None:
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    return [normalize_key(k) for k in data if k]
            except (ValueError, TypeError):
                pass
        return [normalize_key(k) for k in DEFAULT_KEYS[action]]

    def set_keys(self, action: str, keys: list[str]):
        clean = []
        for k in keys:
            k = normalize_key(k) if k else ""
            if k and k not in clean:
                clean.append(k)
        self._s.setValue(f"keys/{action}", json.dumps(clean))

    def matches(self, event, action: str) -> bool:
        text = event_to_text(event)
        return bool(text) and text in self.keys(action)

    def key_hint(self, action: str) -> str:
        """Подпись первой клавиши действия для кнопок: '1', 'Ctrl+A', '←'."""
        keys = self.keys(action)
        if not keys:
            return ""
        return KEY_SYMBOLS.get(keys[0], keys[0])

    # --- внешний вид и общее --------------------------------------------
    @property
    def thumb_size(self) -> int:
        try:
            return max(100, min(400, int(self._s.value("ui/thumb_size", DEFAULT_THUMB_SIZE))))
        except (TypeError, ValueError):
            return DEFAULT_THUMB_SIZE

    @thumb_size.setter
    def thumb_size(self, v: int):
        self._s.setValue("ui/thumb_size", int(v))

    @property
    def mark_color(self) -> str:
        c = QColor(str(self._s.value("ui/mark_color", DEFAULT_MARK_COLOR)))
        return c.name() if c.isValid() else DEFAULT_MARK_COLOR

    @mark_color.setter
    def mark_color(self, v: str):
        self._s.setValue("ui/mark_color", v)

    @property
    def bg_color(self) -> str:
        c = QColor(str(self._s.value("ui/bg_color", DEFAULT_BG_COLOR)))
        return c.name() if c.isValid() else DEFAULT_BG_COLOR

    @bg_color.setter
    def bg_color(self, v: str):
        self._s.setValue("ui/bg_color", v)

    @property
    def show_ext(self) -> bool:
        return _to_bool(self._s.value("ui/show_ext"), True)

    @show_ext.setter
    def show_ext(self, v: bool):
        self._s.setValue("ui/show_ext", bool(v))

    @property
    def wheel_nav(self) -> bool:
        return _to_bool(self._s.value("viewer/wheel_nav"), True)

    @wheel_nav.setter
    def wheel_nav(self, v: bool):
        self._s.setValue("viewer/wheel_nav", bool(v))

    @property
    def remember_folder(self) -> bool:
        return _to_bool(self._s.value("general/remember_folder"), True)

    @remember_folder.setter
    def remember_folder(self, v: bool):
        self._s.setValue("general/remember_folder", bool(v))

    @property
    def last_folder(self) -> str:
        return str(self._s.value("general/last_folder", "") or "")

    @last_folder.setter
    def last_folder(self, v: str):
        self._s.setValue("general/last_folder", v)

    def reset_all(self):
        """Сброс всех настроек, кроме последней папки и режима EXIF в просмотре."""
        for key in list(self._s.allKeys()):
            if key.startswith(("keys/", "ui/", "viewer/wheel_nav", "general/remember_folder")):
                self._s.remove(key)


prefs = Prefs()


# ----------------------------------------------------------------------
# Тема
# ----------------------------------------------------------------------
_original = {}


def apply_theme(app, bg_color: str):
    """Фон и текст рассчитываются по bg_color. Если bg_color == системный, восстанавливаем оригинал."""
    if "system" not in _original:
        _original["system"] = (app.style().objectName(), QPalette(app.palette()))
    
    default_bg = _original["system"][1].color(QPalette.Window)
    if QColor(bg_color) == default_bg or bg_color == "#f0f0f0":
        app.setStyle(_original["system"][0])
        app.setPalette(_original["system"][1])
        return
    
    app.setStyle("Fusion")
    pal = QPalette()
    bg = QColor(bg_color)
    brightness = 0.299*bg.red() + 0.587*bg.green() + 0.114*bg.blue()
    text_col = QColor(230, 230, 230) if brightness < 128 else QColor(20, 20, 20)
    gray_col = QColor(150, 150, 150) if brightness < 128 else QColor(100, 100, 100)
    
    pal.setColor(QPalette.Window, bg)
    pal.setColor(QPalette.WindowText, text_col)
    pal.setColor(QPalette.Base, bg.lighter(110) if brightness < 128 else bg.darker(105))
    pal.setColor(QPalette.AlternateBase, bg)
    pal.setColor(QPalette.ToolTipBase, bg.lighter(120) if brightness < 128 else bg.darker(110))
    pal.setColor(QPalette.ToolTipText, text_col)
    pal.setColor(QPalette.Text, text_col)
    pal.setColor(QPalette.Button, bg.lighter(115) if brightness < 128 else bg.darker(103))
    pal.setColor(QPalette.ButtonText, text_col)
    pal.setColor(QPalette.BrightText, QColor(255, 80, 80))
    pal.setColor(QPalette.Highlight, QColor(42, 130, 218))
    pal.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.Disabled, QPalette.Text, gray_col)
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, gray_col)
    app.setPalette(pal)
