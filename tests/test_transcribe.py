"""文字起こしの入出力の組み立て（モデルは読まない）.

build_plan は「どの WAV を、どこに、どの名前で出すか」を決める要。
ここを間違えると、別の録音の結果を静かに上書きする。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

# helpers がリポジトリのルートを sys.path に足すので、先に読む
import helpers

from local_transcription import config
from local_transcription import transcribe


class TestResolveOutdir(unittest.TestCase):
    def test_explicit_wins(self):
        with mock.patch.object(config, "transcripts_dir", return_value=Path(r"C:\cfg")):
            self.assertEqual(transcribe.resolve_outdir(r"C:\explicit", r"C:\fallback"),
                             Path(r"C:\explicit"))

    def test_config_beats_fallback(self):
        with mock.patch.object(config, "transcripts_dir", return_value=Path(r"C:\cfg")):
            self.assertEqual(transcribe.resolve_outdir(None, r"C:\fallback"),
                             Path(r"C:\cfg"))

    def test_fallback_when_unset(self):
        with mock.patch.object(config, "transcripts_dir", return_value=None):
            self.assertEqual(transcribe.resolve_outdir(None, r"C:\fallback"),
                             Path(r"C:\fallback"))


class TestBuildPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.folder = helpers.make_recording(self.base)
        # 設定値に引きずられないよう、明示指定だけを見る状態にする
        self.patch = mock.patch.object(config, "transcripts_dir", return_value=None)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_recording_folder(self):
        path, items, meta = transcribe.build_plan(self.folder)
        self.assertEqual(path, self.folder / "transcript.txt")
        self.assertEqual([label for _, label, _ in items], ["相手", "自分"])
        # 話者分離をかけるのは相手だけ（自分は物理的に分かれている）
        self.assertEqual([d for _, _, d in items], [True, False])
        self.assertEqual(meta["wall_duration_sec"], 0.1)

    def test_elsewhere_gets_the_recording_name(self):
        """別の場所に出す時は録音名を頭に付ける.

        付けないと録音ごとに transcript.txt になり、前の議事録を静かに潰す。
        """
        other = self.base / "議事録"
        path, _, _ = transcribe.build_plan(self.folder, other)
        self.assertEqual(path, other / f"{self.folder.name}_transcript.txt")

    def test_two_recordings_do_not_collide(self):
        other = self.base / "議事録"
        second = helpers.make_recording(self.base, "2026_09_09_09_09")
        first_path, _, _ = transcribe.build_plan(self.folder, other)
        second_path, _, _ = transcribe.build_plan(second, other)
        self.assertNotEqual(first_path, second_path)

    def test_in_place_keeps_plain_name(self):
        path, _, _ = transcribe.build_plan(self.folder, self.folder)
        self.assertEqual(path.name, "transcript.txt")

    def test_missing_wav_is_skipped(self):
        (self.folder / "mic.wav").unlink()
        _, items, _ = transcribe.build_plan(self.folder)
        self.assertEqual([label for _, label, _ in items], ["相手"])

    def test_single_stream_recording(self):
        folder = helpers.make_recording(self.base, "2026_09_09_09_09", both=False)
        _, items, _ = transcribe.build_plan(folder)
        self.assertEqual(len(items), 1)

    def test_single_audio_file(self):
        wav = self.folder / "system.wav"
        path, items, meta = transcribe.build_plan(wav)
        self.assertEqual(len(items), 1)
        # 誰の声か分からないので 相手/自分 を付けない
        self.assertIsNone(items[0][1])
        self.assertTrue(items[0][2])
        self.assertEqual(meta, {"single_file": True})
        # 元のファイル名を残す（同じフォルダに複数置いても潰し合わない）
        self.assertEqual(path, wav.parent / "system_transcript.txt")

    def test_unsupported_suffix(self):
        bad = self.base / "資料.pdf"
        bad.write_bytes(b"x")
        with self.assertRaises(SystemExit):
            transcribe.build_plan(bad)

    def test_folder_without_meta(self):
        plain = self.base / "ただのフォルダ"
        plain.mkdir()
        with self.assertRaises(SystemExit) as cm:
            transcribe.build_plan(plain)
        self.assertIn("meta.json", str(cm.exception))

    def test_missing_path(self):
        with self.assertRaises(SystemExit):
            transcribe.build_plan(self.base / "無い")

    def test_outdir_is_created(self):
        other = self.base / "作られる" / "深い場所"
        path, _, _ = transcribe.build_plan(self.folder, other)
        self.assertTrue(path.parent.is_dir())

    def test_every_supported_suffix_is_accepted(self):
        from local_transcription import common
        for suffix in common.AUDIO_SUFFIXES:
            path = self.base / f"音声{suffix}"
            path.write_bytes(b"x")
            transcribe.build_plan(path)  # 例外が出ないこと


class TestLatestRecording(unittest.TestCase):
    def test_no_recordings(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                transcribe.latest_recording(Path(tmp))


class TestFormatTranscript(unittest.TestCase):
    """出力書式。壊すと過去の議事録と見た目が変わるので固定する."""

    SEGMENTS = [
        {"speaker": "相手(話者1)", "start": 0.0, "end": 2.0, "text": "おはようございます"},
        {"speaker": "自分", "start": 3.5, "end": 5.0, "text": "よろしくお願いします"},
        {"speaker": None, "start": 3700.0, "end": 3702.0, "text": "誰か分からない発言"},
    ]
    META = {"started_at": "2026-01-02T03:04:05+09:00", "wall_duration_sec": 3702}

    def test_recording_header(self):
        lines = transcribe.format_transcript(
            self.SEGMENTS, self.META, "system.wav", "large-v3-turbo")
        self.assertEqual(lines[0], "# 会議文字起こし")
        self.assertEqual(lines[1], "# 録音日時 : 2026-01-02T03:04:05+09:00")
        self.assertEqual(lines[2], "# 録音長   : 01:01:42")
        self.assertEqual(lines[3], "# モデル   : large-v3-turbo")
        self.assertEqual(lines[4], "")

    def test_single_file_header(self):
        lines = transcribe.format_transcript(
            self.SEGMENTS, {"single_file": True}, "会議.mp4", "large-v3")
        self.assertEqual(lines[1], "# 元ファイル : 会議.mp4")

    def test_body(self):
        lines = transcribe.format_transcript(
            self.SEGMENTS, self.META, "system.wav", "large-v3-turbo")[5:]
        self.assertEqual(lines, [
            "[00:00:00] 相手(話者1): おはようございます",
            "[00:00:03] 自分: よろしくお願いします",
            # 話者不明なら「誰か: 」を付けない（嘘の帰属をしない）
            "[01:01:40] 誰か分からない発言",
        ])

    def test_missing_metadata_does_not_crash(self):
        lines = transcribe.format_transcript([], {}, "x.wav", "m")
        self.assertEqual(lines[1], "# 録音日時 : ?")
        self.assertEqual(lines[2], "# 録音長   : 00:00:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
