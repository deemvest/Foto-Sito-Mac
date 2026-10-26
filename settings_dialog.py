# -*- coding: utf-8 -*-
"""
RawCull - окно настроек. Открывается из главного окна и из полноэкранного просмотра.

Вкладки:
- Горячие клавиши: листание (3 поля вперёд, 3 назад, колесо), остальные действия
- Внешний вид: размер миниатюр, цвет фона, цвет пометки, расширение, последняя папка
- О программе: название, сайт, обсуждение, логотип (assets/logo.png, если есть)

Конфликт клавиш (одна клавиша на два действия) не даёт сохранить настройки.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QColor, QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QSpinBox, QCheckBox, QPushButton,
    QKeySequenceEdit, QColorDialog, QMessageBox, QGroupBox, QGridLayout
)

import settings as st
from platform_utils import resource_path
from version import APP_NAME, APP_VERSION

LOGO_PATH = resource_path("assets", "logo.png")
LOGO_SIZE = 120       # 3/4 от прежних 160
EXIFTOOL_URL = "https://exiftool.org"


class KeyEdit(QKeySequenceEdit):
    """Поле ввода клавиши. Запоминает первое сочетание и сообщает о конфликте."""

    def __init__(self, sequence_text: str, parent=None):
        super().__init__(parent)
        try:
            self.setMaximumSequenceLength(1)
        except AttributeError:
            pass
        self.setKeySequence(QKeySequence(sequence_text))
        self.setClearButtonEnabled(True)
        self.keySequenceChanged.connect(self._squeeze)

    def _squeeze(self, seq: QKeySequence):
        """Оставляем только первое сочетание: 'Ctrl+A, 1' -> 'Ctrl+A'."""
        if seq.count() > 1:
            self.blockSignals(True)
            self.setKeySequence(QKeySequence(seq[0]))
            self.blockSignals(False)

    def text(self) -> str:
        return self.keySequence().toString(QKeySequence.PortableText)


class SettingsDialog(QDialog):
    def __init__(self, parent=None, viewer=False):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(520)
        self._viewer = viewer

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_keys_tab(), "Горячие клавиши")
        tabs.addTab(self._build_view_tab(), "Внешний вид")
        tabs.addTab(self._build_about_tab(), "О программе")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._reset)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Вкладка: горячие клавиши
    # ------------------------------------------------------------------
    def _build_keys_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        nav_box = QGroupBox("Листание в просмотре")
        nav_layout = QGridLayout(nav_box)
        nav_layout.addWidget(QLabel("Назад:"), 0, 0)
        self.nav_prev = [KeyEdit(k) for k in (st.prefs.keys("prev") + ["", ""])[:3]]
        for i, e in enumerate(self.nav_prev):
            nav_layout.addWidget(e, 0, i + 1)
        nav_layout.addWidget(QLabel("Вперёд:"), 1, 0)
        self.nav_next = [KeyEdit(k) for k in (st.prefs.keys("next") + ["", ""])[:3]]
        for i, e in enumerate(self.nav_next):
            nav_layout.addWidget(e, 1, i + 1)
        self.chk_wheel = QCheckBox("Листать колесом мыши")
        self.chk_wheel.setChecked(st.prefs.wheel_nav)
        nav_layout.addWidget(self.chk_wheel, 2, 0, 1, 4)
        outer.addWidget(nav_box)

        keys_box = QGroupBox("Клавиши действий")
        form = QFormLayout(keys_box)
        self.key_edits: dict[str, KeyEdit] = {}
        for action, (label, where) in st.ACTIONS.items():
            if action in ("next", "prev"):
                continue
            edit = KeyEdit(st.prefs.keys(action)[0] if st.prefs.keys(action) else "")
            self.key_edits[action] = edit
            form.addRow(f"{label} ({where})", edit)
        outer.addWidget(keys_box)
        outer.addStretch()
        return page

    # ------------------------------------------------------------------
    # Вкладка: внешний вид
    # ------------------------------------------------------------------
    def _build_view_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.spin_thumb = QSpinBox()
        self.spin_thumb.setRange(100, 400)
        self.spin_thumb.setSingleStep(20)
        self.spin_thumb.setSuffix(" px")
        self.spin_thumb.setValue(st.prefs.thumb_size)
        form.addRow("Размер миниатюр", self.spin_thumb)

        color_bg = QHBoxLayout()
        self.btn_bg = QPushButton()
        self.btn_bg.setFixedWidth(120)
        self._bg = st.prefs.bg_color
        self._update_bg_button()
        self.btn_bg.clicked.connect(self._pick_bg)
        color_bg.addWidget(self.btn_bg)
        btn_reset = QPushButton("Сбросить")
        btn_reset.clicked.connect(lambda: self._set_bg("#f0f0f0"))
        color_bg.addWidget(btn_reset)
        color_bg.addStretch()
        form.addRow("Цвет фона", color_bg)

        self.chk_folder = QCheckBox("При запуске открывать последнюю папку")
        self.chk_folder.setChecked(st.prefs.remember_folder)
        form.addRow("", self.chk_folder)

        self.chk_ext = QCheckBox("Показывать расширение под миниатюрой")
        self.chk_ext.setChecked(st.prefs.show_ext)
        form.addRow("", self.chk_ext)

        color_row = QHBoxLayout()
        self.btn_color = QPushButton()
        self.btn_color.setFixedWidth(120)
        self._color = st.prefs.mark_color
        self._update_color_button()
        self.btn_color.clicked.connect(self._pick_color)
        color_row.addWidget(self.btn_color)
        color_row.addStretch()
        form.addRow("Цвет подсветки пометки", color_row)
        return page

    # ------------------------------------------------------------------
    # Вкладка: о программе
    # ------------------------------------------------------------------
    def _build_about_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        if LOGO_PATH.exists():
            logo = QLabel()
            logo.setAlignment(Qt.AlignCenter)
            pix = QPixmap(str(LOGO_PATH))
            logo.setPixmap(pix.scaled(LOGO_SIZE, LOGO_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            outer.addWidget(logo)

        title = QLabel(f"Программа {APP_NAME} v{APP_VERSION}")
        title.setAlignment(Qt.AlignCenter)
        title_font = title.font()
        title_font.setPointSize(title_font.pointSize() + 4)
        title_font.setBold(True)
        title.setFont(title_font)
        outer.addWidget(title)

        subtitle = QLabel("вайб-код проект deemvest")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: gray;")
        outer.addWidget(subtitle)

        outer.addSpacing(16)

        links = QLabel(
            'Сайт: <a href="https://deemvest.ru/foto_sito">https://deemvest.ru/foto_sito</a><br>'
            'Обсуждение проекта: <a href="https://t.me/foto_sito_deemvest">https://t.me/foto_sito_deemvest</a>'
        )
        links.setAlignment(Qt.AlignCenter)
        links.setOpenExternalLinks(True)
        links.setTextInteractionFlags(Qt.TextBrowserInteraction)
        outer.addWidget(links)

        outer.addSpacing(20)

        credit = QLabel(
            f'Метаданные: <a href="{EXIFTOOL_URL}">ExifTool © Phil Harvey</a>'
        )
        credit.setAlignment(Qt.AlignCenter)
        credit.setOpenExternalLinks(True)
        credit.setTextInteractionFlags(Qt.TextBrowserInteraction)
        credit.setStyleSheet("color: gray;")
        outer.addWidget(credit)

        outer.addStretch()
        return page

    # ------------------------------------------------------------------
    # Цвета
    # ------------------------------------------------------------------
    def _update_bg_button(self):
        self.btn_bg.setText(self._bg)
        text_c = "#fff" if QColor(self._bg).lightness() < 128 else "#000"
        self.btn_bg.setStyleSheet(f"background-color: {self._bg}; color: {text_c}; padding: 4px;")

    def _pick_bg(self):
        chosen = QColorDialog.getColor(QColor(self._bg), self, "Цвет фона")
        if chosen.isValid():
            self._set_bg(chosen.name())

    def _set_bg(self, c: str):
        self._bg = c
        self._update_bg_button()

    def _update_color_button(self):
        self.btn_color.setText(self._color)
        self.btn_color.setStyleSheet(
            f"background-color: {self._color}; color: white; padding: 4px;"
        )

    def _pick_color(self):
        chosen = QColorDialog.getColor(QColor(self._color), self, "Цвет пометки")
        if chosen.isValid():
            self._color = chosen.name()
            self._update_color_button()

    # ------------------------------------------------------------------
    # Сохранение
    # ------------------------------------------------------------------
    def _collect(self):
        """Собирает значения и проверяет конфликты. None - есть ошибка."""
        new_keys = {
            "prev": [e.text() for e in self.nav_prev if e.text()],
            "next": [e.text() for e in self.nav_next if e.text()],
        }
        for action, edit in self.key_edits.items():
            text = edit.text()
            new_keys[action] = [text] if text else []

        seen: dict[str, str] = {}
        for action, keys in new_keys.items():
            for k in keys:
                if k in seen:
                    a1 = st.ACTIONS.get(seen[k], (seen[k],))[0]
                    a2 = st.ACTIONS.get(action, (action,))[0]
                    QMessageBox.warning(
                        self, "Конфликт клавиш",
                        f"Клавиша «{k}» назначена дважды:\n{a1}\n{a2}\n\n"
                        f"Назначьте разные клавиши."
                    )
                    return None
                seen[k] = action

        ui = {
            "thumb_size": self.spin_thumb.value(),
            "bg_color": self._bg,
            "mark_color": self._color,
            "show_ext": self.chk_ext.isChecked(),
            "wheel_nav": self.chk_wheel.isChecked(),
            "remember_folder": self.chk_folder.isChecked(),
        }
        return new_keys, ui

    def _save(self):
        collected = self._collect()
        if collected is None:
            return
        new_keys, ui = collected

        for action, keys in new_keys.items():
            st.prefs.set_keys(action, keys)
        st.prefs.thumb_size = ui["thumb_size"]
        st.prefs.bg_color = ui["bg_color"]
        st.prefs.mark_color = ui["mark_color"]
        st.prefs.show_ext = ui["show_ext"]
        st.prefs.wheel_nav = ui["wheel_nav"]
        st.prefs.remember_folder = ui["remember_folder"]

        st.prefs.changed.emit()
        self.accept()

    def _reset(self):
        if QMessageBox.question(
            self, "Сброс настроек",
            "Вернуть все настройки к значениям по умолчанию?"
        ) != QMessageBox.Yes:
            return
        st.prefs.reset_all()
        st.prefs.changed.emit()
        QMessageBox.information(self, "Готово", "Настройки сброшены.")
        self.accept()
