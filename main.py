"""PolyCaption — real-time multi-language conversation transcriber.

Captures system audio (via VB-Cable "CABLE Output") and, optionally, the
microphone, transcribes each phrase with faster-whisper, translates it locally
with argos-translate, and shows every configured language as an
invisible-to-screen-capture overlay. The set of languages is driven entirely by
the `languages` config key, so any argos-supported pair works. No Claude /
network calls — everything runs offline.
"""
import os
import sys
import json
import time
import signal
import audioop
import threading

import numpy as np
import win32gui
import speech_recognition as sr
from faster_whisper import WhisperModel

import translate
from overlay import Overlay
from audio import init_audio, find_mic_device, make_recognizer
from hotkeys import (
    HotkeyManager, MOD_CONTROL, MOD_SHIFT,
    VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_OEM_PLUS, VK_OEM_MINUS,
)


DEFAULT_CONFIG = {
    "languages": ["en", "es"],         # argos/whisper codes; order = display order
    "whisper_model": "small",          # small is fast and handles most languages well
    "capture_mic": True,               # transcribe both sides of the conversation
    "speaker_max_seconds": 180,
    "speaker_pause_threshold": 1.0,    # snappy commits for a live transcriber
    "mic_pause_threshold": 0.6,
    "mic_max_seconds": 60,
    "live_transcription": True,
    "partial_interval": 1.2,
    "commit_after_seconds": 18,
    "beam_size": 1,                    # greedy: lowest latency
    "audio_device_index": None,
}

# More than this many simultaneous languages hurts latency (every phrase is translated
# N-1 ways) and overflows the overlay, so the configured list is truncated to this.
MAX_LANGUAGES = 3

# Display labels for the overlay; any code not listed falls back to its uppercase form.
LANG_LABELS = {
    "en": "EN", "es": "ES", "de": "DE", "ja": "JA", "fr": "FR", "it": "IT",
    "pt": "PT", "ru": "RU", "zh": "ZH", "ko": "KO", "nl": "NL", "pl": "PL",
    "tr": "TR", "ar": "AR", "hi": "HI", "uk": "UK", "sv": "SV", "cs": "CS",
}


def _lang_label(code):
    return LANG_LABELS.get(code, code.upper())


