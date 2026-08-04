"""単一の実行ファイルから各機能へ振り分けるエントリポイント.

    app.exe                    -> GUI
    app.exe transcribe <dir>   -> 文字起こし
    app.exe record             -> 録音 (CLI)
    app.exe devices            -> デバイス一覧
    app.exe download --all     -> モデル取得

exe 化すると python.exe が無くなるため、GUI からの文字起こし呼び出しも
この振り分けを経由して自分自身を起動する。
"""

import sys


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else None

    if cmd in ("transcribe", "record", "devices", "download"):
        sys.argv = [sys.argv[0]] + argv[1:]
        if cmd == "transcribe":
            import transcribe
            return transcribe.main()
        if cmd == "record":
            import record
            return record.main()
        if cmd == "devices":
            import check_devices
            return check_devices.main()
        import download_models
        return download_models.main()

    import gui
    return gui.main()


if __name__ == "__main__":
    sys.exit(main())
