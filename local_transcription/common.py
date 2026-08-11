"""どのエントリポイントからも使う小物.

重い依存を持たないこと。特に pyaudiowpatch は、署名なし DLL を文字起こし
経路へ増やさないため持ち込まない（背景は BUILD.md）。
"""

import sys
from pathlib import Path

from . import config
from .apppaths import FROZEN

# 単体ファイル指定で受け付ける拡張子。GUI のファイル選択もここから作る
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".wma",
                  ".mp4", ".mkv", ".webm", ".mov"}


def hhmmss(sec):
    """秒を HH:MM:SS にする。負値は 0 とみなす."""
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def use_utf8_stdout():
    """コンソールの文字化けを防ぐ。コンソールを使う main() の先頭で呼ぶ.

    デバイス名の ® などが cp932 に無く UnicodeEncodeError になるため。
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def list_recordings(base=None):
    """録音フォルダ（meta.json があるもの）を新しい順に返す.

    この判定をここだけに置き、GUI の一覧と CLI の「最新」を一致させる。
    """
    base = Path(base) if base else config.recordings_dir()
    if not base.exists():
        return []
    dirs = [d for d in base.iterdir() if d.is_dir() and (d / "meta.json").exists()]
    return sorted(dirs, key=lambda d: d.stat().st_mtime, reverse=True)


def cli_hint(subcommand, *args):
    """利用者に見せる呼び出し方を、実行形態に合わせて返す.

    exe には .venv も .py も無いので、案内を直書きすると嘘になる。
    """
    tail = " ".join([subcommand, *(str(a) for a in args)]).strip()
    if FROZEN:
        return f"{Path(sys.executable).name} {tail}"
    return f".venv\\Scripts\\python.exe app.py {tail}"
