# PyInstaller spec（onedir 構成。理由は BUILD.md「onedir にした理由」）

from PyInstaller.utils.hooks import collect_all, collect_data_files

datas, binaries, hiddenimports = [], [], []

# ネイティブ DLL とデータを丸ごと拾う必要があるパッケージ
for pkg in ("ctranslate2", "onnxruntime", "sherpa_onnx", "av", "pyaudiowpatch",
            "tokenizers"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# faster-whisper は Silero VAD の onnx をパッケージ内に持っているので必須
datas += collect_data_files("faster_whisper")

hiddenimports += ["faster_whisper", "diarization", "transcribe", "record",
                  "check_devices", "download_models", "gui", "apppaths", "config",
                  "common",
                   "shortcut"]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["torch", "matplotlib", "pytest", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MeetingTranscriber",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # GUI アプリなのでコンソールを出さない
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MeetingTranscriber",
)
