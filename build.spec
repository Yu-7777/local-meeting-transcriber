# PyInstaller spec（onedir 構成。理由は BUILD.md「onedir にした理由」）

from PyInstaller.utils.hooks import collect_all, collect_data_files

datas, binaries, hiddenimports = [], [], []

# ネイティブ DLL とデータを丸ごと拾う必要があるパッケージ
# comtypes は動的に生成した COM 型モジュールを imp/pkgutil 経由で読むため、
# hiddenimports だけでは PyInstaller が見つけられない。collect_all で拾う。
# pyaudiowpatch と comtypes は Windows でしか入らない（requirements.txt 参照）。
for pkg in ("ctranslate2", "onnxruntime", "sherpa_onnx", "av", "pyaudiowpatch",
            "tokenizers", "comtypes"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# faster-whisper は Silero VAD の onnx をパッケージ内に持っているので必須
datas += collect_data_files("faster_whisper")

hiddenimports += ["faster_whisper", "local_transcription", "local_transcription.diarization",
                  "local_transcription.transcribe", "local_transcription.record",
                  "local_transcription.check_devices", "local_transcription.download_models",
                  "local_transcription.gui", "local_transcription.apppaths",
                  "local_transcription.config", "local_transcription.common",
                  "local_transcription.shortcut", "local_transcription.audio",
                  "local_transcription.audio_wasapi"]

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
