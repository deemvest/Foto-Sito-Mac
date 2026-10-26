# -*- mode: python ; coding: utf-8 -*-
# Сборка под macOS: pyinstaller FotoSito-mac.spec
# Перед сборкой в папке проекта должны лежать:
#   exiftool_mac/   (файл exiftool + папка lib из Image-ExifTool)
#   assets/         (icon.icns, logo.png)
import os

datas = []
for folder in ("assets", "exiftool_mac"):
    if os.path.isdir(folder):
        datas.append((folder, folder))

icon = "assets/icon.icns" if os.path.exists("assets/icon.icns") else None

a = Analysis(
    ["main.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=["pillow_heif"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FotoSito",
    console=False,
    icon=icon,
)

coll = COLLECT(exe, a.binaries, a.datas, name="FotoSito")

app = BUNDLE(
    coll,
    name="FotoSito.app",
    icon=icon,
    bundle_identifier="com.deemvest.fotosito",
    info_plist={
        "CFBundleDisplayName": "Фото-Сито",
        "CFBundleShortVersionString": "1.1",
        "NSHighResolutionCapable": True,
        "NSRemovableVolumesUsageDescription": "Программе нужен доступ к фото на внешних дисках.",
    },
)
