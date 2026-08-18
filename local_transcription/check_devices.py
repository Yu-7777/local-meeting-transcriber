"""オーディオデバイスを一覧表示する診断ツール.

録音に渡すデバイス index をここで確認する。表示の中身は OS ごとの
バックエンド（audio_wasapi / audio_pulse）が受け持つ。
"""

import sys

from . import audio
from . import common


def main():
    common.use_utf8_stdout()
    return audio.print_devices()


if __name__ == "__main__":
    sys.exit(main())
