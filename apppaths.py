"""実行形態によらずアプリのルートを解決する.

ソースから実行した場合は自分の居るフォルダ、PyInstaller で固めた場合は
exe の置かれたフォルダを返す。models/ や recordings/ は exe の隣に置く。
"""

import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    # onedir 構成では exe と同じ階層。models/recordings は exe の隣に置く
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent

MODELS_DIR = ROOT / "models"
RECORDINGS_DIR = ROOT / "recordings"


def child_command(subcommand, *args):
    """自分自身を子プロセスとして起動するためのコマンド列を返す.

    凍結時は python.exe が無いので、exe 自身をサブコマンド付きで呼び直す。
    """
    if FROZEN:
        return [sys.executable, subcommand, *[str(a) for a in args]]
    return [sys.executable, str(ROOT / "app.py"), subcommand, *[str(a) for a in args]]
