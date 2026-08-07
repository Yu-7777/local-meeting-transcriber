"""モデルも録音も要らない部分の検証."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (sys.path を通す)

import common
import config
import diarization
import download_models as dm
import record


class TestHhmmss(unittest.TestCase):
    def test_format(self):
        self.assertEqual(common.hhmmss(0), "00:00:00")
        self.assertEqual(common.hhmmss(59.9), "00:00:59")
        self.assertEqual(common.hhmmss(3661), "01:01:01")
        self.assertEqual(common.hhmmss(360000), "100:00:00")

    def test_negative_is_zero(self):
        # 時刻の引き算で負になることがある。マイナス表示は事故に見える
        self.assertEqual(common.hhmmss(-5), "00:00:00")


class TestCliHint(unittest.TestCase):
    def test_source_form(self):
        with mock.patch.object(common, "FROZEN", False):
            self.assertEqual(common.cli_hint("download", "--all"),
                             r".venv\Scripts\python.exe app.py download --all")

    def test_frozen_form(self):
        # exe には .venv も .py も無いので、案内を直書きすると嘘になる
        with mock.patch.object(common, "FROZEN", True), \
             mock.patch.object(common.sys, "executable", r"C:\x\MeetingTranscriber.exe"):
            self.assertEqual(common.cli_hint("devices"),
                             "MeetingTranscriber.exe devices")


class TestListRecordings(unittest.TestCase):
    def test_only_meta_dirs_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old = helpers.make_recording(base, "2026_01_01_00_00")
            new = helpers.make_recording(base, "2026_02_02_00_00")
            (base / "ただのフォルダ").mkdir()
            (base / "メモ.txt").write_text("x", encoding="utf-8")

            os.utime(old, (1, 1))
            os.utime(new, (time.time(), time.time()))

            self.assertEqual(common.list_recordings(base), [new, old])

    def test_missing_base(self):
        self.assertEqual(common.list_recordings(Path("存在しない場所")), [])


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self.patch = mock.patch.object(config, "CONFIG_PATH", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_missing_file(self):
        self.assertEqual(config.load(), config.DEFAULTS)

    def test_broken_json(self):
        self.path.write_text("{ 壊れた", encoding="utf-8")
        self.assertEqual(config.load(), config.DEFAULTS)

    def test_wrong_encoding(self):
        # cp932 で保存された config.json を読むと UnicodeDecodeError になる
        self.path.write_bytes('{"model": "日本語"}'.encode("cp932"))
        self.assertEqual(config.load(), config.DEFAULTS)

    def test_not_a_dict(self):
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(config.load(), config.DEFAULTS)

    def test_unknown_keys_are_dropped(self):
        self.path.write_text('{"model": "large-v3", "謎": 1}', encoding="utf-8")
        loaded = config.load()
        self.assertEqual(loaded["model"], "large-v3")
        self.assertNotIn("謎", loaded)

    def test_save_roundtrip(self):
        config.save(threads=3)
        self.assertEqual(config.load()["threads"], 3)
        # 指定しなかったキーは既定のまま残る
        self.assertEqual(config.load()["model"], config.DEFAULTS["model"])

    def test_transcripts_dir_empty_means_none(self):
        config.save(transcripts_dir="")
        self.assertIsNone(config.transcripts_dir())
        config.save(transcripts_dir=r"C:\out")
        self.assertEqual(config.transcripts_dir(), Path(r"C:\out"))


class TestRecordingNames(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(record.sanitize_name("定例MTG"), "定例MTG")
        self.assertEqual(record.sanitize_name('a/b:c*d?e"f<g>h|i'),
                         "a_b_c_d_e_f_g_h_i")
        # Windows は末尾のドット・空白を黙って落とすので、こちらで揃える
        self.assertEqual(record.sanitize_name("名前. "), "名前")
        self.assertEqual(record.sanitize_name("   "), "")
        self.assertEqual(record.sanitize_name(None), "")
        self.assertLessEqual(len(record.sanitize_name("あ" * 100)),
                             record.MAX_NAME_LEN)

    def test_split(self):
        self.assertEqual(record.split_recording_name("2026_01_02_03_04"),
                         ("2026_01_02_03_04", ""))
        self.assertEqual(record.split_recording_name("2026_01_02_03_04_定例"),
                         ("2026_01_02_03_04", "定例"))
        # 旧形式も日時として読めること（過去の録音が迷子にならない）
        self.assertEqual(record.split_recording_name("20260802_174653"),
                         ("20260802_174653", ""))
        # 読めない名前は全体を日時扱い（改名で元の名前を失わないため）
        self.assertEqual(record.split_recording_name("勝手な名前"),
                         ("勝手な名前", ""))

    def test_new_dir_avoids_collision(self):
        import datetime as dt
        now = dt.datetime(2026, 1, 2, 3, 4, 5)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = record.new_recording_dir(base, now=now)
            first.mkdir()
            second = record.new_recording_dir(base, now=now)
            # 同じ分に録り直しても、前の録音を上書きしない
            self.assertNotEqual(first, second)
            self.assertEqual(second.name, first.name + "_2")

    def test_rename_keeps_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = helpers.make_recording(Path(tmp), "2026_01_02_03_04")
            renamed = record.rename_recording(folder, "定例MTG")
            self.assertEqual(renamed.name, "2026_01_02_03_04_定例MTG")
            back = record.rename_recording(renamed, "")
            self.assertEqual(back.name, "2026_01_02_03_04")

    def test_recording_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = helpers.make_recording(Path(tmp))
            self.assertGreater(record.recording_size(folder), 0)


class TestLevels(unittest.TestCase):
    def test_db(self):
        self.assertAlmostEqual(record.level_db(1.0), 0.0)
        self.assertAlmostEqual(record.level_db(0.1), -20.0)
        self.assertEqual(record.level_db(0.0), -120.0)

    def test_ratio_is_clamped(self):
        self.assertEqual(record.level_ratio(0.0), 0.0)
        self.assertEqual(record.level_ratio(1.0), 1.0)
        self.assertEqual(record.level_ratio(2.0), 1.0)
        for level in (0.0, 0.01, 0.5, 1.0):
            self.assertGreaterEqual(record.level_ratio(level), 0.0)
            self.assertLessEqual(record.level_ratio(level), 1.0)

    def test_bar_matches_db(self):
        # CLI のバーと GUI のメーターが別々の計算を持たないこと
        graph, db = record.bar(0.1)
        self.assertEqual(db, record.level_db(0.1))
        self.assertEqual(len(graph), 16)


class TestSpeakerLabels(unittest.TestCase):
    def test_assign_picks_largest_overlap(self):
        turns = [(0.0, 10.0, 1), (4.0, 20.0, 2)]
        self.assertEqual(diarization.assign_speaker(5.0, 9.0, turns), 1)
        self.assertEqual(diarization.assign_speaker(9.0, 19.0, turns), 2)

    def test_assign_returns_none_without_overlap(self):
        self.assertIsNone(diarization.assign_speaker(50.0, 60.0, [(0.0, 1.0, 1)]))
        self.assertIsNone(diarization.assign_speaker(0.0, 1.0, []))

    def test_label_renumbers_in_order_of_appearance(self):
        segs = [{"speaker": "相手", "start": 0.0, "end": 1.0, "text": "a"},
                {"speaker": "相手", "start": 5.0, "end": 6.0, "text": "b"},
                {"speaker": "相手", "start": 10.0, "end": 11.0, "text": "c"}]
        # 間引きでクラスタ番号が飛んでいる状態
        turns = [(0.0, 2.0, 7), (5.0, 6.5, 3), (10.0, 12.0, 7)]
        self.assertEqual(diarization.label_segments(segs, turns, "相手"), 2)
        self.assertEqual([s["speaker"] for s in segs],
                         ["相手(話者1)", "相手(話者2)", "相手(話者1)"])

    def test_label_without_base(self):
        segs = [{"speaker": None, "start": 0.0, "end": 1.0, "text": "a"}]
        diarization.label_segments(segs, [(0.0, 2.0, 5)], None)
        self.assertEqual(segs[0]["speaker"], "話者1")

    def test_label_leaves_unmatched_untouched(self):
        segs = [{"speaker": "相手", "start": 90.0, "end": 91.0, "text": "a"}]
        self.assertEqual(diarization.label_segments(segs, [(0.0, 2.0, 1)], "相手"), 0)
        self.assertEqual(segs[0]["speaker"], "相手")


class TestModelMetadata(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(dm, "MODELS_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_size_lookup(self):
        self.assertEqual(dm.model_size("large-v3"), 2.9)
        self.assertIsNone(dm.model_size("存在しないモデル"))
        self.assertEqual(dm.size_text("large-v3"), "約 2.9 GB")
        self.assertEqual(dm.size_text("存在しないモデル"), "数 GB")

    def test_default_model_comes_from_config(self):
        # 既定モデルの宣言が二箇所に増えていないこと
        self.assertIn(config.DEFAULTS["model"], dm.ALL_MODELS)

    def test_not_downloaded(self):
        name = dm.ALL_MODELS[0]
        self.assertFalse(dm.is_downloaded(name))
        self.assertIn("未取得", dm.size_note(name))
        self.assertIn("初回", dm.download_notice(name))

    def test_downloaded(self):
        name = dm.ALL_MODELS[0]
        helpers.make_model_dir(self.tmp.name, dm.MODELS[name][0])
        self.assertTrue(dm.is_downloaded(name))
        self.assertEqual(dm.size_note(name), "")
        self.assertEqual(dm.download_notice(name), "")

    def test_partial_download_is_not_downloaded(self):
        # 途中で失敗するとフォルダだけ残る。本体が無ければ未取得とみなす
        name = dm.ALL_MODELS[0]
        helpers.make_model_dir(self.tmp.name, dm.MODELS[name][0], downloaded=False)
        self.assertFalse(dm.is_downloaded(name))

    def test_unknown_model_never_raises(self):
        for fn in (dm.is_downloaded, dm.size_note, dm.download_notice, dm.size_text):
            fn("存在しないモデル")

    def test_diarization_paths_shared(self):
        # 置き場所の宣言が download 側と diarization 側で食い違わないこと
        self.assertEqual(dm.SEG_PATH, diarization.SEG_MODEL)
        self.assertEqual(dm.EMB_PATH, diarization.EMB_MODEL)


class TestAtomicWrite(unittest.TestCase):
    def test_replaces_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "model.onnx"
            dm._write_atomic(target, b"abc")
            self.assertEqual(target.read_bytes(), b"abc")
            # 一時ファイルを残さない（残ると次回の判定を汚す）
            self.assertEqual(list(Path(tmp).iterdir()), [target])

    def test_failure_leaves_no_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "model.onnx"
            with mock.patch.object(Path, "write_bytes", side_effect=OSError("切断")):
                with self.assertRaises(OSError):
                    dm._write_atomic(target, b"abc")
            # 壊れたファイルが「取得済み」として残らないこと
            self.assertFalse(target.exists())


class TestMetaFormat(unittest.TestCase):
    def test_no_constant_fields(self):
        """値が変わらないフィールドを meta.json に増やさない（情報量がゼロ）."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = helpers.make_recording(Path(tmp))
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            self.assertNotIn("aligned", meta)


if __name__ == "__main__":
    unittest.main(verbosity=2)
