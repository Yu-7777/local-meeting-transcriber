"""PyInstaller のエントリスクリプト（実体は local_transcription.app）.

exe の Analysis はパッケージ内スクリプトでなく実ファイルパスを要求するため、
ここに薄いスタブを置く。開発時に `python app.py ...` する場合の入口も兼ねる。
"""

import sys

from local_transcription.app import main

if __name__ == "__main__":
    sys.exit(main())
