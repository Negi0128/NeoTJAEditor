import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from neotja import settings as settings_mod
from neotja.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    icon_path = settings_mod.icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    win = MainWindow(app)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
