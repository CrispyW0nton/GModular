"""GModular package entry point."""
from .gui.main_window import MainWindow
import sys

def main():
    from qtpy.QtWidgets import QApplication
    from qtpy.QtCore import Qt, QCoreApplication
    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QCoreApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("GModular")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
