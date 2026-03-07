"""
GModular — PyInstaller hook for PyQt5 (root package)
=====================================================
Same as hook-PyQt5.QtWidgets.py but triggered on the PyQt5 root import.
Both files are included for maximum compatibility across PyInstaller versions.
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all('PyQt5')
