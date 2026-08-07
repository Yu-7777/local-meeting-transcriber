"""GUI の検証.

過去に見逃した 2 件をここで押さえる。
  1. 未取得のモデルを選ぶと AttributeError（pythonw では画面に何も出ない）
  2. 子プロセスの出力が全部「進捗」に分類され、ログ欄が空のまま
どちらも「取得済み・正常系」しか通していなかったために漏れた。
"""

import io
import queue
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401

import common
import config
import download_models as dm

try:
    import tkinter as tk
    import gui
    _root = tk.Tk()
    _root.withdraw()
    _root.destroy()
    GUI_AVAILABLE = True
except Exception as exc:  # 画面が無い環境ではまとめて飛ばす
    GUI_AVAILABLE = False
    GUI_REASON = str(exc)


@unittest.skipUnless(GUI_AVAILABLE, "tkinter を開けない環境")
class TestAppBuilds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = mock.patch.object(
            config, "CONFIG_PATH", Path(self.tmp.name) / "config.json")
        self.cfg.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = gui.App(self.root)
        self.root.update_idletasks()

    def tearDown(self):
        self.app.stop_polling()
        self.root.destroy()
        self.cfg.stop()
        self.tmp.cleanup()

    def test_model_list_matches_source(self):
        self.assertEqual(list(self.app.cb_model["values"]), dm.ALL_MODELS)

    def test_language_options(self):
        self.assertEqual(list(self.app.cb_lang["values"]), list(gui.LANGUAGES))
        # 自動判定は短い発話で外すので、既定にしない
        self.assertEqual(self.app.cb_lang.get(), "日本語")
        self.assertEqual(gui.LANGUAGES[self.app.cb_lang.get()], "ja")

    def test_file_types_follow_common(self):
        pattern = gui.AUDIO_TYPES[0][1]
        for suffix in common.AUDIO_SUFFIXES:
            self.assertIn(f"*{suffix}", pattern)

    def test_lists_initialized(self):
        for attr in ("_recordings", "_loopbacks", "_mics"):
            self.assertIsInstance(getattr(self.app, attr), list)

    def test_no_speaker_count_control(self):
        """人数指定は実測で悪化したので GUI からは出さない（README 参照）."""
        self.assertFalse(hasattr(self.app, "cb_speakers"))

    def test_controls_survive_the_minimum_size(self):
        """最小サイズまで縮めても、操作部が隠れないこと.

        伸び縮みするのはログ欄だけなので、それ以外の高さが下限に収まっていれば
        どのボタンも画面外に出ない。
        """
        boxes = self.app.winfo_children()
        log_box = boxes[-1]           # 最後がログ欄（唯一 weight を持つ行）
        fixed = self.app.winfo_reqheight() - log_box.winfo_reqheight()
        self.assertLess(fixed, gui.MIN_HEIGHT)

    def test_timers_do_not_accumulate(self):
        """自分を再予約する処理があるので、控えが増え続けないこと."""
        before = len(self.app._timers)
        for _ in range(20):
            self.app._drain_queue()
        self.assertEqual(len(self.app._timers), before)

    def test_stop_polling_clears_timers(self):
        self.app.stop_polling()
        self.assertEqual(self.app._timers, {})

    def test_tick_updates_meters_and_reschedules(self):
        """録音中のメーター更新。録音時にしか走らないので明示的に通す."""
        stub = types.SimpleNamespace(
            elapsed=65.0, pending=[],
            recorders=[types.SimpleNamespace(label="相手", level=0.5),
                       types.SimpleNamespace(label="自分", level=0.0)])
        self.app.session = stub
        try:
            self.app._tick()
            self.assertEqual(self.app.lbl_time.cget("text"), "00:01:05")
            self.assertGreater(self.app.meters["相手"][0]["value"], 0)
            self.assertEqual(self.app.meters["自分"][0]["value"], 0)
            self.assertIn("dB", self.app.meters["相手"][1].cget("text"))
            # 次回の予約が入ること（入らないとメーターが止まる）
            self.assertIn(self.app._tick, self.app._timers)
        finally:
            self.app.session = None

    def test_tick_warns_while_a_stream_is_silent(self):
        """片方が音を返さない時に気付けること（無音録音が最大の失敗）."""
        stub = types.SimpleNamespace(
            elapsed=1.0, pending=["自分"],
            recorders=[types.SimpleNamespace(label="相手", level=0.1),
                       types.SimpleNamespace(label="自分", level=0.0)])
        self.app.session = stub
        try:
            self.app._tick()
            self.assertIn("自分", self.app.lbl_state.cget("text"))
        finally:
            self.app.session = None

    def test_tick_stops_when_session_ends(self):
        self.app.session = None
        self.app._timers.pop(self.app._tick, None)
        self.app._tick()
        self.assertNotIn(self.app._tick, self.app._timers)

    def test_device_enumeration_runs_after_window_shows(self):
        # after(50) で遅らせているので、タイマーが回るまで待つ
        self.root.after(400, self.root.quit)
        self.root.mainloop()
        self.assertGreater(len(self.app._loopbacks), 0)

    def test_default_loopback_matches_recorder(self):
        """★既定 が、実際に録音されるデバイスと一致すること."""
        import record
        self.root.after(400, self.root.quit)
        self.root.mainloop()
        import pyaudiowpatch as pyaudio
        p = pyaudio.PyAudio()
        try:
            expected = record.resolve_loopback(p, None)["index"]
        finally:
            p.terminate()
        marked = [i for n, i in self.app._loopbacks if "★" in n]
        self.assertEqual(marked, [expected])


