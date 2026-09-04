# PyInstaller spec — builds KDownloader.app (standalone macOS, arm64).
# Bundles a static ffmpeg (vendor/ffmpeg) so no system ffmpeg is required.
# Build:  pyinstaller --noconfirm KDownloader.spec

block_cipher = None

a = Analysis(
    ['KDownloader.py'],
    pathex=[],
    binaries=[('vendor/ffmpeg', '.')],   # -> Contents/Frameworks/ffmpeg (sys._MEIPASS)
    datas=[],
    hiddenimports=['kino_core', 'certifi'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='KDownloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name='KDownloader',
)
app = BUNDLE(
    coll,
    name='KDownloader.app',
    icon=None,
    bundle_identifier='io.github.kdownloader',
    info_plist={
        'CFBundleName': 'KDownloader',
        'CFBundleDisplayName': 'KDownloader',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'LSApplicationCategoryType': 'public.app-category.utilities',
    },
)
