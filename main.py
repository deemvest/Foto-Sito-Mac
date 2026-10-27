#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RawCull - прототип шага 1
Сканирование папки + показ миниатюр (включая RAW через встроенные превью).

Запуск: python main.py
"""

import sys
import os
import io
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from version import APP_TITLE, APP_NAME, APP_VERSION

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QScrollArea, QGridLayout, QFileDialog,
    QFrame, QSizePolicy, QStatusBar, QMessageBox
)
from PySide6.QtGui import (
    QPixmap, QImage, QPainter, QColor, QFont, QMouseEvent,
    QShortcut, QKeySequence
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QSize, QTimer, QEvent

from PIL import Image
import rawpy

import xmp_manager as xm
import thumb_cache
import filters as flt
from viewer import FullscreenViewer
import settings as st
from settings_dialog import SettingsDialog
from platform_utils import IS_MAC, resource_path, pt

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:
    HEIF_SUPPORTED = False

# Цвет подсветки фона помеченных ячеек (позже будет настройкой)

# ------------------------------------------------------------------
# Поддерживаемые расширения
# ------------------------------------------------------------------
RAW_EXTENSIONS = {
    ".cr2", ".cr3", ".nef", ".arw", ".raf", ".dng", ".pef",
    ".rw2", ".orf", ".srw", ".raw", ".nrw", ".kdc", ".3fr",
    ".erf", ".mef", ".mrw", ".x3f",
}
STANDARD_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif"}
ALL_EXTENSIONS = RAW_EXTENSIONS | STANDARD_EXTENSIONS

# Сколько миниатюр готовить одновременно (потолок; реально берётся min(это, ядер CPU))
THUMB_WORKERS = 4



def extract_preview_qimage(path: str, max_size: int = 200) -> QImage | None:
    """
    Извлекает превью из файла (RAW через rawpy, JPEG/HEIC через Pillow)
    и возвращает QImage, уменьшенный до max_size по большей стороне.
    Учитывает поворот EXIF Orientation.
    """
    ext = Path(path).suffix.lower()
    pil_img = None

    try:
        if ext in RAW_EXTENSIONS:
            with rawpy.imread(path) as raw:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    pil_img = Image.open(io.BytesIO(thumb.data))
                elif thumb.format == rawpy.ThumbFormat.BITMAP:
                    pil_img = Image.fromarray(thumb.data)
                else:
                    return None
        else:
            # JPEG / HEIC / HEIF через Pillow (+pillow-heif)
            pil_img = Image.open(path)

        # JPEG можно декодировать сразу в уменьшенном виде (1/2, 1/4, 1/8).
        # Это в разы быстрее для миниатюр. Для полноразмерного просмотра (max_size
        # очень большой, >= 2000) draft не срабатывает и не меняет размер, поэтому
        # отключаем его, чтобы не сбросить формат у файла.
        if pil_img.format == "JPEG" and max_size < 2000:
            pil_img.draft("RGB", (max_size, max_size))

        # Учитываем поворот EXIF (ImageOps.exif_transpose читает тег Orientation)
        from PIL import ImageOps
        pil_img = ImageOps.exif_transpose(pil_img)

        if pil_img.mode not in ("RGB", "RGBA"):
            pil_img = pil_img.convert("RGB")

        pil_img.thumbnail((max_size, max_size), Image.LANCZOS)

        # Конвертируем PIL -> QImage
        data = pil_img.convert("RGB").tobytes("raw", "RGB")
        qimg = QImage(data, pil_img.width, pil_img.height, pil_img.width * 3,
                       QImage.Format_RGB888)
        return qimg.copy()  # copy() чтобы данные не потерялись при выходе из функции

    except Exception as e:
        print(f"[WARN] Не удалось извлечь превью для {path}: {e}")
        return None


# ------------------------------------------------------------------
# Фоновый поток сканирования + извлечения превью
# ------------------------------------------------------------------
def extract_preview_fullsize(path: str) -> QImage | None:
    """Встроенное превью в полном размере (для полноэкранного просмотра и зума)."""
    return extract_preview_qimage(path, max_size=10000)


class ThumbnailWorker(QObject):
    thumbnail_ready = Signal(str, QImage, float)  # path, image, elapsed_seconds
    finished = Signal(int, float)  # total_count, total_time
    error = Signal(str, str)  # path, error message

    def __init__(self, file_paths, thumb_size=200):
        super().__init__()
        self.file_paths = file_paths
        self.thumb_size = thumb_size
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def _make_one(self, path):
        """Готовит одну миниатюру (кэш или декодирование). Выполняется в пуле потоков."""
        if self._stop_requested:
            return path, None, 0.0, None
        t0 = time.time()
        try:
            qimg = thumb_cache.load(path, self.thumb_size)
            if qimg is None:
                qimg = extract_preview_qimage(path, max_size=self.thumb_size)
                if qimg is not None:
                    thumb_cache.save(path, self.thumb_size, qimg)
            return path, qimg, time.time() - t0, None
        except Exception as e:
            return path, None, time.time() - t0, str(e)

    def run(self):
        t_start = time.time()
        count = 0
        workers = max(2, min(THUMB_WORKERS, os.cpu_count() or 2))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # map сохраняет порядок файлов: миниатюры появляются слева направо, как раньше
            for path, qimg, elapsed, err in pool.map(self._make_one, self.file_paths):
                if self._stop_requested:
                    break
                if err is not None:
                    self.error.emit(path, err)
                elif qimg is not None:
                    self.thumbnail_ready.emit(path, qimg, elapsed)
                    count += 1
                else:
                    self.error.emit(path, "Не удалось извлечь превью")
            if self._stop_requested:
                pool.shutdown(wait=True, cancel_futures=True)
        total_elapsed = time.time() - t_start
        self.finished.emit(count, total_elapsed)


# ------------------------------------------------------------------
# Виджет одной миниатюры в сетке
# ------------------------------------------------------------------
class ThumbnailWidget(QFrame):
    """
    Миниатюра одного файла в сетке.

    Два независимых визуальных состояния:
    - is_marked    - фото помечено как "выбрано" (пишется в XMP) -> подсветка фона
    - is_selected  - ячейка выделена кликом мыши (как в проводнике) -> рамка
    """

    clicked = Signal(str, object)  # file_path, modifiers (Qt.KeyboardModifiers)
    double_clicked = Signal(str)

    def __init__(self, file_path: str, thumb_size=200, show_ext=True, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.is_marked = False
        self.is_selected = False

        self.setFixedSize(thumb_size + 20, thumb_size + 50)
        self.setFrameShape(QFrame.Box)
        self.setLineWidth(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self.image_label = QLabel()
        self.image_label.setFixedSize(thumb_size, thumb_size)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: #2b2b2b;")
        layout.addWidget(self.image_label, alignment=Qt.AlignCenter)

        name_text = Path(file_path).name if show_ext else Path(file_path).stem
        self.name_label = QLabel(name_text)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setWordWrap(True)
        font = QFont()
        font.setPointSize(pt(8))
        self.name_label.setFont(font)
        layout.addWidget(self.name_label)

        self.update_style()

    def set_image(self, qimg: QImage):
        pixmap = QPixmap.fromImage(qimg)
        self.image_label.setPixmap(pixmap)

    def set_loading_placeholder(self):
        self.image_label.setText("Загрузка...")
        self.image_label.setStyleSheet("background-color: #2b2b2b; color: #888;")

    def set_error_placeholder(self):
        self.image_label.setText("Ошибка\nпревью")
        self.image_label.setStyleSheet("background-color: #4a2b2b; color: #ddd;")

    def set_marked(self, marked: bool):
        self.is_marked = marked
        self.update_style()

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self.update_style()

    def update_style(self):
        bg = st.prefs.mark_color if self.is_marked else "transparent"
        border = "3px solid #ffb300" if self.is_selected else "1px solid #555"
        self.setStyleSheet(
            f"ThumbnailWidget {{ background-color: {bg}; border: {border}; }}"
        )

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not hasattr(self, "_press_pos") or self._press_pos is None:
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < 8:
            return
        self._press_pos = None
        drag = __import__("PySide6.QtGui", fromlist=["QDrag"]).QDrag(self)
        mime = __import__("PySide6.QtCore", fromlist=["QMimeData"]).QMimeData()
        urls = self._get_drag_urls()
        mime.setUrls([__import__("PySide6.QtCore", fromlist=["QUrl"]).QUrl.fromLocalFile(p) for p in urls])
        drag.setMimeData(mime)
        drag.exec(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.CopyAction)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and hasattr(self, "_press_pos") and self._press_pos:
            self.clicked.emit(self.file_path, event.modifiers())
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _get_drag_urls(self) -> list[str]:
        """Возвращает список файлов для перетаскивания. Если this выделен, берём всех выделенных, иначе только this."""
        parent = self.parent()
        while parent and not hasattr(parent, "selected_paths"):
            parent = parent.parent()
        if not parent:
            return [self.file_path]
        if self.file_path in parent.selected_paths:
            return list(parent.selected_paths)
        parent.selected_paths = [self.file_path]
        parent.last_clicked_path = self.file_path
        parent._refresh_selection_visuals()
        return [self.file_path]

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self.file_path)
        super().mouseDoubleClickEvent(event)


# ------------------------------------------------------------------
# Главное окно
# ------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1300, 850)

        self.current_folder = None
        self.thumbnail_widgets = {}  # path -> ThumbnailWidget
        self.worker_thread = None
        self.worker = None
        self._loaded_thumb_size = None
        self._loaded_show_ext = None
        self._current_cols = 0

        # Пересчёт сетки при изменении размера окна (с задержкой, чтобы не
        # перекладывать тысячи миниатюр на каждом пикселе перетаскивания)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(150)
        self._resize_timer.timeout.connect(self._on_resize_settled)

        # Все файлы папки (path -> ThumbnailWidget) и фильтры
        # thumbnail_widgets - ВСЕ загруженные миниатюры, видимость задаётся фильтром
        self.all_paths = []              # порядок файлов папки (по имени)
        self.hide_jpg = False            # скрывать JPG, если рядом есть RAW с тем же именем
        self._raw_stems = set()          # базовые имена всех RAW в папке
        self.filter_mode = "all"         # "all" | "picked" | "unpicked" | "list"
        self.list_stems = None           # set базовых имён для фильтра по списку

        # Мультивыделение: упорядоченный список путей + множество для быстрой проверки
        self.selected_paths = []       # сохраняет порядок кликов (для Shift+клик диапазона)
        self.last_clicked_path = None  # для Shift+клик диапазона

        # Проверка наличия exiftool при старте
        ok, msg = xm.check_exiftool_available()
        self._exiftool_ok = ok
        self._exiftool_msg = msg

        self._build_ui()
        st.prefs.changed.connect(self._on_settings_changed)

        if not ok:
            QMessageBox.warning(
                self, "ExifTool не найден",
                f"{msg}\n\nПометка файлов работать не будет, пока ExifTool "
                f"не будет доступен (положите ExifTool рядом с программой или установите его в систему)."
            )

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # Верхняя панель - папка
        top_bar = QHBoxLayout()
        self.btn_refresh = QPushButton("⟳ Обновить (F5)")
        self.btn_refresh.setToolTip("Перечитать папку: найти новые файлы и заново разложить сетку")
        self.btn_refresh.clicked.connect(self.refresh_folder)
        top_bar.addWidget(self.btn_refresh)

        self.btn_choose_folder = QPushButton("Выбрать папку...")
        self.btn_choose_folder.clicked.connect(self.choose_folder)
        top_bar.addWidget(self.btn_choose_folder)

        self.folder_label = QLabel("Папка не выбрана")
        self.folder_label.setStyleSheet("")
        top_bar.addWidget(self.folder_label)
        top_bar.addStretch()

        self.btn_settings = QPushButton("⚙ Настройки...")
        self.btn_settings.clicked.connect(self.open_settings)
        top_bar.addWidget(self.btn_settings)
        main_layout.addLayout(top_bar)

        # Панель фильтров (два взаимоисключающих режима)
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("Фильтр по пометке:"))
        self.btn_filter_all = QPushButton("Все")
        self.btn_filter_picked = QPushButton("Выбранные")
        self.btn_filter_unpicked = QPushButton("Невыбранные")
        for btn, mode in ((self.btn_filter_all, "all"),
                          (self.btn_filter_picked, "picked"),
                          (self.btn_filter_unpicked, "unpicked")):
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, m=mode: self.set_pick_filter(m))
            filter_bar.addWidget(btn)

        filter_bar.addSpacing(20)
        filter_bar.addWidget(QLabel("Фильтр по списку:"))
        self.btn_list_clipboard = QPushButton("Из буфера")
        self.btn_list_clipboard.clicked.connect(self.load_list_from_clipboard)
        filter_bar.addWidget(self.btn_list_clipboard)

        self.btn_list_file = QPushButton("Из CSV-файла...")
        self.btn_list_file.clicked.connect(self.load_list_from_file)
        filter_bar.addWidget(self.btn_list_file)

        filter_bar.addSpacing(20)
        self.btn_hide_jpg = QPushButton("Скрыть JPG")
        self.btn_hide_jpg.setCheckable(True)
        self.btn_hide_jpg.setToolTip(
            "Скрывает JPG/JPEG, если рядом лежит RAW с тем же именем (RAW+JPG съёмка)"
        )
        self.btn_hide_jpg.clicked.connect(self.toggle_hide_jpg)
        filter_bar.addWidget(self.btn_hide_jpg)

        self.filter_status_label = QLabel("")
        self.filter_status_label.setStyleSheet("")
        filter_bar.addWidget(self.filter_status_label)
        filter_bar.addStretch()
        main_layout.addLayout(filter_bar)

        # Панель массовых действий
        actions_bar = QHBoxLayout()

        self.btn_mark = QPushButton()
        self.btn_mark.clicked.connect(lambda: self.bulk_set_pick(True))
        actions_bar.addWidget(self.btn_mark)

        self.btn_unmark = QPushButton()
        self.btn_unmark.clicked.connect(lambda: self.bulk_set_pick(False))
        actions_bar.addWidget(self.btn_unmark)

        actions_bar.addSpacing(20)

        self.btn_select_all = QPushButton()
        self.btn_select_all.clicked.connect(self.select_all)
        actions_bar.addWidget(self.btn_select_all)

        self.btn_clear_selection = QPushButton()
        self.btn_clear_selection.clicked.connect(self.clear_selection)
        actions_bar.addWidget(self.btn_clear_selection)

        actions_bar.addSpacing(20)

        self.selection_label = QLabel("Выделено: 0")
        self.selection_label.setStyleSheet("")
        actions_bar.addWidget(self.selection_label)

        actions_bar.addStretch()
        main_layout.addLayout(actions_bar)

        # Глобальные горячие клавиши (работают независимо от того, какой
        # дочерний виджет сейчас в фокусе - в отличие от keyPressEvent)
        self._shortcuts = []
        self._rebuild_shortcuts()
        self._update_button_labels()

        # Прокручиваемая область с сеткой миниатюр
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(8)
        self.scroll_area.setWidget(self.grid_container)
        main_layout.addWidget(self.scroll_area)

        # Статус-бар
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Готово. Выберите папку с фотографиями.")

    def open_settings(self):
        SettingsDialog(self).exec()

    def _on_settings_changed(self):
        """Перестраиваем шорткаты, обновляем подписи, применяем тему и перерисовываем."""
        self._rebuild_shortcuts()
        self._update_button_labels()
        self._apply_theme()
        self._refresh_thumbnails_if_needed()
        self.update()

    def _rebuild_shortcuts(self):
        for s in self._shortcuts:
            s.setParent(None)
        self._shortcuts.clear()
        for key in st.prefs.keys("select_all"):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(self.select_all)
            self._shortcuts.append(sc)
        for key in st.prefs.keys("clear_selection"):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(self.clear_selection)
            self._shortcuts.append(sc)
        sc = QShortcut(QKeySequence("F5"), self)
        sc.activated.connect(self.refresh_folder)
        self._shortcuts.append(sc)

    def _update_button_labels(self):
        p = st.prefs.key_hint("pick")
        self.btn_mark.setText(f"✓ Пометить выбранные ({p})" if p else "✓ Пометить выбранные")
        self.btn_unmark.setText(f"✗ Снять пометку ({p})" if p else "✗ Снять пометку")
        sa = st.prefs.key_hint("select_all")
        self.btn_select_all.setText(f"Выделить все ({sa})" if sa else "Выделить все")
        cs = st.prefs.key_hint("clear_selection")
        self.btn_clear_selection.setText(f"Снять выделение ({cs})" if cs else "Снять выделение")

    def _apply_theme(self):
        from PySide6.QtWidgets import QApplication
        st.apply_theme(QApplication.instance(), st.prefs.bg_color)

    def _refresh_thumbnails_if_needed(self):
        """Размер миниатюр или расширение изменились - перечитываем папку, иначе обновляем цвета."""
        if self.current_folder and (
            st.prefs.thumb_size != self._loaded_thumb_size
            or st.prefs.show_ext != self._loaded_show_ext
        ):
            self.load_folder(self.current_folder)
        else:
            for w in self.thumbnail_widgets.values():
                w.update_style()

    def refresh_folder(self):
        """F5 / кнопка: перечитать текущую папку (новые файлы + пересчёт колонок)."""
        if not self.current_folder:
            self.status_bar.showMessage("Сначала выберите папку.")
            return
        self.load_folder(self.current_folder)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку с фотографиями")
        if folder:
            self.load_folder(folder)

    def _stop_scan(self):
        """Останавливает фоновую загрузку миниатюр, если она ещё идёт."""
        worker, thread = self.worker, self.worker_thread
        if worker is None:
            return
        for sig in (worker.thumbnail_ready, worker.error, worker.finished):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        worker.stop()
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(5000)
        self.worker = None
        self.worker_thread = None

    def load_folder(self, folder: str):
        self._stop_scan()
        self.current_folder = folder
        self.folder_label.setText(folder)
        if st.prefs.remember_folder:
            st.prefs.last_folder = folder
        self.selected_paths.clear()
        self.last_clicked_path = None
        self._update_selection_label()

        # Очистка текущей сетки
        for widget in self.thumbnail_widgets.values():
            widget.setParent(None)
        self.thumbnail_widgets.clear()
        self.all_paths = []
        self.hide_jpg = False
        self._raw_stems = set()
        self.btn_hide_jpg.setChecked(False)
        self.filter_mode = "all"
        self.list_stems = None
        self.btn_filter_all.setChecked(True)
        self.btn_filter_picked.setChecked(False)
        self.btn_filter_unpicked.setChecked(False)
        self.filter_status_label.setText("")

        # Сканирование файлов поддерживаемых форматов
        try:
            all_files = sorted(os.listdir(folder))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть папку:\n{e}")
            return

        photo_files = [
            os.path.join(folder, f) for f in all_files
            if Path(f).suffix.lower() in ALL_EXTENSIONS
            and not f.startswith("._")  # служебные файлы macOS на флешках
        ]

        if not photo_files:
            self.status_bar.showMessage("В выбранной папке не найдено поддерживаемых фото.")
            return

        self.status_bar.showMessage(
            f"Найдено файлов: {len(photo_files)}. Чтение пометок и извлечение превью..."
        )

        # Пакетное чтение статусов пометки (один проход exiftool по всем sidecar)
        pick_statuses = {}
        if self._exiftool_ok:
            try:
                pick_statuses = xm.read_pick_status_batch(photo_files)
            except Exception as e:
                print(f"[WARN] Не удалось прочитать статусы пометки: {e}")

        # Создаём placeholder-виджеты сразу (чтобы видеть сетку мгновенно)
        thumb_size = st.prefs.thumb_size
        show_ext = st.prefs.show_ext
        cols = self._grid_cols(thumb_size)
        self._current_cols = cols
        self.all_paths = list(photo_files)
        self._raw_stems = {
            flt.file_stem(p) for p in photo_files
            if Path(p).suffix.lower() in RAW_EXTENSIONS
        }
        for idx, path in enumerate(photo_files):
            widget = ThumbnailWidget(path, thumb_size=thumb_size, show_ext=show_ext)
            widget.set_loading_placeholder()
            widget.set_marked(pick_statuses.get(path, False))
            widget.clicked.connect(self.on_thumbnail_clicked)
            widget.double_clicked.connect(self.on_thumbnail_double_clicked)
            row, col = divmod(idx, cols)
            self.grid_layout.addWidget(widget, row, col)
            self.thumbnail_widgets[path] = widget

        # Запуск фоновой загрузки превью, чтобы не блокировать интерфейс
        self.worker_thread = QThread()
        self.worker = ThumbnailWorker(photo_files, thumb_size)
        self._loaded_thumb_size = thumb_size
        self._loaded_show_ext = st.prefs.show_ext
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.thumbnail_ready.connect(self.on_thumbnail_ready)
        self.worker.error.connect(self.on_thumbnail_error)
        self.worker.finished.connect(self.on_scan_finished)
        self.worker.finished.connect(self.worker_thread.quit)

        self.worker_thread.start()

    def on_thumbnail_ready(self, path, qimg, elapsed):
        widget = self.thumbnail_widgets.get(path)
        if widget:
            widget.set_image(qimg)

    def on_thumbnail_error(self, path, message):
        widget = self.thumbnail_widgets.get(path)
        if widget:
            widget.set_error_placeholder()
        print(f"[ERROR] {path}: {message}")

    def on_scan_finished(self, count, total_time):
        avg = (total_time / count) if count else 0
        marked_count = sum(1 for w in self.thumbnail_widgets.values() if w.is_marked)
        self.status_bar.showMessage(
            f"Загружено превью: {count} файлов за {total_time:.2f} сек "
            f"(в среднем {avg*1000:.1f} мс/файл). Помечено: {marked_count}"
        )

    # ----------------------------------------------------------------
    # Мультивыделение (поведение как в проводнике)
    # ----------------------------------------------------------------
    def on_thumbnail_clicked(self, path, modifiers):
        ctrl = bool(modifiers & Qt.ControlModifier)
        shift = bool(modifiers & Qt.ShiftModifier)

        ordered_paths = [p for p in self.all_paths if self._is_visible(p)]

        if shift and self.last_clicked_path in ordered_paths:
            # Выделяем диапазон между last_clicked_path и path
            i1 = ordered_paths.index(self.last_clicked_path)
            i2 = ordered_paths.index(path)
            lo, hi = sorted((i1, i2))
            range_paths = ordered_paths[lo:hi + 1]
            if not ctrl:
                self.selected_paths = []
            for p in range_paths:
                if p not in self.selected_paths:
                    self.selected_paths.append(p)
        elif ctrl:
            # Добавить/убрать из выделения
            if path in self.selected_paths:
                self.selected_paths.remove(path)
            else:
                self.selected_paths.append(path)
            self.last_clicked_path = path
        else:
            # Обычный клик - сброс выделения, выделяем только этот файл
            self.selected_paths = [path]
            self.last_clicked_path = path

        self._refresh_selection_visuals()

    def _refresh_selection_visuals(self):
        selected_set = set(self.selected_paths)
        for path, widget in self.thumbnail_widgets.items():
            widget.set_selected(path in selected_set)
        self._update_selection_label()

    def _update_selection_label(self):
        self.selection_label.setText(f"Выделено: {len(self.selected_paths)}")

    def select_all(self):
        # Выделяем только видимые (отфильтрованные) файлы
        self.selected_paths = [p for p in self.all_paths if self._is_visible(p)]
        self.last_clicked_path = self.selected_paths[-1] if self.selected_paths else None
        self._refresh_selection_visuals()

    def clear_selection(self):
        self.selected_paths = []
        self.last_clicked_path = None
        self._refresh_selection_visuals()

    # ----------------------------------------------------------------
    # Фильтрация (два взаимоисключающих режима)
    # ----------------------------------------------------------------
    def _is_visible(self, path: str) -> bool:
        widget = self.thumbnail_widgets.get(path)
        if widget is None:
            return False
        if self.hide_jpg and self._is_hidden_jpg(path):
            return False
        if self.filter_mode == "picked":
            return widget.is_marked
        if self.filter_mode == "unpicked":
            return not widget.is_marked
        if self.filter_mode == "list":
            return flt.file_stem(path) in (self.list_stems or set())
        return True  # "all"

    def _is_hidden_jpg(self, path: str) -> bool:
        """JPG/JPEG, для которого в папке есть RAW с тем же базовым именем."""
        ext = Path(path).suffix.lower()
        return ext in (".jpg", ".jpeg") and flt.file_stem(path) in self._raw_stems

    def toggle_hide_jpg(self, checked: bool):
        """Кнопка «Скрыть JPG»: работает поверх текущего фильтра."""
        self.hide_jpg = checked
        self._apply_filter()
        hidden = sum(1 for p in self.all_paths if self._is_hidden_jpg(p))
        self.status_bar.showMessage(
            f"Скрыто JPG-дубликатов RAW: {hidden}" if checked else "JPG показаны"
        )

    def set_pick_filter(self, mode: str):
        """Фильтр по пометке: all / picked / unpicked. Отключает фильтр по списку."""
        self.filter_mode = mode
        self.list_stems = None
        self.btn_filter_all.setChecked(mode == "all")
        self.btn_filter_picked.setChecked(mode == "picked")
        self.btn_filter_unpicked.setChecked(mode == "unpicked")
        self._apply_filter()

    def _apply_list_filter(self, stems: set[str], source_label: str):
        """Фильтр по списку. Отключает фильтр по пометке (режимы не пересекаются)."""
        self.filter_mode = "list"
        self.list_stems = stems
        self.btn_filter_all.setChecked(False)
        self.btn_filter_picked.setChecked(False)
        self.btn_filter_unpicked.setChecked(False)
        self._apply_filter()
        found = sum(1 for p in self.all_paths if flt.file_stem(p) in stems)
        self.filter_status_label.setText(
            f"Список ({source_label}): {len(stems)} имён, найдено в папке: {found}"
        )

    def _apply_filter(self):
        """Скрывает/показывает миниатюры по текущему фильтру и сбрасывает выделение скрытых."""
        visible_count = 0
        for path, widget in self.thumbnail_widgets.items():
            visible = self._is_visible(path)
            widget.setVisible(visible)
            if visible:
                visible_count += 1

        # Выделение скрытых файлов не имеет смысла - снимаем
        self.selected_paths = [p for p in self.selected_paths if self._is_visible(p)]
        self._refresh_selection_visuals()
        self._relayout_visible()

        if self.filter_mode in ("all", "picked", "unpicked"):
            self.filter_status_label.setText("" if self.filter_mode == "all"
                                             else f"Показано: {visible_count}")
        self.status_bar.showMessage(f"Показано файлов: {visible_count} из {len(self.all_paths)}")

    def _grid_cols(self, thumb_size=None) -> int:
        """Сколько колонок помещается по ширине окна."""
        if thumb_size is None:
            thumb_size = st.prefs.thumb_size
        return max(1, self.width() // (thumb_size + 40))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.all_paths:
            self._resize_timer.start()

    def _on_resize_settled(self):
        """Окно перестали растягивать: перекладываем сетку, только если число колонок изменилось."""
        if not self.all_paths or self._grid_cols() == self._current_cols:
            return
        bar = self.scroll_area.verticalScrollBar()
        # запоминаем, какая миниатюра была вверху, чтобы остаться на ней
        anchor = None
        top = self.scroll_area.viewport().rect().top()
        for path in self.all_paths:
            w = self.thumbnail_widgets.get(path)
            if w is not None and w.isVisible() and w.mapTo(self.grid_container, w.rect().topLeft()).y() + w.height() > bar.value():
                anchor = w
                break
        self._relayout_visible()
        if anchor is not None:
            QTimer.singleShot(0, lambda: self.scroll_area.ensureWidgetVisible(anchor, 0, 0))

    def _relayout_visible(self):
        """Перераскладывает видимые миниатюры сеткой без пустых мест."""
        cols = self._grid_cols()
        self._current_cols = cols
        # Убираем все виджеты из сетки, потом добавляем только видимые по порядку
        for path in self.all_paths:
            widget = self.thumbnail_widgets.get(path)
            if widget is not None:
                self.grid_layout.removeWidget(widget)
        idx = 0
        for path in self.all_paths:
            widget = self.thumbnail_widgets.get(path)
            if widget is None or not self._is_visible(path):
                continue
            row, col = divmod(idx, cols)
            self.grid_layout.addWidget(widget, row, col)
            idx += 1

    # ----------------------------------------------------------------
    # Фильтр по списку (буфер обмена / CSV)
    # ----------------------------------------------------------------
    def load_list_from_clipboard(self):
        if not self.all_paths:
            self.status_bar.showMessage("Сначала выберите папку с фотографиями.")
            return
        text = QApplication.clipboard().text()
        stems = flt.parse_clipboard_text(text)
        if not stems:
            QMessageBox.information(self, "Список пуст",
                                    "В буфере обмена не найдено имён файлов.\n"
                                    "Ожидается формат: ALL_1249 ALL_1252 ALL_1265")
            return
        self._apply_list_filter(stems, "буфер обмена")

    def load_list_from_file(self):
        if not self.all_paths:
            self.status_bar.showMessage("Сначала выберите папку с фотографиями.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите CSV-файл со списком", "", "CSV (*.csv);;Все файлы (*)"
        )
        if not path:
            return
        try:
            stems = flt.parse_csv_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка чтения CSV", str(e))
            return
        if not stems:
            QMessageBox.information(self, "Список пуст",
                                    "В файле не найдено имён в колонке File.")
            return
        self._apply_list_filter(stems, Path(path).name)

    def on_thumbnail_double_clicked(self, path):
        self.open_viewer(path)

    def open_viewer(self, path=None):
        """Открывает полноэкранный просмотр по видимым файлам (порядок сетки)."""
        visible = [p for p in self.all_paths if self._is_visible(p)]
        if not visible:
            self.status_bar.showMessage("Нет файлов для просмотра.")
            return
        if path is None:
            path = self.selected_paths[0] if self.selected_paths else visible[0]
        if path not in visible:
            path = visible[0]
        index = visible.index(path)

        self.viewer = FullscreenViewer(
            paths=visible,
            index=index,
            load_fn=extract_preview_fullsize,
            is_marked_fn=lambda p: self.thumbnail_widgets[p].is_marked,
            toggle_fn=self._viewer_toggle_pick,
        )
        self.viewer.closed.connect(self._on_viewer_closed)

    def _viewer_toggle_pick(self, path):
        """Переключает пометку одного файла из просмотра и обновляет сетку."""
        new_state = not self.thumbnail_widgets[path].is_marked
        if xm.set_pick_status(path, new_state):
            self._sync_pair_marks([path], new_state)
        else:
            self.status_bar.showMessage(f"Не удалось записать пометку: {Path(path).name}")
        return self.thumbnail_widgets[path].is_marked

    def _on_viewer_closed(self, path):
        # Выделяем последний просмотренный файл в сетке и возвращаем фокус окну
        self.select_single(path)
        self.activateWindow()
        self.setFocus()

    def select_single(self, path):
        self.selected_paths = [path]
        self.last_clicked_path = path
        self._refresh_selection_visuals()
        widget = self.thumbnail_widgets.get(path)
        if widget is not None:
            self.scroll_area.ensureWidgetVisible(widget)

    # ----------------------------------------------------------------
    # Массовая пометка / снятие пометки
    # ----------------------------------------------------------------
    def _sync_pair_marks(self, paths, picked: bool):
        """Обновляет подсветку для всех файлов, делящих sidecar с помеченными.
        Пример: ALL_1249.CR2 и ALL_1249.jpg используют один ALL_1249.xmp."""
        sidecars = {os.path.splitext(p)[0] for p in paths}
        for p, widget in self.thumbnail_widgets.items():
            if os.path.splitext(p)[0] in sidecars:
                widget.set_marked(picked)

    def bulk_set_pick(self, picked: bool):
        if not self._exiftool_ok:
            QMessageBox.warning(self, "ExifTool недоступен", self._exiftool_msg)
            return

        if not self.selected_paths:
            self.status_bar.showMessage("Нет выделенных файлов для пометки.")
            return

        self.status_bar.showMessage(
            f"{'Помечаю' if picked else 'Снимаю пометку'}: {len(self.selected_paths)} файлов..."
        )
        QApplication.processEvents()

        success_count, failed = xm.set_pick_status_batch(self.selected_paths, picked)

        ok_paths = [p for p in self.selected_paths if p not in failed]
        self._sync_pair_marks(ok_paths, picked)

        msg = f"{'Помечено' if picked else 'Снята пометка'}: {success_count} из {len(self.selected_paths)}"
        if failed:
            msg += f". Ошибки: {len(failed)}"
        self.status_bar.showMessage(msg)

    def keyPressEvent(self, event):
        # Enter - открыть полноэкранный просмотр выделенного файла
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.selected_paths:
            self.open_viewer(self.selected_paths[0])
        elif st.prefs.matches(event, "pick") and self.selected_paths:
            self._toggle_pick_selected()
        else:
            super().keyPressEvent(event)

    def _toggle_pick_selected(self):
        """Toggle: если ПЕРВЫЙ выделенный помечен - снимаем со всех, иначе помечаем все."""
        if not self.selected_paths:
            return
        first_widget = self.thumbnail_widgets.get(self.selected_paths[0])
        currently_marked = first_widget.is_marked if first_widget else False
        self.bulk_set_pick(not currently_marked)


    def resizeEvent(self, event):
        """При изменении размера окна пересчитываем сетку, если папка открыта."""
        super().resizeEvent(event)
        if self.current_folder and self.thumbnail_widgets:
            self._relayout_visible()

    def closeEvent(self, event):
        """Перед закрытием гасим фоновую загрузку миниатюр.
        Иначе QThread уничтожается на ходу - это одна из причин падения при выходе."""
        self._stop_scan()
        self._resize_timer.stop()
        super().closeEvent(event)

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    from PySide6.QtGui import QIcon
    icon_name = "icon.icns" if IS_MAC else "icon.ico"
    icon_file = resource_path("assets", icon_name)
    if not icon_file.exists():
        icon_file = resource_path("assets", "icon.ico")
    app.setWindowIcon(QIcon(str(icon_file)))
    st.apply_theme(app, st.prefs.bg_color)
    window = MainWindow()
    window.show()
    if st.prefs.remember_folder and st.prefs.last_folder and Path(st.prefs.last_folder).is_dir():
        window.load_folder(st.prefs.last_folder)

    code = app.exec()

    # Аккуратное завершение: удаляем окно, пока QApplication ещё жив.
    # Без этого PySide6 на macOS падает в самом конце работы (SIGSEGV в ~QObject).
    window.close()
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    return code


if __name__ == "__main__":
    _code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # Пропускаем финализацию Python: она и рушит Qt на выходе. Данные уже сохранены.
    os._exit(_code)
