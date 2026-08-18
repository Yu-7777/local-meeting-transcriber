#!/usr/bin/env bash
# 会議録音・文字起こしツール - Ubuntu 向けセットアップ
#
# setup.bat と同じ流れ（Python の用意 → .venv → 依存 → モデル → 起動項目）。
# .bat と違いコードページの問題が無いので、案内はそのまま日本語で書く。
set -u

cd "$(dirname "$(readlink -f "$0")")" || exit 1

say() { printf '%s\n' "$*"; }
fail() { say ""; say "*** $* ***"; say ""; exit 1; }

say "============================================================"
say "  会議録音・文字起こしツール - セットアップ"
say "============================================================"
say ""

# ---------------------------------------------------------------- [1/4]
say "[1/4] Python を探しています..."
PYEXE=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    "$candidate" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info < (3,14) else 1)' \
        >/dev/null 2>&1 || continue
    PYEXE="$(command -v "$candidate")"
    break
done
[ -n "$PYEXE" ] || fail "Python 3.10〜3.13 が見つかりません。sudo apt install python3 を実行してください"
say "  使用します: $PYEXE ($("$PYEXE" -V 2>&1))"

# Ubuntu は venv と tkinter を Python 本体と別のパッケージにしている。
# 無いまま進むと分かりにくい所で失敗するので、ここで揃える。
missing=()
"$PYEXE" -c 'import ensurepip' >/dev/null 2>&1 || missing+=("python3-venv")
"$PYEXE" -c 'import tkinter'   >/dev/null 2>&1 || missing+=("python3-tk")
if [ "${#missing[@]}" -gt 0 ]; then
    say ""
    say "  次のパッケージが必要です: ${missing[*]}"
    say "  （Ubuntu では venv と tkinter が Python 本体と別になっています）"
    say ""
    say "      sudo apt install ${missing[*]}"
    say ""
    if [ -t 0 ]; then
        read -r -p "  いま実行しますか? [Y/n] " answer
        case "${answer:-Y}" in
            [Nn]*) fail "上のコマンドを実行してから、もう一度 setup.sh を動かしてください" ;;
        esac
        sudo apt install -y "${missing[@]}" \
            || fail "パッケージを入れられませんでした。上のコマンドを手で実行してください"
    else
        fail "上のコマンドを実行してから、もう一度 setup.sh を動かしてください"
    fi
fi

# 録音は libpulse をそのまま呼ぶ（デスクトップ環境には最初から入っている）。
# 無い場合はここで言わないと、録音開始まで気付けない。
"$PYEXE" -c 'import ctypes; ctypes.CDLL("libpulse.so.0")' >/dev/null 2>&1 \
    || say "  ※ libpulse が見つかりません。録音時に必要です:
      sudo apt install libpulse0"

# ---------------------------------------------------------------- [2/4]
say ""
if [ -d .venv ]; then
    say "[2/4] 仮想環境は作成済みです。とばします。"
else
    say "[2/4] 仮想環境を作成しています..."
    "$PYEXE" -m venv .venv || fail "仮想環境を作れませんでした"
fi
VENV_PY=".venv/bin/python"
# 既にある .venv が別の道具で作られていると pip が入っていないことがある。
# そのまま進むと「No module named pip」で止まって理由が分かりにくい
"$VENV_PY" -m pip --version >/dev/null 2>&1 \
    || fail "既にある .venv が使えません（pip が入っていません）。.venv を削除してから、もう一度 setup.sh を実行してください"

# ---------------------------------------------------------------- [3/4]
say ""
say "[3/4] 依存パッケージを入れています（約 350MB）..."
"$VENV_PY" -m pip install --upgrade pip || fail "pip を更新できませんでした"
"$VENV_PY" -m pip install -r requirements.txt || fail "依存パッケージを入れられませんでした"

# ---------------------------------------------------------------- [4/4]
# ここで入れるのは既定のモデルだけ。精度優先の large-v3 (2.9GB) は選ばれた
# 時に取得するので、初回のセットアップは小さいまま。容量は
# local_transcription/download_models.py にある。
say ""
say "[4/4] 音声認識モデルを取得しています（約 1.6GB、時間がかかります）..."
"$VENV_PY" -m local_transcription.download_models large-v3-turbo --diarization \
    || fail "モデルを取得できませんでした。通信を確認してもう一度実行してください"

# 毎回 gui.sh を探すのは面倒なので、アプリ一覧に登録する（Super キーを押して
# 名前を打てば出てくる）。デスクトップに置くかは好みなので聞く。15 秒で
# 「置かない」に倒すのは、無人実行でも止まらないようにするため。
# ここで失敗してもセットアップ自体は成功とする。
say ""
say "アプリ一覧に登録しています..."
"$VENV_PY" -m local_transcription.shortcut

say ""
answer=""
if [ -t 0 ]; then
    read -r -t 15 -p "デスクトップにもアイコンを置きますか? [y/N] (15 秒で「置かない」) " answer
    say ""
fi
case "${answer:-N}" in
    [Yy]*) "$VENV_PY" -m local_transcription.shortcut --desktop ;;
    *) say "  アプリ一覧のみ。後から置きたくなったら:"
       say "    .venv/bin/python -m local_transcription.shortcut --desktop" ;;
esac

say ""
say "============================================================"
say "  セットアップ完了"
say "============================================================"
say ""
say "  起動: Super キーを押して「会議」と入力"
say "        ./gui.sh でも起動できます"
say ""
say "  コマンドラインから:"
say "    .venv/bin/python -m local_transcription.check_devices"
say "    ./record.sh"
say "    .venv/bin/python -m local_transcription.transcribe"
say ""
