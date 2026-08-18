"""音声バックエンド（OS ごとの実装）の検証.

実際の音声装置に依存しない部分だけを見る。装置を開く経路は自動テストに
載せられないので、そこは実機での確認に任せる（BUILD.md「何が守られていないか」）。
"""

import sys
import unittest

# helpers がリポジトリのルートを sys.path に足すので、先に読む
import helpers

from local_transcription import audio
from local_transcription import audio_pulse


class TestBackendSelection(unittest.TestCase):
    def test_backend_matches_platform(self):
        """OS 判定は audio.py の 1 箇所だけ。取り違えると無音録音になる."""
        expected = ("local_transcription.audio_wasapi" if sys.platform == "win32"
                    else "local_transcription.audio_pulse")
        self.assertEqual(audio.AudioSystem.__module__, expected)
        self.assertEqual(audio.DeviceWatcher.__module__, expected)

    def test_both_backends_are_present(self):
        """一方の OS でしか動かせないので、取り違え・消し忘れをここで止める."""
        for name in ("audio_wasapi.py", "audio_pulse.py"):
            self.assertTrue((helpers.ROOT / "local_transcription" / name).exists(),
                            f"{name} が無い")

    def test_backend_provides_the_whole_interface(self):
        for name in ("AudioSystem", "DeviceWatcher", "print_devices"):
            self.assertTrue(hasattr(audio, name), name)
        for name in ("list_devices", "resolve_loopback", "resolve_mic",
                     "open_stream", "close"):
            self.assertTrue(hasattr(audio.AudioSystem, name), name)
        # stop() が無いと、窓を開け閉めするたびに接続が積み上がる
        for name in ("start", "stop"):
            self.assertTrue(hasattr(audio.DeviceWatcher, name), name)


class TestTimeline(unittest.TestCase):
    """受け取ったチャンクに時刻を割り当てる部分.

    2 本の WAV の時間軸はここで決まる。実測時刻をそのまま使うとバッファの
    揺らぎが時間軸の揺らぎになり、無音が挿し込まれて音がずれていく。
    """

    RATE, CHUNK = 48000, 1024        # 1 チャンク = 21.3ms

    def timeline(self):
        return audio_pulse._Timeline(self.RATE, self.CHUNK, resync_sec=0.30)

    def test_timestamps_advance_by_exactly_one_chunk(self):
        t = self.timeline()
        first, _ = t.stamp(100.0)
        second, _ = t.stamp(100.0 + self.CHUNK / self.RATE)
        self.assertAlmostEqual(second - first, self.CHUNK / self.RATE, places=9)

    def test_jitter_does_not_move_the_timeline(self):
        """遅延の測定値が揺れても、時刻は一定の間隔で進むこと."""
        t = self.timeline()
        step = self.CHUNK / self.RATE
        stamps = []
        for i, jitter in enumerate([0.0, 0.05, -0.04, 0.12, -0.09, 0.02]):
            ts, overflow = t.stamp(100.0 + i * step + jitter)
            stamps.append(ts)
            self.assertFalse(overflow, f"{jitter} で取りこぼし扱いになった")
        diffs = [b - a for a, b in zip(stamps, stamps[1:])]
        for d in diffs:
            self.assertAlmostEqual(d, step, places=9)

    def test_real_loss_moves_the_timeline_and_is_reported(self):
        """本当に取りこぼしたら、時刻を実測に合わせて呼び出し側に埋めさせる."""
        t = self.timeline()
        step = self.CHUNK / self.RATE
        t.stamp(100.0)
        ts, overflow = t.stamp(100.0 + step + 1.0)   # 1 秒ぶん飛んだ
        self.assertTrue(overflow)
        self.assertAlmostEqual(ts, 100.0 + step + 1.0, places=9)
        # 引き直した後は、また一定間隔で進む
        ts2, overflow2 = t.stamp(100.0 + 2 * step + 1.0)
        self.assertFalse(overflow2)
        self.assertAlmostEqual(ts2 - ts, step, places=9)

    def test_first_chunk_starts_at_its_own_head(self):
        """先頭チャンクの開始時刻がずれると、2 本の頭が揃わない."""
        t = self.timeline()
        ts, _ = t.stamp(100.0)
        self.assertAlmostEqual(ts, 100.0, places=9)


class _StubConnection:
    """libpulse の応答を模したもの（実機の構成に依存しないため）."""

    def defaults(self):
        return "sink_hdmi", "source_mic_usb"

    def sinks(self):
        return [{"index": 1, "name": "sink_hdmi", "description": "HDMI 出力",
                 "monitor_source_name": "sink_hdmi.monitor",
                 "rate": 48000, "channels": 2},
                {"index": 2, "name": "sink_speaker", "description": "スピーカー",
                 "monitor_source_name": "sink_speaker.monitor",
                 "rate": 44100, "channels": 2}]

    def sources(self):
        return [{"index": 10, "name": "sink_hdmi.monitor",
                 "description": "Monitor of HDMI 出力", "monitor_of": "sink_hdmi",
                 "rate": 48000, "channels": 2},
                {"index": 11, "name": "sink_speaker.monitor",
                 "description": "Monitor of スピーカー",
                 "monitor_of": "sink_speaker", "rate": 44100, "channels": 2},
                {"index": 12, "name": "source_mic_usb", "description": "USB マイク",
                 "monitor_of": None, "rate": 48000, "channels": 1}]


class TestPulseDeviceListing(unittest.TestCase):
    """PulseAudio 側の一覧の作り方（実機が無くても確かめられる部分）."""

    def setUp(self):
        self.system = object.__new__(audio_pulse.AudioSystem)
        self.system._conn = _StubConnection()

    def test_monitor_is_shown_with_the_output_device_name(self):
        """"Monitor of X" は環境で訳が変わるので、出力デバイス名を引き当てる."""
        loopbacks, mics = self.system.list_devices()
        self.assertEqual(loopbacks, [("HDMI 出力 ★既定", 10), ("スピーカー", 11)])
        self.assertEqual(mics, [("USB マイク ★既定", 12)])

    def test_default_loopback_follows_the_default_sink(self):
        info = self.system.resolve_loopback()
        self.assertEqual(info["index"], 10)
        self.assertEqual(info["source"], "sink_hdmi.monitor")
        self.assertEqual(info["name"], "HDMI 出力")

    def test_device_info_has_the_shape_the_recorder_expects(self):
        """record.StreamRecorder が読む key が揃っていること."""
        info = self.system.resolve_mic()
        self.assertEqual(info["index"], 12)
        self.assertEqual(int(info["defaultSampleRate"]), 48000)
        self.assertEqual(int(info["maxInputChannels"]), 1)
        self.assertIsInstance(info["name"], str)

    def test_named_index_is_honoured(self):
        self.assertEqual(self.system.resolve_loopback(11)["source"],
                         "sink_speaker.monitor")

    def test_a_microphone_index_is_refused_as_loopback(self):
        """マイクを「相手」に選ぶと、相手の声が録れないまま会議が終わる."""
        with self.assertRaises(SystemExit):
            self.system.resolve_loopback(12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
