# -*- coding: utf-8 -*-
"""一次打包出四個語言版本的免安裝 EXE。

    pip install pyinstaller
    python build-4lang.py

沿用原本「兩個語系各自獨立、網頁埠不同」的設計，四個語系可同時執行不衝突：
    zh/     繁體中文  8080   -> LANQuickShare-zh-TW.exe
    zh-CN/  简体中文  8100   -> LANQuickShare-zh-CN.exe
    ja/     日本語    8120   -> LANQuickShare-ja.exe
    en/     English   8181   -> LANQuickShare-en.exe
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"

LANGS = [
    ("zh-TW", "zh"),
    ("zh-CN", "zh-CN"),
    ("ja", "ja"),
    ("en", "en"),
]

DIST.mkdir(exist_ok=True)
BUILD.mkdir(exist_ok=True)

for code, folder in LANGS:
    src = ROOT / folder
    entry = src / "lan_share.py"
    name = "LANQuickShare-" + code
    print("\n=== 打包 {} ({} / {}) ===".format(code, folder, entry.name), flush=True)
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onefile", "--windowed",
            "--name", name,
            "--distpath", str(DIST),
            "--workpath", str(BUILD / code),
            "--specpath", str(BUILD / "spec"),
            "--add-data", "{};.".format(src / "icon.ico"),
            str(entry),
        ],
        check=True,
    )
    print("✔ " + str(DIST / (name + ".exe")), flush=True)

print("\n四個語言 EXE 已產出於 dist/", flush=True)
