"""アプリのルートを解決する.

ソース実行なら local_transcription/ の一つ上、exe に固めたなら exe の
置かれたフォルダ。models/ と recordings/ はその隣に置く。
"""

import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
ROOT = (Path(sys.executable).resolve().parent if FROZEN
         else Path(__file__).resolve().parent.parent)

MODELS_DIR = ROOT / "models"
RECORDINGS_DIR = ROOT / "recordings"


def child_command(subcommand, *args):
    """自分自身を子プロセスとして起動するコマンド列を返す.

    凍結時は python.exe が無いので、exe 自身を呼び直す。
    """
    head = [sys.executable] if FROZEN else [sys.executable, str(ROOT / "app.py")]
    return [*head, subcommand, *(str(a) for a in args)]
