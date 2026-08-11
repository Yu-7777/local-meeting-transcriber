"""スタートメニュー（希望すればデスクトップにも）にショートカットを作る.

既定をスタートメニューだけにするのは、デスクトップにアイコンが並ぶのを
嫌う人がいるため。gui.bat でなく起動するもの自体を指すのは黒い窓を出さない
ため。pywin32 を使わず PowerShell に作らせるのは依存を増やさないため。
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from . import common
from .apppaths import FROZEN, ROOT

NAME = "会議録音・文字起こし"

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
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    return pythonw, "-m local_transcription.gui", ROOT, pythonw


def create(desktop=False):
    """ショートカットを作り、作成したパスのリストを返す.

    スタートメニューには必ず作る。デスクトップは desktop=True の時だけ。
    """
    target, args, workdir, icon = targets()
    if not Path(target).exists():
        raise SystemExit(
            f"{target} が見つかりません。先に setup.bat を実行してください。")

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
