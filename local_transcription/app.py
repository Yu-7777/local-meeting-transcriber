"""各機能へ振り分けるエントリポイント（サブコマンドの一覧は BUILD.md）.

exe には python.exe が無いので、GUI からの文字起こし呼び出しもここを
経由して自分自身を起動する。
"""

import importlib
import sys

COMMANDS = {
    "transcribe": "transcribe",
    "record": "record",
    "devices": "check_devices",
    "download": "download_models",
    "shortcut": "shortcut",
}


def main():
    argv = sys.argv[1:]
    module = COMMANDS.get(argv[0]) if argv else None
    if module is None:
        return importlib.import_module(".gui", __package__).main()
    sys.argv = [sys.argv[0], *argv[1:]]
    return importlib.import_module(f".{module}", __package__).main()


if __name__ == "__main__":
    sys.exit(main())
