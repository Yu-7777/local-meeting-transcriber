"""会議録音・文字起こしツールの簡易 GUI.

録音の開始/停止と、文字起こしの実行だけを行う。
結果はファイルに出力する（出力先によって名前が変わる。transcribe.py 参照）。

録音は record.RecordingSession をそのまま使い、文字起こしは app.py 経由で
サブプロセスとして呼ぶ（exe 化しても同じ経路になる）。処理の実体を GUI 側に
複製しないため。
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import pyaudiowpatch as pyaudio

import config
import record

import common
import download_models
from apppaths import ROOT, child_command

MODELS = download_models.ALL_MODELS
# 先頭が既定。自動判定は短い発話で外すことがあるので、既定にはしない
LANGUAGES = {"日本語": "ja", "英語": "en", "自動判定": "auto"}
FILE_LOCKED_HINT = "音声ファイルを開いているアプリがあれば閉じてください。"
# 対応形式は common の定義から作る（片方だけ増えて選べなくなるのを防ぐ）
AUDIO_TYPES = [("音声・動画ファイル",
                " ".join(f"*{s}" for s in sorted(common.AUDIO_SUFFIXES))),
               ("すべてのファイル", "*.*")]


def list_devices():
    """(ループバック一覧, マイク一覧) を返す。要素は (表示名, index)."""
    p = pyaudio.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        try:
            # 録音時と同じ手順で決める。GUI が独自に選ぶと ★既定 の表示と
            # 実際に録音されるデバイスが食い違う
            default_lb = record.resolve_loopback(p, None)["index"]
        except (Exception, SystemExit):
            default_lb = None  # 決められなくても一覧は出す（手で選べる）
        default_mic = wasapi["defaultInputDevice"]

        loopbacks, mics = [], []
        for lb in p.get_loopback_device_info_generator():
            mark = " ★既定" if lb["index"] == default_lb else ""
            name = lb["name"].replace(" [Loopback]", "")
            loopbacks.append((f"{name}{mark}", lb["index"]))

        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] != wasapi["index"]:
                continue
            if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice", False):
                mark = " ★既定" if d["index"] == default_mic else ""
                mics.append((f"{d['name']}{mark}", d["index"]))
        return loopbacks, mics
    finally:
        p.terminate()


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.session = None
        self.proc = None
        self.msg_queue = queue.Queue()
        self._recordings = []
        self._loopbacks = []
        self._mics = []

        self._build_record_box()
        self._build_transcribe_box()
        self._build_log_box()

        # 列挙は初回だけ 0.3 秒ほどかかるので窓を先に出す。after(0) では
        # まだ窓が表示されていないため 0 にしない。
        self.after(50, self.refresh_devices)
        self.refresh_recordings()
        self.after(100, self._drain_queue)

    # ---------------------------------------------------------------- 録音
    def _build_record_box(self):
        box = ttk.LabelFrame(self, text=" 録音 ", padding=10)
        box.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="相手 (PC の音):").grid(row=0, column=0, sticky="w", pady=2)
        self.cb_loopback = ttk.Combobox(box, state="readonly", width=46)
        self.cb_loopback.grid(row=0, column=1, sticky="ew", padx=(6, 4), pady=2)

        ttk.Label(box, text="自分 (マイク):").grid(row=1, column=0, sticky="w", pady=2)
        self.cb_mic = ttk.Combobox(box, state="readonly", width=46)
        self.cb_mic.grid(row=1, column=1, sticky="ew", padx=(6, 4), pady=2)

        ttk.Button(box, text="更新", width=6, command=self.refresh_devices).grid(
            row=0, column=2, rowspan=2, padx=2)

        ttk.Label(box, text="保存先:").grid(row=2, column=0, sticky="w", pady=(6, 2))
        self.var_savedir = tk.StringVar(value=str(config.recordings_dir()))
        ttk.Entry(box, textvariable=self.var_savedir, state="readonly").grid(
            row=2, column=1, sticky="ew", padx=(6, 4), pady=(6, 2))
        ttk.Button(box, text="変更", width=6, command=self.choose_savedir).grid(
            row=2, column=2, padx=2, pady=(6, 2))

        ttk.Label(box, text="名前 (任意):").grid(row=3, column=0, sticky="w", pady=2)
        self.var_recname = tk.StringVar()
        ttk.Entry(box, textvariable=self.var_recname).grid(
            row=3, column=1, sticky="ew", padx=(6, 4), pady=2)
        ttk.Label(box, text="例: 定例MTG", foreground="gray").grid(
            row=3, column=2, sticky="w", padx=2)

        ctrl = ttk.Frame(box)
        ctrl.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 2))
        ctrl.columnconfigure(2, weight=1)

        self.btn_record = ttk.Button(ctrl, text="● 録音開始", width=14,
                                     command=self.toggle_record)
        self.btn_record.grid(row=0, column=0)
        self.lbl_time = ttk.Label(ctrl, text="00:00:00",
                                  font=("Consolas", 16))
        self.lbl_time.grid(row=0, column=1, padx=14)
        self.lbl_state = ttk.Label(ctrl, text="待機中", foreground="gray")
        self.lbl_state.grid(row=0, column=2, sticky="w")

        self.var_auto = tk.BooleanVar(value=config.load()["auto_transcribe"])
        ttk.Checkbutton(box, text="録音を停止したら、そのまま文字起こしを開始する",
                        variable=self.var_auto, command=self._save_auto).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

        meters = ttk.Frame(box)
        meters.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        meters.columnconfigure(1, weight=1)
        self.meters = {}
        for i, label in enumerate(("相手", "自分")):
            ttk.Label(meters, text=label, width=4).grid(row=i, column=0, sticky="w")
            pb = ttk.Progressbar(meters, maximum=100, length=260)
            pb.grid(row=i, column=1, sticky="ew", padx=6, pady=1)
            db = ttk.Label(meters, text="  -- dB", width=9, font=("Consolas", 9))
            db.grid(row=i, column=2, sticky="e")
            self.meters[label] = (pb, db)

    # ---------------------------------------------------------- 文字起こし
    def _build_transcribe_box(self):
        box = ttk.LabelFrame(self, text=" 文字起こし ", padding=10)
        box.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="対象の録音:").grid(row=0, column=0, sticky="w", pady=2)
        self.cb_rec = ttk.Combobox(box, state="readonly", width=46)
        self.cb_rec.grid(row=0, column=1, sticky="ew", padx=(6, 4), pady=2)
        self.cb_rec.bind("<<ComboboxSelected>>", lambda e: self._clear_picked())
        recbtn = ttk.Frame(box)
        recbtn.grid(row=0, column=2, padx=2, pady=2)
        ttk.Button(recbtn, text="更新", width=5,
                   command=self.refresh_recordings).grid(row=0, column=0)
        ttk.Button(recbtn, text="改名", width=5, command=self.rename_recording).grid(
            row=0, column=1, padx=(2, 0))
        ttk.Button(recbtn, text="削除", width=5, command=self.delete_recording).grid(
            row=0, column=2, padx=(2, 0))

        pick = ttk.Frame(box)
        pick.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 4))
        ttk.Button(pick, text="音声ファイルを選ぶ...", width=20,
                   command=self.choose_audio_file).grid(row=0, column=0)
        self.lbl_picked = ttk.Label(pick, text="", foreground="#06c")
        self.lbl_picked.grid(row=0, column=1, sticky="w", padx=8)
        self.picked_file = None

        ttk.Label(box, text="出力先:").grid(row=2, column=0, sticky="w", pady=2)
        self.outdir = config.transcripts_dir()
        self.var_outdir = tk.StringVar(value=self._outdir_text())
        ttk.Entry(box, textvariable=self.var_outdir, state="readonly").grid(
            row=2, column=1, sticky="ew", padx=(6, 4), pady=2)
        outbtn = ttk.Frame(box)
        outbtn.grid(row=2, column=2, padx=2, pady=2)
        ttk.Button(outbtn, text="変更", width=5, command=self.choose_outdir).grid(
            row=0, column=0)
        ttk.Button(outbtn, text="既定", width=5, command=self.reset_outdir).grid(
            row=0, column=1, padx=(2, 0))

        ttk.Label(box, text="モデル:").grid(row=3, column=0, sticky="w", pady=2)
        self.cb_model = ttk.Combobox(box, state="readonly", values=MODELS, width=22)
        saved_model = config.load()["model"]
        self.cb_model.current(MODELS.index(saved_model) if saved_model in MODELS else 0)
        self.cb_model.grid(row=3, column=1, sticky="w", padx=(6, 4), pady=2)
        self.cb_model.bind("<<ComboboxSelected>>", lambda e: self._update_model_note())
        self.lbl_model = ttk.Label(box, text="", foreground="#a60")
        self.lbl_model.grid(row=3, column=1, sticky="e", padx=(0, 4))
        self._update_model_note()

        # 言語が実際と違うと、Whisper は無理に翻訳せず幻聴を書き出す
        ttk.Label(box, text="言語:").grid(row=4, column=0, sticky="w", pady=2)
        self.cb_lang = ttk.Combobox(box, state="readonly", width=22,
                                    values=list(LANGUAGES))
        self.cb_lang.current(0)
        self.cb_lang.grid(row=4, column=1, sticky="w", padx=(6, 4), pady=2)

        opt = ttk.Frame(box)
        opt.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 2))
        self.var_diarize = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="相手を話者ごとに分ける", variable=self.var_diarize,
                        command=self._toggle_speakers).grid(row=0, column=0, sticky="w")
        ttk.Label(opt, text="  相手の人数:").grid(row=0, column=1, sticky="w")
        self.var_speakers = tk.StringVar(value="自動")
        self.cb_speakers = ttk.Combobox(
            opt, state="disabled", width=6, textvariable=self.var_speakers,
            values=["自動", "2", "3", "4", "5", "6", "7", "8"])
        self.cb_speakers.grid(row=0, column=2, padx=4)

        run = ttk.Frame(box)
        run.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.btn_transcribe = ttk.Button(run, text="文字起こしを実行", width=18,
                                         command=self.start_transcribe)
        self.btn_transcribe.grid(row=0, column=0)
        ttk.Button(run, text="出力先を開く", width=14, command=self.open_folder).grid(
            row=0, column=1, padx=8)

    def _build_log_box(self):
        box = ttk.LabelFrame(self, text=" ログ ", padding=6)
        box.grid(row=2, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self.txt = tk.Text(box, height=11, wrap="none", font=("Consolas", 9))
        self.txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.txt.configure(yscrollcommand=sb.set, state="disabled")

        self.lbl_progress = ttk.Label(box, text="", font=("Consolas", 9),
                                      foreground="#0a6")
        self.lbl_progress.grid(row=1, column=0, sticky="w", pady=(4, 0))

    # ----------------------------------------------------------- ユーティリティ
    def log(self, text):
        self.txt.configure(state="normal")
        self.txt.insert("end", text + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def refresh_devices(self):
        try:
            loopbacks, mics = list_devices()
        except Exception as exc:
            messagebox.showerror("デバイス取得に失敗", str(exc))
            return
        self._loopbacks, self._mics = loopbacks, mics
        self.cb_loopback["values"] = [n for n, _ in loopbacks]
        self.cb_mic["values"] = [n for n, _ in mics]
        for cb, items in ((self.cb_loopback, loopbacks), (self.cb_mic, mics)):
            if items:
                default = next((i for i, (n, _) in enumerate(items) if "★" in n), 0)
                cb.current(default)

    def refresh_recordings(self):
        self._recordings = common.list_recordings(self.var_savedir.get())
        self.cb_rec["values"] = [d.name for d in self._recordings]
        if self._recordings:
            self.cb_rec.current(0)

    def choose_savedir(self):
        d = filedialog.askdirectory(title="録音の保存先を選んでください",
                                    initialdir=self.var_savedir.get())
        if not d:
            return
        self.var_savedir.set(str(Path(d)))
        config.save(recordings_dir=str(Path(d)))   # 次回以降もここに保存する
        self.log(f"保存先を変更しました: {d}")
        self.refresh_recordings()

    def _outdir_text(self):
        return str(self.outdir) if self.outdir else "（入力と同じ場所）"

    def choose_outdir(self):
        d = filedialog.askdirectory(
            title="文字起こしの出力先を選んでください",
            initialdir=str(self.outdir) if self.outdir else self.var_savedir.get())
        if not d:
            return
        self.outdir = Path(d)
        self.var_outdir.set(self._outdir_text())
        config.save(transcripts_dir=str(self.outdir))
        self.log(f"出力先を変更しました: {d}")

    def reset_outdir(self):
        """出力先を「入力と同じ場所」に戻す（録音フォルダ内に出す従来の挙動）."""
        self.outdir = None
        self.var_outdir.set(self._outdir_text())
        config.save(transcripts_dir="")
        self.log("出力先を入力と同じ場所に戻しました。")

    def choose_audio_file(self):
        f = filedialog.askopenfilename(title="文字起こしする音声ファイル",
                                       filetypes=AUDIO_TYPES)
        if not f:
            return
        self.picked_file = Path(f)
        self.lbl_picked.configure(text=self.picked_file.name)
        self.log(f"対象ファイル: {f}")

    def rename_recording(self):
        """選択中の録音の名前部分だけを付け替える（日時は変えない）."""
        if self._busy():
            return
        target = self._selected_recording("改名")
        if target is None:
            return
        _, current = record.split_recording_name(target.name)
        new = simpledialog.askstring(
            "録音の名前",
            f"{target.name}\n\n名前を入力してください（空にすると日時だけに戻ります）",
            initialvalue=current, parent=self)
        if new is None:
            return
        try:
            renamed = record.rename_recording(target, new)
        except OSError as exc:
            messagebox.showerror(
                "改名できません",
                f"{exc}\n\n{FILE_LOCKED_HINT}")
            return
        self.log(f"改名しました: {target.name} -> {renamed.name}")
        self.refresh_recordings()
        if renamed.name in self.cb_rec["values"]:
            self.cb_rec.current(list(self.cb_rec["values"]).index(renamed.name))

    def _selected_recording(self, action):
        """一覧で選択中の録音を返す。選べていなければ案内して None."""
        idx = self.cb_rec.current()
        if idx < 0 or not self._recordings:
            messagebox.showinfo("対象がありません",
                                f"{action}する録音を選んでください。")
            return None
        return self._recordings[idx]

    def _busy(self):
        """録音中・文字起こし中なら案内を出して True を返す."""
        if self.session is not None:
            messagebox.showwarning("録音中", "先に録音を停止してください。")
            return True
        if self.proc is not None:
            messagebox.showwarning("実行中", "文字起こしの完了を待ってください。")
            return True
        return False

    def delete_recording(self):
        """選択中の録音をごみ箱へ移す."""
        if self._busy():
            return
        target = self._selected_recording("削除")
        if target is None:
            return

        size_mb = record.recording_size(target) / (1024 * 1024)
        transcripts = sorted(p.name for p in target.glob("*transcript.txt"))
        detail = f"{target.name}\n\n  サイズ: {size_mb:,.0f} MB\n"
        if transcripts:
            detail += f"  文字起こし: {', '.join(transcripts)} も一緒に消えます\n"
        detail += "\nごみ箱に移動します（元に戻せます）。よろしいですか?"

        if not messagebox.askokcancel("録音の削除", detail, icon="warning"):
            return
        try:
            record.move_to_trash(target)
        except OSError as exc:
            messagebox.showerror(
                "削除できません",
                f"{exc}\n\n{FILE_LOCKED_HINT}")
            return
        self.log(f"ごみ箱へ移動しました: {target.name} ({size_mb:,.0f} MB)")
        self.refresh_recordings()

    def _save_auto(self):
        config.save(auto_transcribe=self.var_auto.get())

    def _clear_picked(self):
        """録音一覧を選び直したら、単体ファイル指定は解除する."""
        if self.picked_file is not None:
            self.picked_file = None
            self.lbl_picked.configure(text="")

    def _update_model_note(self):
        """未取得のモデルを選んだ時に、ダウンロードが要ることを見せる."""
        self.lbl_model.configure(text=download_models.size_note(self.cb_model.get()))

    def _confirm_model_download(self, name):
        """未取得のモデルなら、数GB落とすことを確認する。続けるなら True."""
        notice = download_models.download_notice(name)
        if not notice:
            return True
        return messagebox.askokcancel(
            "モデルのダウンロード",
            f"{name} はまだ取得していません。\n\n{notice}\n\n続けますか?")

    def _toggle_speakers(self):
        self.cb_speakers.configure(
            state="readonly" if self.var_diarize.get() else "disabled")

    def open_folder(self):
        """文字起こしの結果が出る場所を開く."""
        if self.outdir is not None:
            target = self.outdir
        elif self.picked_file is not None:
            target = self.picked_file.parent
        else:
            target = Path(self.var_savedir.get())
            idx = self.cb_rec.current()
            if 0 <= idx < len(self._recordings):
                target = self._recordings[idx]
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))

    # -------------------------------------------------------------- 録音制御
    def toggle_record(self):
        if self.session is None:
            self.start_record()
        else:
            self.stop_record()

    def start_record(self):
        if self._busy():
            return
        try:
            lb = self._loopbacks[self.cb_loopback.current()][1]
            mic = self._mics[self.cb_mic.current()][1]
        except (IndexError, AttributeError):
            messagebox.showerror("デバイス未選択", "デバイスを選択してください。")
            return

        try:
            self.session = record.RecordingSession(
                mic_index=mic, loopback_index=lb, name=self.var_recname.get())
            self.session.start()
        except (SystemExit, Exception) as exc:  # SystemExit は BaseException 直下
            self.session = None
            messagebox.showerror("録音を開始できません", str(exc))
            return

        self.btn_record.configure(text="■ 停止")
        self.lbl_state.configure(text="録音中", foreground="#c00")
        self.btn_transcribe.configure(state="disabled")
        self.log(f"録音開始: {self.session.outdir.name}")
        self.log("  両方のメーターが振れているか確認してください。")
        self._tick()

    def _tick(self):
        if self.session is None:
            return
        self.lbl_time.configure(text=common.hhmmss(self.session.elapsed))
        for r in self.session.recorders:
            pb, db = self.meters[r.label]
            decibels = record.level_db(r.level)
            pb["value"] = record.level_ratio(r.level) * 100
            db.configure(text=f"{decibels:6.1f} dB")
        pending = self.session.pending
        if pending:
            self.lbl_state.configure(
                text="録音中 (" + "/".join(pending) + " が応答待ち。マイクの許可を確認)",
                foreground="#c60")
        else:
            self.lbl_state.configure(text="録音中", foreground="#c00")
        self.after(150, self._tick)

    def stop_record(self):
        session, self.session = self.session, None
        self.btn_record.configure(text="● 録音開始", state="disabled")
        self.lbl_state.configure(text="停止しています...", foreground="gray")
        self.update_idletasks()

        def worker():
            try:
                session.stop()
                lines = session.summary_lines()
            except Exception as exc:
                lines = [f"停止時にエラー: {exc}"]
            finally:
                session.close()
            self.msg_queue.put(("record_done", (session.outdir, lines)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_record_done(self, payload):
        outdir, lines = payload
        for line in lines:
            self.log("  " + line)
        self.log(f"保存先: {outdir}")
        self.log("")
        self.btn_record.configure(state="normal")
        self.btn_transcribe.configure(state="normal")
        self.lbl_state.configure(text="待機中", foreground="gray")
        # 名前は消しておく。次の録音に前回の名前が残るほうが事故になりやすい
        self.var_recname.set("")
        for pb, db in self.meters.values():
            pb["value"] = 0
            db.configure(text="  -- dB")
        self.refresh_recordings()

        if self.var_auto.get():
            self.log("続けて文字起こしを開始します。")
            self._clear_picked()  # 単体ファイル指定が残っていても録音を優先する
            self.start_transcribe(outdir)

    # ---------------------------------------------------------- 文字起こし制御
    def start_transcribe(self, target=None):
        """文字起こしを開始する。target 省略時は画面の選択に従う.

        録音停止直後の自動実行では、一覧の選択状態に左右されず、いま録った
        ものを確実に対象にするため録音先を直接渡す。
        """
        if self._busy():
            return
        if target is None:
            if self.picked_file is not None:
                target = self.picked_file
            else:
                idx = self.cb_rec.current()
                if idx < 0 or not self._recordings:
                    messagebox.showinfo(
                        "対象がありません",
                        "先に録音するか、「音声ファイルを選ぶ...」で指定してください。")
                    return
                target = self._recordings[idx]

        if not self._confirm_model_download(self.cb_model.get()):
            return

        config.save(model=self.cb_model.get())
        # --threads と --outdir は渡さない。config の値を transcribe 側に
        # 解決させる（GUI が明示指定すると設定が常に迂回されるため）
        cmd = child_command("transcribe", target, "--model", self.cb_model.get(),
                            "--language", LANGUAGES[self.cb_lang.get()])
        if self.var_diarize.get():
            cmd.append("--diarize")
            if self.var_speakers.get() != "自動":
                cmd += ["--speakers", self.var_speakers.get()]

        self.btn_transcribe.configure(state="disabled")
        self.btn_record.configure(state="disabled")
        self.log(f"文字起こし開始: {target.name} ({self.cb_model.get()})")
        self.log("  CPU 処理のため時間がかかります。完了までお待ちください。")

        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            cmd, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=getattr(
                subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._pump_output, args=(self.proc,),
                         daemon=True).start()

    def _pump_output(self, proc):
        """子プロセスの出力を読む。単独の \\r による上書きだけ進捗ラベルへ回す.

        Windows は print() の \\n を \\r\\n にして出すので、\\r を見た時点では
        行末か上書きか決まらない。次の 1 文字まで判断を遅らせる。
        """
        buf, saw_cr = b"", False

        def emit(kind):
            nonlocal buf
            line = buf.decode("utf-8", "replace").rstrip()
            buf = b""
            if line.strip():
                self.msg_queue.put((kind, line))

        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            if ch == b"\r":
                if saw_cr:      # \r が続いた = 直前のは単独の \r だった
                    emit("progress")
                saw_cr = True
                continue
            if saw_cr:
                saw_cr = False
                emit("log" if ch == b"\n" else "progress")
                if ch == b"\n":
                    continue
            if ch == b"\n":
                emit("log")
            else:
                buf += ch
        emit("progress" if saw_cr else "log")
        proc.wait()
        self.msg_queue.put(("transcribe_done", proc.returncode))

    def _on_transcribe_done(self, code):
        self.proc = None
        self.lbl_progress.configure(text="")
        self.btn_transcribe.configure(state="normal")
        self.btn_record.configure(state="normal")
        self._update_model_note()  # 取得できていれば案内を消す
        if code == 0:
            self.log("完了しました。「出力先を開く」で結果を確認できます。")
            self.log("")
        else:
            self.log(f"エラーで終了しました (code {code})")
            messagebox.showerror("文字起こし失敗",
                                 "ログを確認してください。\n"
                                 "モデルが未取得の場合は、GUI のモデル欄で"
                                 "確認を進めるか setup.bat を実行してください。")

    # ------------------------------------------------------------------ 受信
    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "progress":
                    self.lbl_progress.configure(text=payload.strip())
                elif kind == "record_done":
                    self._on_record_done(payload)
                elif kind == "transcribe_done":
                    self._on_transcribe_done(payload)
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def on_close(self):
        if self.session is not None:
            if not messagebox.askokcancel("録音中", "録音を停止して終了しますか?"):
                return
            try:
                self.session.stop()
                self.session.close()
            except Exception:
                pass
        if self.proc is not None:
            if not messagebox.askokcancel("実行中", "文字起こしを中断して終了しますか?"):
                return
            self.proc.terminate()
        self.master.destroy()


def main():
    # pythonw.exe は例外を画面にもコンソールにも出さず黙って終了するため、
    # 落ちた理由が必ず残るようにログへ書き出す。
    import traceback

    log_path = ROOT / "gui_error.log"
    try:
        root = tk.Tk()
        root.title("会議録音・文字起こし (ローカル完結)")
        app = App(root)
        # 画面に収まる範囲で全体を出す。1366x768 のノート PC では 800 だと
        # ログ欄が画面外に出て掴めなくなるため、高さは画面から決める。
        root.update_idletasks()
        root.geometry(f"720x{min(app.winfo_reqheight() + 24, root.winfo_screenheight() - 80)}")
        # 縮めた分はログ欄が吸う（伸びる行はそこだけ）。操作部が隠れない下限。
        root.minsize(690, 640)
        root.protocol("WM_DELETE_WINDOW", app.on_close)
        root.mainloop()
        return 0
    except Exception:
        detail = traceback.format_exc()
        try:
            log_path.write_text(detail, encoding="utf-8")
        except Exception:
            pass
        try:
            messagebox.showerror(
                "起動に失敗しました",
                f"{detail.strip().splitlines()[-1]}\n\n詳細: {log_path}")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
