"""デスクトップとスタートメニューにショートカットを作る.

毎回 gui.bat を探しに行くのは手間なので、セットアップの最後に作る。
リンク先は pythonw.exe を直接指すため、起動時にコンソールが一瞬も出ない
（gui.bat 経由だと黒い窓が一瞬光る）。

外部ライブラリは使わず PowerShell に .lnk を作らせる。pywin32 を足すと
依存が増え、スマート アプリ コントロールに引っかかる面が増えるため。
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from apppaths import FROZEN, ROOT

NAME = "会議録音・文字起こし"

# OneDrive にリダイレクトされている場合があるので、パスは決め打ちにせず
# Windows に問い合わせる。
PS_TEMPLATE = """
$ErrorActionPreference = 'Stop'
$target  = {target}
$args    = {args}
$workdir = {workdir}
$icon    = {icon}

$shell = New-Object -ComObject WScript.Shell
$made = @()
foreach ($folder in @('Desktop', 'Programs')) {{
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
    return pythonw, str(ROOT / "gui.py"), ROOT, pythonw


def create():
    """ショートカットを作り、作成したパスのリストを返す."""
    target, args, workdir, icon = targets()
    if not Path(target).exists():
        raise SystemExit(
            f"{target} が見つかりません。先に setup.bat を実行してください。")

    script = PS_TEMPLATE.format(
        target=_quote(target), args=_quote(args),
        workdir=_quote(workdir), icon=_quote(icon), name=NAME,
    )
    # -Command に日本語を渡すと環境によって化けるので、UTF-8 BOM 付きの
    # ファイルにして -File で渡す
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
        )
    finally:
        Path(path).unlink(missing_ok=True)

    if proc.returncode != 0:
        raise SystemExit(f"ショートカットを作れませんでした:\n{proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        made = create()
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
