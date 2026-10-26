# -*- coding: utf-8 -*-
"""
RawCull - полноэкранный просмотр фотографии.

Возможности:
- Фото вписано в экран (Fit in Window)
- Имя файла с расширением в правом верхнем углу
- Цветной квадратик пометки слева от имени - только если фото помечено
- Клавиша 1 - переключить пометку (запись в XMP делает main через callback)
- Клавиша I - информация EXIF под именем файла, по кругу:
  нет -> дата и время съёмки -> диафрагма / выдержка / ISO -> нет
  состояние запоминается между запусками
- ЛКМ (клик без перетаскивания) - зум 100% / возврат к Fit, зум к центру
- В зуме - панорамирование перетаскиванием с зажатой ЛКМ
- Стрелки влево/вправо и колесо мыши - предыдущее/следующее (без зацикливания)
- Esc - выход

Горячие клавиши и цвет пометки берутся из настроек (settings.prefs).
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QSettings
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import QWidget

import exif_info
import settings as st
from platform_utils import pt
from settings_dialog import SettingsDialog

PICK_MARK_SIZE = 36           # размер квадратика, px (130% от 28)
PICK_MARK_MARGIN = 16         # отступ от краёв, px
OVERLAY_FONT_PT = 14          # размер шрифта имени файла (130% от 11)
OVERLAY_FONT_INFO = 11        # размер шрифта строк EXIF
OVERLAY_BG = QColor(0, 0, 0, 160)
PREFETCH_AHEAD = 2            # сколько кадров вперёд готовим заранее
PREFETCH_BEHIND = 1           # сколько кадров назад держим готовыми
_SKIPPED = object()           # задача отменена: кадр уже вне окна подгрузки
DRAG_THRESHOLD = 4            # px: меньше этого - это клик, а не перетаскивание
WHEEL_STEP = 120              # стандартный шаг колеса мыши (Qt angleDelta)

SETTINGS_ORG = "RawCull"
SETTINGS_APP = "RawCull"
SETTINGS_INFO_MODE = "viewer/info_mode"   # 0 - нет, 1 - дата, 2 - экспозиция



class FullscreenViewer(QWidget):
    """
    Полноэкранный просмотр.

    paths         - список путей (только видимые в текущем фильтре)
    index         - индекс стартового файла
    load_fn       - func(path) -> QImage | None  (полноразмерное встроенное превью)
    is_marked_fn  - func(path) -> bool
    toggle_fn     - func(path) -> bool  (переключает пометку, возвращает новое состояние)
    """

    closed = Signal(str)  # путь файла, на котором закрыли просмотр

    def __init__(self, paths, index, load_fn, is_marked_fn, toggle_fn):
        super().__init__()
        self._paths = list(paths)
        self._index = index
        self._load_fn = load_fn
        self._is_marked = is_marked_fn
        self._toggle = toggle_fn

        self._img = None
        self._zoomed = False
        self._offset = QPointF(0, 0)
        self._press = None          # позиция нажатия ЛКМ
        self._pan_origin = None     # offset на момент нажатия
        self._moved = False         # был ли жест перетаскиванием
        self._wheel_acc = 0
        self._exif_cache = {}
        # Фоновая подгрузка: кадры и EXIF готовятся в потоках. В словарях лежат
        # future: поток только считает, а результат забирает главный поток.
        self._img_futures = {}
        self._exif_futures = {}
        self._wanted = frozenset()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prefetch")

        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._info_mode = int(settings.value(SETTINGS_INFO_MODE, 0))

        self.setFocusPolicy(Qt.StrongFocus)
        self._load_current()
        self.showFullScreen()
        self.setFocus()

    # ------------------------------------------------------------------
    # Загрузка и навигация
    # ------------------------------------------------------------------
    def _window(self):
        lo = max(0, self._index - PREFETCH_BEHIND)
        hi = min(len(self._paths), self._index + PREFETCH_AHEAD + 1)
        return self._paths[lo:hi]

    def _update_window(self):
        """Запоминает, какие кадры нужны сейчас, и отбрасывает остальные."""
        self._wanted = frozenset(self._window())
        for d in (self._img_futures, self._exif_futures):
            for p in [p for p in d if p not in self._wanted]:
                d.pop(p).cancel()   # ещё не начатое отменится, начатое просто выбросим

    def _load_task(self, path):
        """Работает в потоке. Если кадр уже не нужен (быстро пролистали), пропускает."""
        if path not in self._wanted:
            return _SKIPPED
        try:
            return self._load_fn(path)
        except Exception as e:
            print(f"[WARN] Не удалось загрузить {path}: {e}")
            return None

    def _exif_task(self, path):
        if path not in self._wanted:
            return _SKIPPED
        try:
            return exif_info.read_summary(path)
        except Exception as e:
            print(f"[WARN] Не удалось прочитать EXIF {path}: {e}")
            return None

    def _submit_image(self, path):
        fut = self._img_futures.get(path)
        if fut is None:
            fut = self._pool.submit(self._load_task, path)
            self._img_futures[path] = fut
        return fut

    def _submit_exif(self, path):
        if path in self._exif_cache:
            return None
        fut = self._exif_futures.get(path)
        if fut is None:
            fut = self._pool.submit(self._exif_task, path)
            self._exif_futures[path] = fut
        return fut

    def _get_image(self, path):
        """Возвращает кадр: готовый из фона, либо ждёт уже начатую загрузку."""
        img = self._submit_image(path).result()
        if img is _SKIPPED:
            self._img_futures.pop(path, None)
            img = self._load_fn(path)
        return img

    def _prefetch_neighbours(self):
        """Ставит в очередь соседние кадры (и их EXIF, если он сейчас показывается)."""
        order = [self._index + i for i in range(1, PREFETCH_AHEAD + 1)]
        order += [self._index - i for i in range(1, PREFETCH_BEHIND + 1)]
        for i in order:
            if 0 <= i < len(self._paths):
                self._submit_image(self._paths[i])
                if self._info_mode:
                    self._submit_exif(self._paths[i])

    def _load_current(self):
        path = self._paths[self._index]
        self._update_window()
        self._submit_image(path)            # текущий кадр - первым в очереди
        self._prefetch_neighbours()         # соседи грузятся параллельно с ним
        self._img = self._get_image(path)
        self._zoomed = False
        self._offset = QPointF(0, 0)
        self._ensure_exif()
        self.setWindowTitle(f"{Path(path).name}  ({self._index + 1} / {len(self._paths)})")
        self.update()

    def _go(self, step):
        """Листание без зацикливания."""
        new_index = self._index + step
        if 0 <= new_index < len(self._paths) and new_index != self._index:
            self._index = new_index
            self._load_current()

    def _toggle_pick(self):
        self._toggle(self._paths[self._index])
        self.update()

    def _cycle_info(self):
        """I: нет -> дата -> экспозиция -> нет. Состояние сохраняется."""
        self._info_mode = (self._info_mode + 1) % 3
        QSettings(SETTINGS_ORG, SETTINGS_APP).setValue(SETTINGS_INFO_MODE, self._info_mode)
        self._ensure_exif()
        self._prefetch_neighbours()
        self.update()

    def _ensure_exif(self):
        """Читает EXIF текущего файла, только если информация сейчас показывается."""
        if not self._info_mode or self.current_path in self._exif_cache:
            return
        path = self.current_path
        info = self._submit_exif(path).result()
        if info is _SKIPPED:
            info = exif_info.read_summary(path)
        self._exif_cache[path] = info
        self._exif_futures.pop(path, None)

    @property
    def current_path(self):
        return self._paths[self._index]

    # ------------------------------------------------------------------
    # Геометрия
    # ------------------------------------------------------------------
    def _fit_rect(self) -> QRectF:
        iw, ih = self._img.width(), self._img.height()
        scale = min(self.width() / iw, self.height() / ih)
        w, h = iw * scale, ih * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _zoom_rect(self) -> QRectF:
        iw, ih = self._img.width(), self._img.height()
        x = (self.width() - iw) / 2 + self._offset.x()
        y = (self.height() - ih) / 2 + self._offset.y()
        return QRectF(x, y, iw, ih)

    def _clamp(self, off: QPointF) -> QPointF:
        """Не даём уводить изображение дальше его края."""
        if self._img is None:
            return QPointF(0, 0)
        max_x = max(0.0, (self._img.width() - self.width()) / 2)
        max_y = max(0.0, (self._img.height() - self.height()) / 2)
        x = min(max_x, max(-max_x, off.x()))
        y = min(max_y, max(-max_y, off.y()))
        return QPointF(x, y)

    # ------------------------------------------------------------------
    # Отрисовка
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111111"))

        if self._img is None:
            painter.setPen(QColor("#dddddd"))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Не удалось загрузить превью")
        else:
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            rect = self._zoom_rect() if self._zoomed else self._fit_rect()
            painter.drawImage(rect, self._img)

        self._draw_overlay(painter)
        painter.end()

    def _draw_label(self, painter, fm, text, x_right, y, pad=8) -> float:
        """Рисует подпись в полупрозрачной плашке, выровненную по правому краю.
        Возвращает высоту плашки."""
        w = fm.horizontalAdvance(text) + 2 * pad
        h = fm.height() + pad
        rect = QRectF(x_right - w, y, w, h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(OVERLAY_BG)
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(rect, Qt.AlignCenter, text)
        return h

    def _info_lines(self) -> list[str]:
        info = self._exif_cache.get(self.current_path)
        if info is None:
            return ["EXIF не загружен"]
        if self._info_mode == 1:
            return [
                f"Дата/время: {info['datetime'] or '—'}",
                f"Разрешение: {info['resolution'] or '—'}"
            ]
        return [
            f"{info['fnumber'] or '—'} - {info['exposure'] or '—'} - "
            f"ISO {info['iso'] or '—'}",
            f"Фокусное: {info['focal_length'] or '—'}"
        ]

    def _draw_overlay(self, painter):
        font_name = QFont()
        font_name.setPointSize(pt(OVERLAY_FONT_PT))
        painter.setFont(font_name)
        fm = painter.fontMetrics()

        x_right = self.width() - PICK_MARK_MARGIN
        y = PICK_MARK_MARGIN

        # Имя файла с расширением - правый верхний угол
        name = Path(self.current_path).name
        name_h = self._draw_label(painter, fm, name, x_right, y)
        name_w = fm.horizontalAdvance(name) + 16
        name_left = x_right - name_w

        # Квадратик пометки - слева от имени, только если помечено
        if self._is_marked(self.current_path):
            mx = name_left - 8 - PICK_MARK_SIZE
            my = y + (name_h - PICK_MARK_SIZE) / 2
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(QColor(st.prefs.mark_color))
            painter.drawRect(QRectF(mx, my, PICK_MARK_SIZE, PICK_MARK_SIZE))

        # EXIF-строки под именем файла
        if self._info_mode:
            ly = y + name_h + 6
            font_info = QFont()
            font_info.setPointSize(pt(OVERLAY_FONT_INFO))
            painter.setFont(font_info)
            fm_info = painter.fontMetrics()
            for line in self._info_lines():
                ly += self._draw_label(painter, fm_info, line, x_right, ly) + 4

    # ------------------------------------------------------------------
    # Мышь
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.position()
            self._pan_origin = QPointF(self._offset)
            self._moved = False

    def mouseMoveEvent(self, event):
        if self._press is None:
            return
        delta = event.position() - self._press
        if abs(delta.x()) > DRAG_THRESHOLD or abs(delta.y()) > DRAG_THRESHOLD:
            self._moved = True
        if self._moved and self._zoomed:
            self._offset = self._clamp(self._pan_origin + delta)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press is not None:
            if not self._moved:
                # Клик без перетаскивания - переключаем зум
                self._zoomed = not self._zoomed
                self._offset = QPointF(0, 0)
                self.update()
            self._press = None

    def wheelEvent(self, event):
        if not st.prefs.wheel_nav:
            event.accept()
            return
        self._wheel_acc += event.angleDelta().y()
        while self._wheel_acc >= WHEEL_STEP:
            self._go(-1)
            self._wheel_acc -= WHEEL_STEP
        while self._wheel_acc <= -WHEEL_STEP:
            self._go(1)
            self._wheel_acc += WHEEL_STEP
        event.accept()

    # ------------------------------------------------------------------
    # Клавиатура
    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        prefs = st.prefs
        if prefs.matches(event, "prev"):
            self._go(-1)
        elif prefs.matches(event, "next"):
            self._go(1)
        elif prefs.matches(event, "pick"):
            self._toggle_pick()
        elif prefs.matches(event, "info"):
            self._cycle_info()
        elif prefs.matches(event, "settings"):
            self.open_settings()
        elif prefs.matches(event, "close"):
            self.close()
        else:
            super().keyPressEvent(event)

    def open_settings(self):
        """Окно настроек поверх просмотра; после сохранения перерисовываем."""
        SettingsDialog(self, viewer=True).exec()
        self.update()
        self.setFocus()

    def resizeEvent(self, event):
        self._offset = self._clamp(self._offset)
        super().resizeEvent(event)

    def closeEvent(self, event):
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.closed.emit(self.current_path)
        super().closeEvent(event)