@unittest.skipUnless(GUI_AVAILABLE, "tkinter を開けない環境")
class TestNotDownloadedModel(unittest.TestCase):
    """未取得のモデルを選んだ時の経路（過去に AttributeError を出した）."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.models = mock.patch.object(dm, "MODELS_DIR", Path(self.tmp.name))
        self.cfg = mock.patch.object(
            config, "CONFIG_PATH", Path(self.tmp.name) / "config.json")
        self.models.start()
        self.cfg.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = gui.App(self.root)
        self.root.update_idletasks()

    def tearDown(self):
        self.app.stop_polling()
        self.root.destroy()
        self.cfg.stop()
        self.models.stop()
        self.tmp.cleanup()

    def test_note_shows_size(self):
        self.app.cb_model.set("large-v3")
        self.app._update_model_note()
        self.assertIn("2.9", self.app.lbl_model.cget("text"))

    def test_confirm_dialog_shows_size(self):
        asked = {}
        with mock.patch.object(gui.messagebox, "askokcancel",
                               side_effect=lambda t, m: asked.update(msg=m) or False):
            proceeded = self.app._confirm_model_download("large-v3")
        self.assertIn("2.9", asked["msg"])
        self.assertFalse(proceeded)

    def test_downloaded_model_asks_nothing(self):
        helpers.make_model_dir(self.tmp.name, dm.MODELS["large-v3"][0])
        with mock.patch.object(gui.messagebox, "askokcancel") as ask:
            self.assertTrue(self.app._confirm_model_download("large-v3"))
        ask.assert_not_called()

    def test_unknown_model_does_not_crash(self):
        self.app.cb_model.set("存在しないモデル")
        self.app._update_model_note()
        with mock.patch.object(gui.messagebox, "askokcancel", return_value=False):
            self.app._confirm_model_download("存在しないモデル")


class FakeProc:
    """_pump_output に食わせる子プロセスの替え玉."""

    def __init__(self, data):
        self.stdout = io.BytesIO(data)
        self.returncode = 0

    def wait(self):
        return 0


def classify(raw):
    """出力バイト列を (ログ, 進捗) に振り分ける."""
    app = types.SimpleNamespace(msg_queue=queue.Queue())
    gui.App._pump_output(app, FakeProc(raw))
    logs, progress = [], []
    while not app.msg_queue.empty():
        kind, payload = app.msg_queue.get_nowait()
        if kind == "log":
            logs.append(payload)
        elif kind == "progress":
            progress.append(payload)
    return logs, progress


@unittest.skipUnless(GUI_AVAILABLE, "tkinter を開けない環境")
class TestOutputRouting(unittest.TestCase):
    """ログ欄と進捗ラベルの振り分け.

    Windows の Python は print() の \\n を \\r\\n にして出す。\\r を見た時点では
    「行末」か「上書き」か決まらないため、次の 1 文字まで判断を遅らせている。
    """

    def test_windows_line_endings_go_to_log(self):
        logs, progress = classify("一行目\r\n二行目\r\n".encode("utf-8"))
        self.assertEqual(logs, ["一行目", "二行目"])
        self.assertEqual(progress, [])

    def test_bare_cr_goes_to_progress(self):
        logs, progress = classify("  10.0%\r  50.0%\r 100.0%\r".encode("utf-8"))
        self.assertEqual(logs, [])
        self.assertEqual(progress, ["  10.0%", "  50.0%", " 100.0%"])

    def test_real_shape(self):
        """進捗で上書きしたあと、完了行が \\r\\n で確定する実際の形."""
        raw = ("モデルを読み込んでいます...\r\n"
               "  10.0%\r  50.0%\r 100.0%  完了\r\n"
               "  出力: transcript.txt\r\n").encode("utf-8")
        logs, progress = classify(raw)
        self.assertEqual(logs, ["モデルを読み込んでいます...",
                                " 100.0%  完了",
                                "  出力: transcript.txt"])
        self.assertEqual(progress, ["  10.0%", "  50.0%"])

    def test_unix_line_endings_still_work(self):
        logs, progress = classify(b"a\nb\n")
        self.assertEqual(logs, ["a", "b"])
        self.assertEqual(progress, [])

    def test_trailing_text_without_newline(self):
        logs, _ = classify("最後の行".encode("utf-8"))
        self.assertEqual(logs, ["最後の行"])

    def test_blank_lines_dropped(self):
        logs, progress = classify(b"\r\n   \r\na\r\n")
        self.assertEqual(logs, ["a"])
        self.assertEqual(progress, [])

    def test_broken_utf8_does_not_raise(self):
        logs, _ = classify(b"\xff\xfe abc\r\n")
        self.assertEqual(len(logs), 1)

    def test_completion_is_reported(self):
        app = types.SimpleNamespace(msg_queue=queue.Queue())
        gui.App._pump_output(app, FakeProc(b"done\r\n"))
        kinds = []
        while not app.msg_queue.empty():
            kinds.append(app.msg_queue.get_nowait()[0])
        self.assertEqual(kinds[-1], "transcribe_done")


class TestWindowHeight(unittest.TestCase):
    """起動時の窓の大きさ（tkinter を開かずに検証する）."""

    def test_small_laptop_is_clamped(self):
        # 1366x768 でタスクバーを除いた実領域に収める
        self.assertEqual(gui.window_height(767, 768), "720x688")

    def test_large_screen_uses_content_height(self):
        self.assertEqual(gui.window_height(767, 2160), "720x791")

    def test_never_below_minimum(self):
        # 画面が極端に低くても、下限より小さくしない（縮むとログ欄が潰れる）
        self.assertEqual(gui.window_height(767, 400), f"720x{gui.MIN_HEIGHT}")

    def test_minimum_fits_a_small_laptop(self):
        self.assertLessEqual(gui.MIN_HEIGHT, 768 - gui.TASKBAR_MARGIN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
