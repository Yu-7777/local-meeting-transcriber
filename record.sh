#!/usr/bin/env bash
set -u
cd "$(dirname "$(readlink -f "$0")")" || exit 1

if [ ! -x .venv/bin/python ]; then
    printf '%s\n' "[エラー] 仮想環境がありません。先に ./setup.sh を実行してください。"
    exit 1
fi

exec .venv/bin/python -m local_transcription.record "$@"
