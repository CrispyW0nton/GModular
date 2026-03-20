"""
GModular — KotOR Module Editor
Setup script.
"""
from setuptools import setup, find_packages

setup(
    name="gmodular",
    version="2.0.0",
    description="KotOR Module Editor — Unreal Engine-style editor for KotOR 1 & 2",
    long_description=open("README.md").read() if __import__("os").path.exists("README.md") else "",
    author="GModular Contributors",
    python_requires=">=3.8",
    packages=find_packages(),
    install_requires=[
        # Qt compatibility layer — works with PyQt5, PyQt6, PySide2 or PySide6.
        # Install at least one Qt backend (e.g. PyQt5>=5.15) alongside qtpy.
        "qtpy>=2.4.0",
        "PyQt5>=5.15.0",
        "moderngl>=5.8.0",
        "numpy>=1.21.0",
        "watchdog>=2.0.0",
        "requests>=2.28.0",
    ],
    entry_points={
        "console_scripts": [
            "gmodular=gmodular.__main__:main",
        ],
        "gui_scripts": [
            "gmodular-gui=gmodular.__main__:main",
        ],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: X11 Applications :: Qt",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Games/Entertainment",
    ],
)
