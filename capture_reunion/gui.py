"""Interface graphique (PySide6)."""

from __future__ import annotations

import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, QSettings, QSize, Qt, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRubberBand,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .session import Session
from .slides import DetectorSettings, Zone, format_timestamp
from .windows import (
    IS_WINDOWS,
    MonitorInfo,
    WindowInfo,
    is_minimized,
    list_monitors,
    list_windows,
    window_exists,
)

Q = Qt.ConnectionType.QueuedConnection
DEFAULT_DIR = Path.home() / "Videos" / "Capture reunion"
LEGAL = (
    "Rappel : prévenez les participants avant d'enregistrer (RGPD). "
    "En RCP, les données patients imposent un stockage sécurisé."
)


def to_pixmap(image: np.ndarray) -> QPixmap:
    rgb = cv2.cvtColor(np.ascontiguousarray(image[:, :, :3]), cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


def read_image(path: Path) -> Optional[np.ndarray]:
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def open_folder(path: Path) -> None:
    if IS_WINDOWS:
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class Task(QObject):
    """Exécute une fonction dans un fil séparé et renvoie le résultat dans l'interface."""

    done = Signal(object)
    failed = Signal(str)
    progress = Signal(float)

    def __init__(self, fn: Callable, *args, **kwargs) -> None:
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(str(exc))


class Bridge(QObject):
    """Relaie vers l'interface les événements venant des fils de capture."""

    slide = Signal(str)
    window_closed = Signal()


class ZoneDialog(QDialog):
    """Sélection à la souris de la zone où s'affichent les diapos."""

    def __init__(self, image: np.ndarray, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Zone des diapos — tracez un rectangle autour de la diapo")
        self._label = QLabel()
        pix = to_pixmap(image)
        self._pix = pix.scaled(QSize(1100, 650), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        self._label.setPixmap(self._pix)
        self._label.setFixedSize(self._pix.size())
        self._label.mousePressEvent = self._press
        self._label.mouseMoveEvent = self._move
        self._label.mouseReleaseEvent = lambda e: None
        self._band = QRubberBand(QRubberBand.Shape.Rectangle, self._label)
        self._origin = QPoint()
        self._rect = QRect()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Cliquez-glissez pour entourer la diapo (sans les vignettes vidéo)."))
        layout.addWidget(self._label)
        layout.addWidget(buttons)

    def _press(self, event) -> None:
        self._origin = event.position().toPoint()
        self._band.setGeometry(QRect(self._origin, QSize()))
        self._band.show()

    def _move(self, event) -> None:
        self._rect = QRect(self._origin, event.position().toPoint()).normalized()
        self._rect = self._rect.intersected(self._label.rect())
        self._band.setGeometry(self._rect)

    def zone(self) -> Optional[Zone]:
        if self._rect.width() < 10 or self._rect.height() < 10:
            return None
        w, h = self._pix.width(), self._pix.height()
        r = self._rect
        return (r.x() / w, r.y() / h, r.width() / w, r.height() / h)


def zone_text(zone: Optional[Zone]) -> str:
    if not zone:
        return "Toute la fenêtre"
    x, y, w, h = zone
    return f"Zone : {w * 100:.0f} % × {h * 100:.0f} % de la fenêtre"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Capture réunion {__version__}")
        self.settings = QSettings("CaptureReunion", "CaptureReunion")
        self.recorder = None
        self.record_zone: Optional[Zone] = None
        self.session: Optional[Session] = None
        self.bridge = Bridge()
        self.bridge.slide.connect(self._on_slide)
        self.bridge.window_closed.connect(self._on_window_closed)
        self._tasks: list[Task] = []

        tabs = QTabWidget()
        tabs.addTab(self._build_record_tab(), "1. Enregistrer")
        tabs.addTab(self._build_after_tab(), "2. Diapos et compte-rendu")
        self.tabs = tabs
        self.setCentralWidget(tabs)
        self.resize(780, 880)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.refresh_windows()

    # ================================================================ onglet 1
    def _build_record_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        win_box = QGroupBox("Fenêtre ou écran à enregistrer")
        wl = QVBoxLayout(win_box)
        row = QHBoxLayout()
        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(420)
        refresh = QPushButton("Actualiser")
        refresh.clicked.connect(self.refresh_windows)
        row.addWidget(self.window_combo, 1)
        row.addWidget(refresh)
        wl.addLayout(row)
        hint = QLabel(
            "• Quelqu'un d'autre partage → la fenêtre Zoom / Teams.\n"
            "• Vous partagez → « Écran entier » ou la fenêtre partagée (ex. Diaporama PowerPoint).\n"
            "• Une fenêtre peut être cachée derrière d'autres, mais pas réduite.\n"
            "• Chrome : détachez l'onglet de la réunion dans sa propre fenêtre."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        wl.addWidget(hint)
        self.visio_warning = QLabel(
            "⚠ Si c'est vous qui partagez votre écran, cette fenêtre Zoom/Teams ne montrera que "
            "votre vidéo : choisissez plutôt « Écran entier » ou la fenêtre de votre présentation."
        )
        self.visio_warning.setWordWrap(True)
        self.visio_warning.setStyleSheet("color: #d35400;")
        self.visio_warning.setVisible(False)
        wl.addWidget(self.visio_warning)
        self.window_combo.currentIndexChanged.connect(self._on_target_changed)
        layout.addWidget(win_box)

        opt_box = QGroupBox("Options")
        form = QFormLayout(opt_box)
        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(self.settings.value("output_dir", str(DEFAULT_DIR)))
        browse = QPushButton("…")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._choose_output_dir)
        dir_row.addWidget(self.dir_edit)
        dir_row.addWidget(browse)
        form.addRow("Dossier des enregistrements", dir_row)

        self.audio_check = QCheckBox("Enregistrer le son de l'ordinateur (son de la réunion)")
        self.audio_check.setChecked(self.settings.value("audio", True, type=bool))
        form.addRow(self.audio_check)
        self.mic_check = QCheckBox("Enregistrer mon micro (ma voix) — idéal avec un casque")
        self.mic_check.setChecked(self.settings.value("mic", True, type=bool))
        form.addRow(self.mic_check)
        self.slides_check = QCheckBox("Détecter et photographier les diapos pendant l'enregistrement")
        self.slides_check.setChecked(self.settings.value("slides", True, type=bool))
        form.addRow(self.slides_check)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 30)
        self.fps_spin.setValue(self.settings.value("fps", 15, type=int))
        self.fps_spin.setSuffix(" images/s")
        form.addRow("Fluidité de la vidéo", self.fps_spin)

        zone_row = QHBoxLayout()
        self.zone_label = QLabel(zone_text(None))
        zone_btn = QPushButton("Définir la zone des diapos…")
        zone_btn.clicked.connect(self._choose_record_zone)
        zone_reset = QPushButton("Toute la fenêtre")
        zone_reset.clicked.connect(lambda: self._set_record_zone(None))
        zone_row.addWidget(self.zone_label, 1)
        zone_row.addWidget(zone_btn)
        zone_row.addWidget(zone_reset)
        form.addRow("Diapos", zone_row)
        layout.addWidget(opt_box)

        self.record_btn = QPushButton("●  Démarrer l'enregistrement")
        self.record_btn.setMinimumHeight(48)
        self.record_btn.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.record_btn.clicked.connect(self.toggle_recording)
        layout.addWidget(self.record_btn)

        status_row = QHBoxLayout()
        self.status_label = QLabel("Prêt.")
        self.status_label.setWordWrap(True)
        self.preview = QLabel()
        self.preview.setFixedSize(240, 135)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("border: 1px solid #ccc; color: gray;")
        self.preview.setText("Dernière diapo")
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.preview)
        layout.addLayout(status_row)

        self.warning_label = QLabel()
        self.warning_label.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)

        legal = QLabel(LEGAL)
        legal.setWordWrap(True)
        legal.setStyleSheet("color: gray; font-size: 11px;")
        layout.addStretch(1)
        layout.addWidget(legal)
        return page

    def refresh_windows(self) -> None:
        current = self.window_combo.currentData()
        self.window_combo.blockSignals(True)
        self.window_combo.clear()
        windows = list_windows()
        # Applications de visio et PowerPoint, puis les écrans entiers, puis le reste.
        targets = [w for w in windows if w.preferred] + list_monitors() \
            + [w for w in windows if not w.preferred]
        for target in targets:
            self.window_combo.addItem(target.label, target)
        if not targets:
            self.window_combo.addItem(
                "Aucune fenêtre trouvée" if IS_WINDOWS else "Capture disponible sous Windows uniquement",
                None,
            )
        if current:
            for i in range(self.window_combo.count()):
                if self.window_combo.itemData(i) == current:
                    self.window_combo.setCurrentIndex(i)
        self.window_combo.blockSignals(False)
        self._on_target_changed()

    def _on_target_changed(self, *_args) -> None:
        target = self.window_combo.currentData()
        self.visio_warning.setVisible(isinstance(target, WindowInfo) and target.is_visio)

    def _choose_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Dossier des enregistrements", self.dir_edit.text())
        if folder:
            self.dir_edit.setText(folder)

    def _selected_window(self) -> Optional[WindowInfo | MonitorInfo]:
        data = self.window_combo.currentData()
        if not data:
            QMessageBox.warning(self, "Fenêtre", "Choisissez une fenêtre à enregistrer.")
            return None
        if isinstance(data, WindowInfo) and not window_exists(data.hwnd):
            QMessageBox.warning(self, "Fenêtre", "Cette fenêtre n'existe plus. Cliquez sur Actualiser.")
            return None
        return data

    @staticmethod
    def _capture_args(target: WindowInfo | MonitorInfo) -> tuple[Optional[int], Optional[int]]:
        """(hwnd, numéro d'écran) attendus par l'enregistreur."""
        if isinstance(target, MonitorInfo):
            return None, target.index
        return target.hwnd, None

    def _choose_record_zone(self) -> None:
        selected = self._selected_window()
        if not selected:
            return
        from .recorder import grab_snapshot

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            image = grab_snapshot(*self._capture_args(selected))
        except Exception as exc:
            QMessageBox.warning(self, "Capture", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        dialog = ZoneDialog(image, self)
        if dialog.exec():
            self._set_record_zone(dialog.zone())

    def _set_record_zone(self, zone: Optional[Zone]) -> None:
        self.record_zone = zone
        self.zone_label.setText(zone_text(zone))

    def toggle_recording(self) -> None:
        if self.recorder is None:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self) -> None:
        selected = self._selected_window()
        if not selected:
            return
        hwnd, monitor_index = self._capture_args(selected)
        from .recorder import Recorder

        self.settings.setValue("output_dir", self.dir_edit.text())
        self.settings.setValue("audio", self.audio_check.isChecked())
        self.settings.setValue("mic", self.mic_check.isChecked())
        self.settings.setValue("slides", self.slides_check.isChecked())
        self.settings.setValue("fps", self.fps_spin.value())

        recorder = Recorder(
            hwnd,
            Path(self.dir_edit.text()),
            window_title=selected.title,
            monitor_index=monitor_index,
            fps=self.fps_spin.value(),
            record_audio=self.audio_check.isChecked(),
            record_mic=self.mic_check.isChecked(),
            detect_slides=self.slides_check.isChecked(),
            detector_settings=DetectorSettings(zone=self.record_zone),
            on_slide=lambda s: self.bridge.slide.emit(str(s.image_path)),
            on_slide_updated=lambda s: self.bridge.slide.emit(str(s.image_path)),
            on_window_closed=self.bridge.window_closed.emit,
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            recorder.start()
        except Exception as exc:
            QMessageBox.critical(self, "Impossible de démarrer", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.recorder = recorder
        self.preview.setText("Dernière diapo")
        self.preview.setPixmap(QPixmap())
        self.record_btn.setText("■  Arrêter l'enregistrement")
        self.record_btn.setStyleSheet("font-size: 16px; font-weight: bold; color: white; background: #c0392b;")
        self.warning_label.setText(recorder.audio_warning)
        self.window_combo.setEnabled(False)
        self.timer.start(500)
        self._tick()

    def stop_recording(self) -> None:
        recorder = self.recorder
        if recorder is None:
            return
        self.timer.stop()
        self.record_btn.setEnabled(False)
        self.record_btn.setText("Finalisation de la vidéo…")
        self.status_label.setText("Finalisation (assemblage vidéo et son)…")

        def finish():
            try:
                return recorder.stop(), ""
            except Exception as exc:
                return recorder.session, str(exc)

        task = Task(finish)
        task.done.connect(self._recording_finished, Q)
        self._run(task)

    def _recording_finished(self, result) -> None:
        session, problem = result
        self.recorder = None
        self.record_btn.setEnabled(True)
        self.record_btn.setText("●  Démarrer l'enregistrement")
        self.record_btn.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.window_combo.setEnabled(True)
        self.warning_label.setText(problem)
        self.status_label.setText(
            f"Terminé : {format_timestamp(session.duration)}, {len(session.slides)} diapos.\n"
            f"{session.folder}"
        )
        self.load_session(session)
        if problem:
            QMessageBox.warning(self, "Enregistrement", problem)
        box = QMessageBox(self)
        box.setWindowTitle("Enregistrement terminé")
        box.setText(f"Enregistrement terminé ({len(session.slides)} diapos détectées).")
        open_btn = box.addButton("Ouvrir le dossier", QMessageBox.ButtonRole.ActionRole)
        next_btn = box.addButton("Créer les diapos / compte-rendu", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Fermer", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            open_folder(session.folder)
        elif box.clickedButton() is next_btn:
            self.tabs.setCurrentIndex(1)

    def _tick(self) -> None:
        r = self.recorder
        if r is None:
            return
        elapsed = format_timestamp(r.elapsed)
        slides = f" — {r.slide_count} diapo(s)" if r.detector else ""
        self.status_label.setText(f"● Enregistrement en cours : {elapsed}{slides}")
        warnings = [r.audio_warning] if r.audio_warning else []
        if r.hwnd is not None and is_minimized(r.hwnd):
            warnings.append("⚠ La fenêtre est réduite : l'image est figée. Rouvrez-la "
                            "(elle peut rester derrière les autres fenêtres).")
        self.warning_label.setText("\n".join(warnings))

    def _on_slide(self, path: str) -> None:
        image = read_image(Path(path))
        if image is not None:
            self.preview.setPixmap(to_pixmap(image).scaled(
                self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def _on_window_closed(self) -> None:
        if self.recorder is not None:
            self.stop_recording()

    # ================================================================ onglet 2
    def _build_after_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        src_box = QGroupBox("Enregistrement")
        sl = QVBoxLayout(src_box)
        row = QHBoxLayout()
        open_session = QPushButton("Ouvrir un dossier d'enregistrement…")
        open_session.clicked.connect(self._choose_session)
        open_video = QPushButton("Ouvrir une autre vidéo…")
        open_video.clicked.connect(self._choose_video)
        row.addWidget(open_session)
        row.addWidget(open_video)
        sl.addLayout(row)
        self.session_label = QLabel("Aucun enregistrement chargé.")
        self.session_label.setWordWrap(True)
        sl.addWidget(self.session_label)
        layout.addWidget(src_box)

        ext_box = QGroupBox("Photos des diapos")
        el = QVBoxLayout(ext_box)
        row = QHBoxLayout()
        self.after_zone_label = QLabel(zone_text(None))
        zone_btn = QPushButton("Zone…")
        zone_btn.clicked.connect(self._choose_video_zone)
        self.extract_btn = QPushButton("Extraire les diapos de la vidéo")
        self.extract_btn.clicked.connect(self._extract)
        row.addWidget(self.after_zone_label, 1)
        row.addWidget(zone_btn)
        row.addWidget(self.extract_btn)
        el.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        el.addWidget(self.progress)
        layout.addWidget(ext_box)

        tr_box = QGroupBox("Transcription sur ce PC (sans Plaud, rien n'est envoyé sur Internet)")
        tl = QFormLayout(tr_box)
        from .transcribe import DEFAULT_MODEL, MODELS

        self.model_combo = QComboBox()
        for label, name in MODELS.items():
            self.model_combo.addItem(label, name)
        saved = self.settings.value("model", DEFAULT_MODEL)
        self.model_combo.setCurrentIndex(max(0, self.model_combo.findData(saved)))
        tl.addRow("Qualité", self.model_combo)
        self.lang_combo = QComboBox()
        for label, code in (("Détection automatique (recommandé)", ""), ("Français", "fr"), ("Anglais", "en")):
            self.lang_combo.addItem(label, code)
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(self.settings.value("language", ""))))
        tl.addRow("Langue parlée", self.lang_combo)
        self.translate_check = QCheckBox(
            "Créer aussi une version traduite (anglais → français, ou français → anglais)")
        self.translate_check.setChecked(self.settings.value("translate", True, type=bool))
        tl.addRow(self.translate_check)
        self.llm_check = QCheckBox(
            "Utiliser l'IA locale LM Studio, si elle est lancée (traduction soignée, résumé rédigé)")
        self.llm_check.setChecked(self.settings.value("use_llm", True, type=bool))
        tl.addRow(self.llm_check)
        llm_row = QHBoxLayout()
        self.llm_url = QLineEdit(self.settings.value("llm_url", "http://localhost:1234"))
        self.llm_url.setToolTip("Adresse du serveur LM Studio (onglet Developer)")
        test_btn = QPushButton("Tester")
        test_btn.clicked.connect(self._test_llm)
        self.llm_status = QLabel("")
        self.llm_status.setStyleSheet("color: gray;")
        llm_row.addWidget(self.llm_url, 1)
        llm_row.addWidget(test_btn)
        tl.addRow("Serveur LM Studio", llm_row)
        tl.addRow("", self.llm_status)
        self.glossary_edit = QLineEdit(self.settings.value("glossary", ""))
        self.glossary_edit.setPlaceholderText("Optionnel : fichier .txt, une ligne « terme = traduction »")
        tl.addRow("Glossaire", self._with_browse(self.glossary_edit, "Glossaire", "Texte (*.txt)"))
        self.vocab_edit = QLineEdit(self.settings.value("vocabulary", ""))
        self.vocab_edit.setPlaceholderText("Optionnel : noms et termes difficiles (ex. RCP, pembrolizumab, Dr Le Gall)")
        tl.addRow("Vocabulaire", self.vocab_edit)
        self.transcribe_btn = QPushButton("Transcrire")
        self.transcribe_btn.clicked.connect(self._transcribe)
        tr_hint = QLabel(
            "Modèles téléchargés une seule fois (connexion nécessaire la 1ʳᵉ fois), ensuite "
            "tout se fait hors ligne. Le texte se termine par un résumé (phrases clés). "
            "Durée indicative pour 1 h : 10 à 20 min en Rapide, nettement plus en Précis."
        )
        tr_hint.setWordWrap(True)
        tr_hint.setStyleSheet("color: gray;")
        tl.addRow(self.transcribe_btn, tr_hint)
        layout.addWidget(tr_box)

        exp_box = QGroupBox("Diapos maison et synchronisation")
        form = QFormLayout(exp_box)
        self.template_edit = QLineEdit(self.settings.value("template", ""))
        self.template_edit.setPlaceholderText("Optionnel : votre modèle .potx / .pptx (logo, bandeau…)")
        form.addRow("Modèle PowerPoint", self._with_browse(
            self.template_edit, "Modèle PowerPoint", "PowerPoint (*.potx *.pptx)"))
        self.transcript_edit = QLineEdit()
        self.transcript_edit.setPlaceholderText("Celle du logiciel (ci-dessus) ou un export Plaud horodaté")
        form.addRow("Transcription", self._with_browse(
            self.transcript_edit, "Transcription", "Transcription (*.txt *.srt *.vtt *.docx)"))
        self.plaud_time = QTimeEdit()
        self.plaud_time.setDisplayFormat("HH:mm:ss")
        form.addRow("Heure de début du Plaud", self.plaud_time)
        self.fine_offset = QDoubleSpinBox()
        self.fine_offset.setRange(-3600, 3600)
        self.fine_offset.setDecimals(1)
        self.fine_offset.setSuffix(" s")
        form.addRow("Ajustement fin", self.fine_offset)
        sync_hint = QLabel(
            "Heure du Plaud : uniquement pour un export Plaud (l'heure à laquelle vous l'avez "
            "lancé). La transcription du logiciel est déjà calée sur la vidéo."
        )
        sync_hint.setWordWrap(True)
        sync_hint.setStyleSheet("color: gray;")
        form.addRow(sync_hint)
        layout.addWidget(exp_box)

        row = QHBoxLayout()
        self.pptx_btn = QPushButton("Créer le PowerPoint")
        self.pptx_btn.clicked.connect(self._make_pptx)
        self.report_btn = QPushButton("Créer le compte-rendu Word")
        self.report_btn.clicked.connect(self._make_report)
        open_btn = QPushButton("Ouvrir le dossier")
        open_btn.clicked.connect(lambda: self.session and open_folder(self.session.folder))
        for b in (self.pptx_btn, self.report_btn, open_btn):
            b.setMinimumHeight(36)
            row.addWidget(b)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _with_browse(self, edit: QLineEdit, title: str, filt: str) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        btn = QPushButton("…")
        btn.setFixedWidth(32)

        def browse():
            path, _ = QFileDialog.getOpenFileName(self, title, edit.text() or str(Path.home()), filt)
            if path:
                edit.setText(path)

        btn.clicked.connect(browse)
        row.addWidget(edit)
        row.addWidget(btn)
        return w

    def _choose_session(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Dossier d'enregistrement", self.dir_edit.text())
        if not folder:
            return
        try:
            self.load_session(Session.load(Path(folder)))
        except Exception as exc:
            QMessageBox.warning(self, "Enregistrement", str(exc))

    def _choose_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Vidéo", str(Path.home()), "Vidéos (*.mp4 *.mkv *.mov *.avi *.webm)")
        if path:
            self.load_session(Session.for_video(Path(path)))

    def load_session(self, session: Session) -> None:
        self.session = session
        start = session.start_datetime
        parts = [str(session.folder)]
        if start:
            parts.append(f"Début : {start:%d/%m/%Y %H:%M:%S}")
        if session.duration:
            parts.append(f"Durée : {format_timestamp(session.duration)}")
        parts.append(f"{len(session.slides)} diapo(s)")
        self.session_label.setText(" — ".join(parts))
        self.after_zone_label.setText(zone_text(session.zone))
        if start:
            self.plaud_time.setTime(QTime(start.hour, start.minute, start.second))
        # Sans heure de début (vidéo importée), seul l'ajustement manuel est utilisable.
        self.plaud_time.setEnabled(bool(start))
        own = session.folder / session.transcript if session.transcript else None
        self.transcript_edit.setText(str(own) if own and own.exists() else "")

    def _need_session(self, with_slides: bool = False) -> bool:
        if self.session is None:
            QMessageBox.information(self, "Enregistrement", "Ouvrez d'abord un enregistrement.")
            return False
        if with_slides and not self.session.slides:
            QMessageBox.information(self, "Diapos", "Aucune diapo : lancez d'abord l'extraction.")
            return False
        return True

    def _choose_video_zone(self) -> None:
        if not self._need_session() or not self.session.video_path:
            return
        cap = cv2.VideoCapture(str(self.session.video_path))
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total // 2)))
        ok, frame = cap.read()
        cap.release()
        if not ok:
            QMessageBox.warning(self, "Vidéo", "Impossible de lire la vidéo.")
            return
        dialog = ZoneDialog(frame, self)
        if dialog.exec():
            self.session.zone = dialog.zone()
            self.after_zone_label.setText(zone_text(self.session.zone))

    def _extract(self) -> None:
        if not self._need_session():
            return
        from .workflow import extract_slides

        if self.session.slides and QMessageBox.question(
            self, "Diapos", "Remplacer les diapos déjà extraites ?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.extract_btn.setEnabled(False)
        task = Task(lambda: extract_slides(
            self.session, DetectorSettings(zone=self.session.zone),
            progress=task.progress.emit))
        task.progress.connect(lambda p: self.progress.setValue(int(p * 100)), Q)
        task.done.connect(self._extracted, Q)
        task.failed.connect(self._task_failed, Q)
        self._run(task)

    def _extracted(self, session: Session) -> None:
        self.progress.setVisible(False)
        self.extract_btn.setEnabled(True)
        self.load_session(session)
        QMessageBox.information(self, "Diapos", f"{len(session.slides)} diapo(s) extraite(s).")

    def _offset(self) -> float:
        from .workflow import compute_offset

        start = self.session.start_datetime
        if not start:
            return self.fine_offset.value()
        t = self.plaud_time.time()
        plaud = datetime.combine(start.date(), datetime.min.time()).replace(
            hour=t.hour(), minute=t.minute(), second=t.second())
        return compute_offset(self.session, plaud) + self.fine_offset.value()

    def _transcript(self) -> Optional[Path]:
        text = self.transcript_edit.text().strip()
        return Path(text) if text else None

    def _make_pptx(self) -> None:
        if not self._need_session(with_slides=True):
            return
        from .workflow import make_pptx

        template = self.template_edit.text().strip()
        self.settings.setValue("template", template)
        task = Task(make_pptx, self.session, Path(template) if template else None,
                    self._transcript(), self._offset())
        task.done.connect(lambda p: self._created(p, self.pptx_btn), Q)
        task.failed.connect(self._task_failed, Q)
        self.pptx_btn.setEnabled(False)
        self._run(task)

    def _make_report(self) -> None:
        if not self._need_session(with_slides=True):
            return
        transcript = self._transcript()
        if not transcript:
            QMessageBox.information(self, "Compte-rendu",
                                    "Lancez d'abord « Transcrire », ou choisissez un export Plaud.")
            return
        from .workflow import make_reports

        task = Task(make_reports, self.session, transcript, self._offset())
        task.done.connect(lambda p: self._created(p, self.report_btn), Q)
        task.failed.connect(self._task_failed, Q)
        self.report_btn.setEnabled(False)
        self._run(task)

    def _transcribe(self) -> None:
        if not self._need_session():
            return
        from .transcribe import transcribe_session

        model = self.model_combo.currentData()
        vocabulary = self.vocab_edit.text().strip()
        language = self.lang_combo.currentData() or None
        translate = self.translate_check.isChecked()
        use_llm = self.llm_check.isChecked()
        llm_url = self.llm_url.text().strip() or "http://localhost:1234"
        glossary_path = self.glossary_edit.text().strip()
        from .llm import load_glossary

        try:
            glossary = load_glossary(Path(glossary_path)) if glossary_path else ""
        except OSError as exc:
            QMessageBox.warning(self, "Glossaire", f"Glossaire illisible : {exc}")
            return
        self.settings.setValue("use_llm", use_llm)
        self.settings.setValue("llm_url", llm_url)
        self.settings.setValue("glossary", glossary_path)
        self.settings.setValue("model", model)
        self.settings.setValue("vocabulary", vocabulary)
        self.settings.setValue("language", language or "")
        self.settings.setValue("translate", translate)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.setText("Transcription…")
        session = self.session
        task = Task(lambda: transcribe_session(session, model, vocabulary,
                                               progress=task.progress.emit,
                                               language=language, translate=translate,
                                               use_llm=use_llm, llm_url=llm_url,
                                               glossary=glossary))
        task.progress.connect(lambda p: self.progress.setValue(int(p * 100)), Q)
        task.done.connect(self._transcribed, Q)
        task.failed.connect(self._task_failed, Q)
        self._run(task)

    def _transcribed(self, path: Path) -> None:
        self.progress.setVisible(False)
        self.transcribe_btn.setEnabled(True)
        self.transcribe_btn.setText("Transcrire")
        self.transcript_edit.setText(str(path))
        ai = self.session.ai if self.session else {}
        lines = [f"Transcription terminée :\n{path.with_suffix('.txt')}"]
        if ai.get("model"):
            lines.append(f"Résumé rédigé par l'IA locale : {ai['model']}")
        for lang, info in (self.session.translations if self.session else {}).items():
            lines.append(f"Version traduite ({lang}) : {info.get('engine', '')}")
        if ai.get("warnings"):
            lines.append("⚠ " + "\n⚠ ".join(ai["warnings"]))
        if QMessageBox.question(
            self, "Transcription", "\n\n".join(lines) + "\n\nOuvrir la transcription ?"
        ) == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.with_suffix(".txt"))))

    def _test_llm(self) -> None:
        from .llm import LMStudio

        self.llm_status.setText("Connexion…")
        url = self.llm_url.text().strip() or "http://localhost:1234"

        def probe():
            client = LMStudio(url, timeout=120)
            name = client.connect()
            reply = client.chat("Réponds en un seul mot.", "Dis « prêt ».", max_tokens=200)
            return name, reply

        def done(result):
            name, reply = result
            self.llm_status.setStyleSheet("color: #1e8449;")
            self.llm_status.setText(f"✔ Connecté : {name} (réponse : {reply[:30]})")

        def failed(message):
            self.llm_status.setStyleSheet("color: #c0392b;")
            self.llm_status.setText(f"✘ {message}")

        task = Task(probe)
        task.done.connect(done, Q)
        task.failed.connect(failed, Q)
        self._run(task)

    def _created(self, paths, button: QPushButton) -> None:
        button.setEnabled(True)
        paths = paths if isinstance(paths, list) else [paths]
        names = "\n".join(str(p) for p in paths)
        label = "Fichier créé" if len(paths) == 1 else "Fichiers créés"
        if QMessageBox.question(self, "Créé", f"{label} :\n{names}\n\nOuvrir ?") \
                == QMessageBox.StandardButton.Yes:
            for path in paths:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _task_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        for b in (self.extract_btn, self.pptx_btn, self.report_btn, self.transcribe_btn):
            b.setEnabled(True)
        self.transcribe_btn.setText("Transcrire")
        QMessageBox.warning(self, "Erreur", message)

    def _run(self, task: Task) -> None:
        self._tasks.append(task)
        task.done.connect(lambda *_: self._tasks.remove(task) if task in self._tasks else None, Q)
        task.failed.connect(lambda *_: self._tasks.remove(task) if task in self._tasks else None, Q)
        task.start()

    # ================================================================ fermeture
    def closeEvent(self, event) -> None:
        if self.recorder is not None:
            answer = QMessageBox.question(
                self, "Enregistrement en cours", "Arrêter l'enregistrement et quitter ?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            try:
                self.recorder.stop()
            except Exception:
                pass
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Capture réunion")
    window = MainWindow()
    window.show()
    return app.exec()