def _force_utf8_io():
    """Windows consoles default to cp1252, which raises UnicodeEncodeError the moment
    we print a transcript containing CJK / non-Latin text — and that exception would be
    swallowed by a capture loop, silently dropping the phrase before it reaches the
    overlay. Make stdout/stderr tolerant so a print can never kill a capture thread."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def get_base_dir():
    """Directory where the exe (or script) lives."""
    if 'NUITKA_ONEFILE_BINARY' in os.environ:
        return os.path.dirname(os.environ['NUITKA_ONEFILE_BINARY'])
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _config_path():
    return os.path.join(get_base_dir(), 'config.json')


def _log(msg):
    """Append a line to transcript.log next to the exe. Never raises."""
    try:
        with open(os.path.join(get_base_dir(), 'transcript.log'), 'a', encoding='utf-8') as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _load_config():
    path = _config_path()
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg.update(json.load(f))
        except Exception as e:
            _log(f"config load failed ({e}); using defaults")
    else:
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass
    return cfg


def _hold_callback(tap_fn, hold_fn, threshold=0.15):
    """Call tap_fn on a fresh press and hold_fn on key-repeat (held key)."""
    last = [0.0]
    def cb():
        now = time.monotonic()
        (hold_fn if now - last[0] < threshold else tap_fn)()
        last[0] = now
    return cb


class TranscriptTool:
    def __init__(self, config_path=None):
        self.config = _load_config()
        langs = [c.lower() for c in (self.config.get('languages') or ['en', 'es'])]
        if len(langs) > MAX_LANGUAGES:
            dropped = langs[MAX_LANGUAGES:]
            langs = langs[:MAX_LANGUAGES]
            _log(f"languages capped at {MAX_LANGUAGES}; dropped {dropped}")
            print(f"Note: 'languages' is capped at {MAX_LANGUAGES} — using {langs}, dropped {dropped}")
        self._langs = langs
        self._whisper_language = None  # auto-detect, then snapped to a configured language
        # Validate against the argos index on a background thread — it hits the network
        # (update_package_index), so doing it inline would add seconds to startup.
        threading.Thread(target=self._validate_languages, daemon=True).start()

        self.device_index, self.recognizer = init_audio(
            fallback=self.config.get('audio_device_index'))
        self.recognizer.pause_threshold = float(self.config.get('speaker_pause_threshold', 1.0))

        whisper_model = self.config.get('whisper_model', 'small')
        _log(f"loading whisper model '{whisper_model}' on cuda...")
        try:
            self._whisper = WhisperModel(whisper_model, device="cuda", compute_type="float16")
            dummy = np.zeros(16000, dtype=np.float32)
            list(self._whisper.transcribe(dummy)[0])
            _log("whisper: cuda ok")
        except Exception as e:
            _log(f"whisper: cuda failed ({e}), falling back to cpu")
            print(f"GPU unavailable ({e.__class__.__name__}), using CPU for whisper")
            self._whisper = WhisperModel(whisper_model, device="cpu", compute_type="int8")
        self._whisper_lock = threading.Lock()  # one WhisperModel, serialize across threads

        translate.warmup(self._langs)  # install packages for the configured languages

        self.overlay = Overlay([_lang_label(c) for c in self._langs])

        self.running = False
        self._recording = False
        self._speaker_heard_at = 0.0
        self._speaker_hold_until = 0.0
        self._flush = threading.Event()        # force the active phrase to finalize now
        self._flush_done = threading.Event()

    def _validate_languages(self):
        """Check configured languages against the live argos index and warn about any
        that won't translate, with the list of languages that are actually available.
        Degrades to a no-op when argos is offline/unavailable so startup never blocks
        on it. Misconfigured languages still run (untranslated), so this only warns."""
        targets, sources = translate.language_availability()
        if targets is None:
            _log("language validation skipped (argos index unavailable)")
            return
        missing = [c for c in self._langs if c not in targets]       # no en->c: can't display
        no_speech = [c for c in self._langs                          # has en->c but no c->en
                     if c in targets and c not in sources]
        if no_speech:
            warn = ("Configured language(s) {} can be displayed but not transcribed from "
                    "speech (no <lang>->en package) — if someone speaks them the text won't "
                    "translate.").format(', '.join(no_speech))
            _log(warn)
            print(warn)
        if missing:
            available = ', '.join(sorted(targets))
            warn = ("Configured language(s) {} are not available in the argos index and "
                    "won't translate. Available languages: {}").format(
                        ', '.join(missing), available)
            _log(warn)
            print('\n*** ' + warn + ' ***\n')

    # ── Transcription ──

    def _transcribe(self, audio_data, quick=False, with_lang=False):
        """Transcribe sr.AudioData with faster-whisper. Returns text, or a
        (text, detected_language) tuple when with_lang=True. Serialized by a lock."""
        raw = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
        audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        beam = 1 if quick else int(self.config.get('beam_size', 1))
        vad = dict(threshold=0.35, min_silence_duration_ms=300, speech_pad_ms=500)
        with self._whisper_lock:
            try:
                segments, info = self._whisper.transcribe(
                    audio_np, language=self._whisper_language, beam_size=beam,
                    vad_filter=True, vad_parameters=vad)
            except Exception as e:
                msg = str(e).lower()
                if any(k in msg for k in ('cublas', 'cudnn', 'cuda', 'dll', 'cannot be loaded')):
                    _log(f"whisper cuda inference failed ({e}), switching to cpu")
                    print("CUDA unavailable for whisper — falling back to CPU")
                    self._whisper = WhisperModel(self.config.get('whisper_model', 'small'),
                                                 device="cpu", compute_type="int8")
                    segments, info = self._whisper.transcribe(
                        audio_np, language=self._whisper_language, beam_size=beam,
                        vad_filter=True, vad_parameters=vad)
                else:
                    raise
            text = " ".join(s.text.strip() for s in segments).strip()
            return (text, getattr(info, 'language', None)) if with_lang else text

    def _resolve_lang(self, detected):
        """Snap Whisper's detected language to a configured one. If the detection
        isn't in the configured set (a misdetect), fall back to the first configured
        language so translation still produces every line."""
        code = (detected or '').lower()
        if code in self._langs:
            return code
        return self._langs[0] if self._langs else 'en'

    def _post_translations(self, text, lang):
        """Translate the phrase into every configured language locally and show each in
        its own overlay column."""
        if not self.overlay or not text:
            return
        try:
            pairs = translate.to_languages(text, self._resolve_lang(lang), self._langs)
        except Exception as e:
            _log(f"translate ERROR: {e!r}")
            pairs = [(c, text) for c in self._langs]
        self.overlay.post([t for _, t in pairs])

    def _is_speaker_active(self):
        return time.monotonic() < self._speaker_hold_until

    def _listen_phrase(self, source, recognizer, live, label, max_seconds,
                       abort_check=None, set_speaker_hold=False):
        """Record one phrase with incremental transcription. While speech is ongoing a
        quick partial pass runs every ~partial_interval seconds and updates the overlay's
        live line (both languages); long phrases are committed in <=commit_after-second
        segments. Returns (full_phrase_text, detected_language), or (None, None)."""
        sample_width = source.SAMPLE_WIDTH
        sample_rate = source.SAMPLE_RATE
        chunk_size = source.CHUNK
        seconds_per_chunk = chunk_size / sample_rate
        pause_threshold = recognizer.pause_threshold
        energy_threshold = recognizer.energy_threshold
        partial_every = float(self.config.get('partial_interval', 1.2))
        commit_after = float(self.config.get('commit_after_seconds', 18))

        committed = []
        committed_lock = threading.Lock()
        lang_holder = [None]
        seg_frames = []
        started = False
        silent = 0.0
        phrase_total = 0.0
        seg_total = 0.0
        last_partial = 0.0
        partial_busy = [False]
        commit_busy = [False]

        def show_live(body, lang=None):
            if not (live and body):
                return
            try:
                pairs = translate.to_languages(body, self._resolve_lang(lang), self._langs)
                self.overlay.live_transcript([t for _, t in pairs])
            except Exception:
                self.overlay.live_transcript([body])

        def run_partial(seg_bytes, prefix):
            def work():
                try:
                    txt, lng = self._transcribe(sr.AudioData(seg_bytes, sample_rate, sample_width),
                                                quick=True, with_lang=True)
                    if txt:
                        show_live((prefix + ' ' + txt).strip(), lng)
                except Exception as e:
                    _log(f"partial ERROR: {e!r}")
                finally:
                    partial_busy[0] = False
            partial_busy[0] = True
            threading.Thread(target=work, daemon=True).start()

        def run_commit(seg_bytes):
            def work():
                try:
                    txt, lng = self._transcribe(sr.AudioData(seg_bytes, sample_rate, sample_width),
                                                with_lang=True)
                    if txt:
                        with committed_lock:
                            committed.append(txt)
                            if lng:
                                lang_holder[0] = lng
                        show_live(' '.join(committed), lang_holder[0])
                except Exception as e:
                    _log(f"commit ERROR: {e!r}")
                finally:
                    commit_busy[0] = False
            commit_busy[0] = True
            threading.Thread(target=work, daemon=True).start()

        while self.running:
            buf = source.stream.read(chunk_size)
            if abort_check and abort_check():
                if started:
                    break
                return None, None
            energy = audioop.rms(buf, sample_width)
            if energy > energy_threshold:
                if not started:
                    self._recording = True
                    _log(f"listen[{label.strip()}]: speech started (energy={energy})")
                if set_speaker_hold:
                    self._speaker_hold_until = time.monotonic() + 0.4
                started = True
                silent = 0.0
            elif started:
                silent += seconds_per_chunk
            seg_frames.append(buf)
            phrase_total += seconds_per_chunk
            seg_total += seconds_per_chunk

            now = time.monotonic()
            if live and started and not partial_busy[0] and (now - last_partial) >= partial_every:
                last_partial = now
                with committed_lock:
                    prefix = ' '.join(committed)
                run_partial(b''.join(seg_frames), prefix)

            if started and seg_total >= commit_after and silent >= 0.25 and not commit_busy[0]:
                run_commit(b''.join(seg_frames))
                seg_frames = []
                seg_total = 0.0

            if self._flush.is_set() and started:
                break
            if started and silent > pause_threshold:
                break
            if phrase_total > max_seconds:
                break

        if not started:
            return None, None
        while (partial_busy[0] or commit_busy[0]) and self.running:
            time.sleep(0.02)
        if seg_frames:
            tail, tlang = self._transcribe(sr.AudioData(b''.join(seg_frames), sample_rate, sample_width),
                                           with_lang=True)
            if tlang:
                lang_holder[0] = tlang
        else:
            tail = ''
        with committed_lock:
            parts = [t for t in committed if t]
        if tail:
            parts.append(tail)
        return ' '.join(parts).strip(), lang_holder[0]

    # ── Capture loops ──

    def _capture_loop(self):
        """Listen to the system audio (speaker) and post each finished phrase."""
        print("Listening for speaker...")
        live = bool(self.config.get('live_transcription', True))
        speaker_max = float(self.config.get('speaker_max_seconds', 180))
        _log(f"capture_loop start: live={live} device={self.device_index}")
        with sr.Microphone(device_index=self.device_index, sample_rate=44100) as source:
            while self.running:
                try:
                    text, lang = self._listen_phrase(source, self.recognizer, live, '[Them]: ',
                                                     speaker_max, set_speaker_hold=True)
                    self._recording = False
                    self._speaker_heard_at = time.monotonic()
                    if live:
                        self.overlay.clear_live_transcript()
                    if text:
                        print(f"Heard: {text}")
                        self._post_translations(text, lang)
                except KeyboardInterrupt:
                    self.running = False
                    break
                except Exception as e:
                    if live:
                        try:
                            self.overlay.clear_live_transcript()
                        except Exception:
                            pass
                    print(f"Listen error: {e}")
                    time.sleep(1)
        print("Stopping listener...")

    def _mic_capture_loop(self, mic_idx):
        """Capture the user's mic with the same incremental transcription."""
        recognizer = make_recognizer()
        recognizer.pause_threshold = float(self.config.get('mic_pause_threshold', 0.6))
        live = bool(self.config.get('live_transcription', True))
        mic_max = float(self.config.get('mic_max_seconds', 60))
        time.sleep(2)  # let the speaker loop open its PyAudio stream first
        print(f"Listening to microphone (device {mic_idx})...")
        with sr.Microphone(device_index=mic_idx, sample_rate=44100) as source:
            while self.running:
                try:
                    if self._is_speaker_active() or time.monotonic() - self._speaker_heard_at < 3.0:
                        time.sleep(0.2)
                        continue
                    text, lang = self._listen_phrase(source, recognizer, live, '[You]: ', mic_max,
                                                     abort_check=self._is_speaker_active)
                    self._recording = False
                    if live:
                        self.overlay.clear_live_transcript()
                    if text:
                        print(f"You: {text}")
                        self._post_translations(text, lang)
                except Exception as e:
                    if live:
                        try:
                            self.overlay.clear_live_transcript()
                        except Exception:
                            pass
                    print(f"Mic error: {e}")
                    time.sleep(1)

    # ── Lifecycle ──

    def run(self):
        self.running = True
        print("Starting PolyCaption...")
        mods = MOD_CONTROL | MOD_SHIFT
        self.hotkeys = HotkeyManager()
        self.hotkeys.add(mods, VK_UP, self.overlay.scroll_up)
        self.hotkeys.add(mods, VK_DOWN, self.overlay.scroll_down)
        self.hotkeys.add(mods, VK_LEFT,
            _hold_callback(lambda: self.overlay.move(-20), lambda: self.overlay.resize_width(-8)),
            norepeat=False)
        self.hotkeys.add(mods, VK_RIGHT,
            _hold_callback(lambda: self.overlay.move(20), lambda: self.overlay.resize_width(8)),
            norepeat=False)
        self.hotkeys.add(mods, VK_OEM_MINUS, lambda: self.overlay.adjust_opacity(-0.1))
        self.hotkeys.add(mods, VK_OEM_PLUS, lambda: self.overlay.adjust_opacity(0.1))
        self.hotkeys.add(mods, ord('Q'), self.stop)
        self.hotkeys.start()
        print("Hotkeys: Ctrl+Shift+↑↓ (scroll) | ←→ (move/resize) | -/= (opacity) | Q (quit)")

        mic_idx = find_mic_device() if self.config.get('capture_mic', True) else None
        threading.Thread(target=self._capture_loop, daemon=True).start()
        if mic_idx is not None:
            threading.Thread(target=self._mic_capture_loop, args=(mic_idx,), daemon=True).start()
            _log(f"mic_capture: started on device {mic_idx}")
        elif self.config.get('capture_mic', True):
            _log("mic_capture: no physical microphone found, skipped")

        signal.signal(signal.SIGINT, lambda *_: self.stop())
        self.overlay.run()

    def stop(self):
        self.running = False
        if getattr(self, 'hotkeys', None):
            self.hotkeys.stop()
        if self.overlay:
            self.overlay.quit()


def _show_error_popup(message):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('PolyCaption Error', message)
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    _force_utf8_io()
    _log(f"=== startup === base_dir={get_base_dir()} executable={sys.executable}")
    try:
        TranscriptTool().run()
        _log("run() returned (normal exit)")
    except Exception:
        import traceback
        tb = traceback.format_exc()
        _log("UNHANDLED EXCEPTION:\n" + tb)
        print(tb)
        _show_error_popup(tb)
