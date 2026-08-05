# PyInstaller spec — 会議録音・文字起こしツール
#
# onedir 構成にする理由:
#   onefile は起動のたびに 300MB 超の DLL を temp に展開するため起動が遅く、
#   ネイティブ依存が多い構成では失敗しやすい。市販ソフト同様「exe + DLL 群の
#   フォルダ」にする。models/ と recordings/ は exe の隣に置く（同梱しない）。

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
