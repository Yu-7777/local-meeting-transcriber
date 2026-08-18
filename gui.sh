#!/usr/bin/env bash
set -u
cd "$(dirname "$(readlink -f "$0")")" || exit 1

if [ ! -x .venv/bin/python ]; then
    printf '%s\n' "[エラー] 仮想環境がありません。先に ./setup.sh を実行してください。"
    exit 1
fi

# 端末を閉じても残るように切り離す（.bat 側で黒い窓を出さないのと同じ意図）
exec .venv/bin/python -m local_transcription.gui "$@"
