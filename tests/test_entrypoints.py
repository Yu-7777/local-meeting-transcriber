"""入口まわりの検証（app / apppaths / shortcut / 削除）.

いずれも関数カバレッジが 0% だった箇所。音声装置もモデルも要らないのに
通していなかった。特に録音の削除は、取り違えると取り返しがつかない。
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# helpers がリポジトリのルートを sys.path に足すので、先に読む
import helpers

from local_transcription import app
from local_transcription import apppaths
from local_transcription import record
from local_transcription import shortcut


class TestDispatch(unittest.TestCase):
    """app.py の振り分け。exe でも同じ経路を通るので、崩すと全部が止まる."""

    def run_main(self, argv):
        """sys.argv を差し替えて app.main() を呼び、呼ばれたモジュールを返す."""
        called = {}

        class FakeModule:
            @staticmethod
            def main():
                called["argv"] = list(sys.argv)
                return 0

        def fake_import(name, package=None):
            called["module"] = name
            called["package"] = package
            return FakeModule

        with mock.patch.object(sys, "argv", ["app.py", *argv]), \
             mock.patch.object(app.importlib, "import_module", fake_import):
            code = app.main()
        return code, called

    def test_each_subcommand_reaches_its_module(self):
        for sub, module in app.COMMANDS.items():
            with self.subTest(sub=sub):
                _, called = self.run_main([sub])
                self.assertEqual(called["module"], f".{module}")
                self.assertEqual(called["package"], "local_transcription")

    def test_arguments_are_forwarded_without_the_subcommand(self):
        # 子側は argparse で読むので、サブコマンド名が残っていると誤解する
        _, called = self.run_main(["transcribe", "録音", "--diarize"])
        self.assertEqual(called["argv"][1:], ["録音", "--diarize"])

    def test_no_argument_opens_the_gui(self):
        with mock.patch.object(sys, "argv", ["app.py"]), \
             mock.patch.dict(sys.modules,
                              {"local_transcription.gui": mock.Mock(main=lambda: 7)}):
            self.assertEqual(app.main(), 7)

    def test_unknown_subcommand_opens_the_gui(self):
        """知らない語で黙って別のことをしない（以前は download に落ちていた）."""
        with mock.patch.object(sys, "argv", ["app.py", "しらない語"]), \
             mock.patch.dict(sys.modules,
                              {"local_transcription.gui": mock.Mock(main=lambda: 7)}):
            self.assertEqual(app.main(), 7)

    def test_commands_point_at_real_modules(self):
        for module in app.COMMANDS.values():
            self.assertTrue(
                (helpers.ROOT / "local_transcription" / f"{module}.py").exists(), module)


class TestChildCommand(unittest.TestCase):
    """GUI が自分自身を呼び直す経路。exe には python.exe が無い."""

    def test_source_form(self):
        with mock.patch.object(apppaths, "FROZEN", False):
            cmd = apppaths.child_command("transcribe", Path("録音"), "--diarize")
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1], str(apppaths.ROOT / "app.py"))
        self.assertEqual(cmd[2:], ["transcribe", "録音", "--diarize"])

    def test_frozen_form(self):
        with mock.patch.object(apppaths, "FROZEN", True), \
             mock.patch.object(sys, "executable", r"C:\x\MeetingTranscriber.exe"):
            cmd = apppaths.child_command("devices")
        # exe 自身を呼ぶ。app.py を挟むと凍結後に存在しない
        self.assertEqual(cmd, [r"C:\x\MeetingTranscriber.exe", "devices"])

    def test_arguments_become_strings(self):
        cmd = apppaths.child_command("transcribe", Path("a"), 5)
        self.assertTrue(all(isinstance(x, str) for x in cmd))


class TestShortcutTargets(unittest.TestCase):
    def test_quote_escapes_single_quotes(self):
        # PowerShell へ渡すので、' を含むユーザー名で壊れないこと
        self.assertEqual(shortcut._quote("O'Brien"), "'O''Brien'")
        self.assertEqual(shortcut._quote(Path(r"C:\x")), r"'C:\x'")

    def test_source_targets_use_pythonw(self):
        with mock.patch.object(shortcut, "FROZEN", False):
            exe, args, cwd, icon = shortcut.targets()
        # pythonw なら黒いコンソールが出ない
        self.assertEqual(exe.name, "pythonw.exe")
        self.assertEqual(args, "-m local_transcription.gui")
        self.assertEqual(cwd, shortcut.ROOT)

    def test_frozen_targets_use_the_exe_itself(self):
        with mock.patch.object(shortcut, "FROZEN", True), \
             mock.patch.object(sys, "executable", r"C:\x\MeetingTranscriber.exe"):
            exe, args, cwd, icon = shortcut.targets()
        self.assertEqual(exe, Path(r"C:\x\MeetingTranscriber.exe"))
        self.assertEqual(args, "")
        self.assertEqual(icon, exe)


class TestMoveToTrash(unittest.TestCase):
    """録音の削除。完全削除ではなくごみ箱であることが要件.

    実際にごみ箱へ入るので、テストを流すとごみ箱に一時フォルダが 1 つ残る。
    取り違えると録音が失われる処理なので、実物で確かめる価値のほうが大きい。
    """

    def test_missing_path_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                record.move_to_trash(Path(tmp) / "無い")

    @staticmethod
    def recycle_bin_names():
        """ごみ箱の中身を名前で返す。照会できない環境では None."""
        # 出力を UTF-8 にしないと、既定の cp932 で日本語名が化ける
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              "$items = (New-Object -ComObject Shell.Application)"
              ".NameSpace(10).Items(); foreach ($i in $items) "
              "{ Write-Output $i.Name }")
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps], timeout=60,
                capture_output=True, text=True, encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0:
            return None
        return [n for n in r.stdout.splitlines() if n.strip()]

    def test_folder_actually_goes_to_the_recycle_bin(self):
        """消えるだけでは不足。完全削除だと録音が二度と戻らない."""
        before = self.recycle_bin_names()
        if before is None:
            self.skipTest("ごみ箱を照会できない環境")
        # 既存の項目と紛れないよう、この実行だけの名前にする
        name = f"テスト用_ごみ箱確認_{len(before)}_{id(self)}"
        with tempfile.TemporaryDirectory() as tmp:
            folder = helpers.make_recording(Path(tmp), name)
            record.move_to_trash(folder)
            self.assertFalse(folder.exists())
            # 親は残す（保存先ごと消してはいけない）
            self.assertTrue(Path(tmp).is_dir())
        after = self.recycle_bin_names()
        self.assertIn(name, after,
                      "ごみ箱に入っていない = 完全削除されている")


class TestDeviceListing(unittest.TestCase):
    """デバイス一覧。以前 ® を含む機器名で UnicodeEncodeError を出した."""

    def test_runs_without_crashing(self):
        r = subprocess.run(
            [sys.executable, "app.py", "devices"], cwd=helpers.ROOT,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120)
        if "PortAudio" in r.stderr or "pyaudio" in r.stderr.lower():
            self.skipTest("音声装置を開けない環境")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertIn("ループバック", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
