from PIL.ImageQt import ImageQt
from PySide6.QtCore import QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QVBoxLayout,
)

from neotja import settings as settings_mod
from neotja.tja_image_export import generate_chart_image, load_sprites


class ChartGraphicsView(QGraphicsView):
    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)


class TJAImagePreviewDialog(QDialog):
    def __init__(self, main_window, content, selected_label, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.content = content
        self.selected_label = selected_label
        self.img = None
        self.setWindowTitle("譜面画像プレビュー")
        self.resize(1000, 700)

        layout = QVBoxLayout(self)
        self.lbl_status = QLabel("画像生成中...")
        layout.addWidget(self.lbl_status)

        self.view = ChartGraphicsView()
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.scene = QGraphicsScene()
        self.view.setScene(self.scene)
        self.view.hide()
        layout.addWidget(self.view, 1)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton("この画像を保存")
        self.btn_save.setObjectName("accentButton")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._save_image)
        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)

        QTimer.singleShot(100, self._generate_and_show)

    def _generate_and_show(self):
        try:
            courses = self.main_window.analyzer.parse_courses(self.content)
            sprites = load_sprites(settings_mod.notes_png_path())
            self.img = generate_chart_image(self.content, self.selected_label, courses, sprites)
            qimage = ImageQt(self.img)
            pixmap = QPixmap.fromImage(qimage)
            self.scene.clear()
            self.scene.addPixmap(pixmap)
            self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
            self.lbl_status.hide()
            self.view.show()
            self.btn_save.setEnabled(True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.lbl_status.setText(f"エラーが発生しました: {str(e)}")

    def _save_image(self):
        title = next((l[6:].strip() for l in self.content.split('\n') if l.startswith("TITLE:")), "No Title")
        path, _ = QFileDialog.getSaveFileName(self, "保存", f"{title}_{self.selected_label}.png", "PNG Image (*.png)")
        if path:
            self.img.save(path)
            QMessageBox.information(self, "成功", "譜面画像を保存しました。")
            self.accept()
