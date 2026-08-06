"""どのエントリポイントからも使う小物.

ここに置くものは重い依存を持たないこと。特に pyaudiowpatch は音声を扱わない
文字起こし経路へ持ち込まない。

理由は、pyaudiowpatch の _portaudiowpatch.pyd が署名なしで、スマート アプリ
コントロールに引っかかる面を 1 つ増やすため。ただし同じ経路が読む
ctranslate2 / av / onnxruntime / sherpa-onnx も署名なしなので、これで経路が
署名なしから自由になるわけではない（増やさない、というだけ）。
"""

import sys
from pathlib import Path

import config
from apppaths import FROZEN

# 単体ファイルを直接指定された場合に受け付ける拡張子。
# GUI のファイル選択ダイアログもここから組み立てる（表記が割れないように）。
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".wma",
                  ".mp4", ".mkv", ".webm", ".mov"}


def hhmmss(sec):
    """秒を HH:MM:SS にする。負値は 0 とみなす."""
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def use_utf8_stdout():
    """コンソールの文字化けを防ぐ。コンソールを使う main() の先頭で呼ぶ.

    cp932 に無い文字がデバイス名に入っていると UnicodeEncodeError になる。
    実際に落ちたのは「インテル® スマート・サウンド」の ®（U+00AE の
    丸 R。ASCII の "(R)" は cp932 で表せるので落ちない）。
    GUI は標準出力を使わないので呼んでいない。stderr は対象外。
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def list_recordings(base=None):
    """録音フォルダを新しい順に返す.

    「録音フォルダ」の定義（meta.json があるディレクトリ）はここだけに置く。
    GUI の一覧と CLI の「最新の録音」が食い違わないようにするため。
    """
    base = Path(base) if base else config.recordings_dir()
    if not base.exists():
        return []
    dirs = [d for d in base.iterdir() if d.is_dir() and (d / "meta.json").exists()]
    return sorted(dirs, key=lambda d: d.stat().st_mtime, reverse=True)


def cli_hint(subcommand, *args):
    """利用者に見せる「この機能の呼び出し方」を、実行形態に合わせて返す.

    exe に固めると .venv も .py も存在しないので、案内を直書きすると
    どこにも無いパスを指すことになる。
    """
    tail = " ".join([subcommand, *(str(a) for a in args)]).strip()
    if FROZEN:
        return f"{Path(sys.executable).name} {tail}"
    return f".venv\\Scripts\\python.exe app.py {tail}"
