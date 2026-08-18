"""スタートメニュー（希望すればデスクトップにも）にショートカットを作る.

既定をスタートメニューだけにするのは、デスクトップにアイコンが並ぶのを
嫌う人がいるため。gui.bat でなく起動するもの自体を指すのは黒い窓を出さない
ため。pywin32 を使わず PowerShell に作らせるのは依存を増やさないため。

Linux では freedesktop の .desktop ファイルを書く。~/.local/share/applications
に置くと、Super キーを押して名前を打てば出てくる（Windows のスタートメニューと
同じ使い勝手になる）。
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import common
from .apppaths import FROZEN, ROOT

NAME = "会議録音・文字起こし"
COMMENT = "会議の音声を録音してローカルで文字起こしします"
# 逆順ドメイン風の一意な名前。ここが被ると他のアプリの項目を上書きしてしまう
DESKTOP_ID = "local-meeting-transcriber"
# 専用のアイコンは持たないので、デスクトップ環境が必ず持っている名前を使う
# (freedesktop の icon naming spec)
ICON_NAME = "audio-input-microphone"

# 保存先は OneDrive にリダイレクトされることがあるので Windows に聞く
PS_TEMPLATE = """
$ErrorActionPreference = 'Stop'
$target  = {target}
$args    = {args}
$workdir = {workdir}
$icon    = {icon}

$shell = New-Object -ComObject WScript.Shell
$made = @()
foreach ($folder in @({folders})) {{
    $dir = [Environment]::GetFolderPath($folder)
    if (-not $dir -or -not (Test-Path $dir)) {{ continue }}
    $path = Join-Path $dir '{name}.lnk'
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath       = $target
    $lnk.Arguments        = $args
    $lnk.WorkingDirectory = $workdir
    $lnk.IconLocation     = $icon
    $lnk.Description      = '会議の音声を録音してローカルで文字起こしします'
    $lnk.Save()
    $made += $path
}}
$made -join "`n"
"""


def _quote(text):
    """PowerShell の単一引用符文字列にする（' は '' でエスケープ）."""
    return "'" + str(text).replace("'", "''") + "'"


def targets():
    """(実行するもの, 引数, 作業フォルダ, アイコン) を返す."""
    if FROZEN:
        exe = Path(sys.executable)
        return exe, "", ROOT, exe
    if sys.platform == "win32":
        pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
        return pythonw, "-m local_transcription.gui", ROOT, pythonw
    return (ROOT / ".venv" / "bin" / "python", "-m local_transcription.gui",
            ROOT, ICON_NAME)


def _run_quiet(cmd):
    """あれば実行する。無くても、失敗しても先へ進む（本体の登録は済んでいる）."""
    try:
        subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _exec_field(target, args):
    """.desktop の Exec 行にする（空白を含むパスのために " で囲む）."""
    escaped = str(target).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}" {args}'.strip()


def _desktop_text(target, args, workdir, icon):
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={NAME}",
        f"Comment={COMMENT}",
        f"Exec={_exec_field(target, args)}",
        f"Path={workdir}",
        f"Icon={icon}",
        "Terminal=false",
        # 主カテゴリを 2 つ書くとメニューに二重に出る（desktop-file-validate）
        "Categories=AudioVideo;Audio;Recorder;",
        # 日本語入力に切り替えなくても引けるよう、英字の手掛かりも入れる
        "Keywords=meeting;record;transcribe;会議;録音;文字起こし;",
        "",
    ])


def _desktop_dir():
    """デスクトップのフォルダ。日本語環境では ~/デスクトップ になる."""
    try:
        proc = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True,
                              text=True, timeout=10)
        folder = Path(proc.stdout.strip())
        if proc.returncode == 0 and proc.stdout.strip() and folder.is_dir():
            return folder
    except (OSError, subprocess.TimeoutExpired):
        pass
    fallback = Path.home() / "Desktop"
    return fallback if fallback.is_dir() else None


def _create_desktop_entries(target, args, workdir, icon, desktop):
    """freedesktop の .desktop を書く。作成したパスのリストを返す."""
    text = _desktop_text(target, args, workdir, icon)
    data_home = Path(os.environ.get("XDG_DATA_HOME")
                     or Path.home() / ".local" / "share")
    apps = data_home / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    entry = apps / f"{DESKTOP_ID}.desktop"
    entry.write_text(text, encoding="utf-8")
    made = [str(entry)]

    if desktop:
        folder = _desktop_dir()
        if folder is not None:
            on_desktop = folder / f"{DESKTOP_ID}.desktop"
            on_desktop.write_text(text, encoding="utf-8")
            on_desktop.chmod(0o755)
            # GNOME は「信頼済み」の印が無いとダブルクリックで起動しない
            _run_quiet(["gio", "set", str(on_desktop),
                        "metadata::trusted", "true"])
            made.append(str(on_desktop))

    # 一覧の索引。更新できなくてもいずれ反映されるので、失敗しても続ける
    _run_quiet(["update-desktop-database", str(apps)])
    return made


def create(desktop=False):
    """ショートカットを作り、作成したパスのリストを返す.

    スタートメニュー（Linux ではアプリ一覧）には必ず作る。
    デスクトップは desktop=True の時だけ。
    """
    target, args, workdir, icon = targets()
    if not Path(target).exists():
        setup = "setup.bat" if sys.platform == "win32" else "setup.sh"
        raise SystemExit(f"{target} が見つかりません。先に {setup} を実行してください。")
    if sys.platform != "win32":
        return _create_desktop_entries(target, args, workdir, icon, desktop)

    folders = ["'Programs'"] + (["'Desktop'"] if desktop else [])
    script = PS_TEMPLATE.format(
        target=_quote(target), args=_quote(args),
        workdir=_quote(workdir), icon=_quote(icon), name=NAME,
        folders=", ".join(folders),
    )
    # -Command 経由だと日本語が化けるので、BOM 付きファイルを -File で渡す
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                     encoding="utf-8-sig") as f:
        f.write(script)
        path = f.name
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            # setup.bat から呼ばれるので、返らないと導入手順全体が止まる
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit("ショートカット作成 (PowerShell) が 60 秒で返りませんでした。")
    finally:
        Path(path).unlink(missing_ok=True)

    if proc.returncode != 0:
        raise SystemExit(f"ショートカットを作れませんでした:\n{proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def main():
    ap = argparse.ArgumentParser(description="起動用のショートカットを作る")
    ap.add_argument("--desktop", action="store_true",
                    help="デスクトップにも作る（既定はスタートメニューのみ）")
    args = ap.parse_args()

    common.use_utf8_stdout()
    try:
        made = create(desktop=args.desktop)
    except SystemExit as exc:
        print(f"  ※ {exc}")
        return 0  # ショートカットが作れなくてもセットアップは成功扱いにする
    for path in made:
        print(f"  作成しました: {path}")
    if not made:
        print("  ※ ショートカットの作成先が見つかりませんでした。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
