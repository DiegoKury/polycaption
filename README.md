# PolyCaption

Real-time **multi-language conversation transcriber**. Captures audio, transcribes
each phrase with [faster-whisper](https://github.com/SYSTRAN/faster-whisper), translates
it locally with [argos-translate](https://github.com/argosopentech/argos-translate), and
shows **every language you configure** in an overlay that is **invisible to screen
capture / screen share**.

Pick any languages [argos supports](https://github.com/argosopentech/argos-translate#supported-languages)
via the `languages` config key — e.g. `["de", "ja"]` for German + Japanese, or the default
`["en", "es"]`. Whoever speaks one of those languages gets the others filled in. Translation
pivots through English when there's no direct package (so `de`↔`ja` works without a
`de`↔`ja` model).

The full list of installable language packages is the
[argospm index](https://www.argosopentech.com/argospm/index/). Almost every package is
`X ↔ English`, so the rule of thumb is simple: any language with an English package in that
index can be combined with any other.

## How it works

- **Speaker audio** is captured via VB-Cable's `CABLE Output` device (set your call/app
  output to `CABLE Input`).
- **Your microphone** is captured too (set `capture_mic: false` to disable).
- Each phrase is transcribed in the spoken language, then every other configured language
  is filled in by argos. All lines render as blue text, updating live as you speak.

## Setup

1. Install [VB-Cable](https://vb-audio.com/Cable/) and reboot.
2. `pip install -r requirements.txt`
3. Copy `config.example.json` to `config.json` and adjust it to taste (see
   [Config](#config-configjson) below). `config.json` is gitignored, so your local
   tweaks (e.g. `audio_device_index`) won't get committed. If you skip this step,
   `main.py` creates a default `config.json` for you on first run.
4. `python main.py`
   - First run will download the Whisper `small` model and the EN↔ES argos packages
     automatically, so it will take a bit longer than usual.

## Hotkeys

| Keys | Action |
|---|---|
| `Ctrl+Shift+↑/↓` | Scroll transcript history |
| `Ctrl+Shift+←/→` | Move (tap) / resize width (hold) |
| `Ctrl+Shift+ -/=` | Opacity down / up |
| `Ctrl+Shift+R` | Reset / clear all text |
| `Ctrl+Shift+Q` | Quit |

## Config (`config.json`)

There's no need to create this file by hand — `config.example.json` has every key with its
default value, and `main.py` will write a fresh `config.json` from those defaults on first
run if one doesn't exist yet. Edit the values below to taste.

| Key | Default | Notes |
|---|---|---|
| `languages` | `["en", "es"]` | ordered list of [argos codes](https://www.argosopentech.com/argospm/index/) to transcribe & display; order = display order |
| `whisper_model` | `small` | `base` faster / `medium` more accurate |
| `partial_model` | `null` | a faster model (e.g. `tiny`/`base`) for the live partial line, so it feels instant while the final text still uses `whisper_model`. `null` reuses `whisper_model` |
| `speaker_language` | `null` | pin the **speaker's** language code (e.g. `ja`) to skip detection — faster & more accurate. `null` = auto-detect |
| `mic_language` | `null` | pin **your** language code the same way |
| `whisper_device` | `null` | `cuda` / `cpu`; `null` auto-tries CUDA then falls back to CPU |
| `whisper_compute_type` | `null` | override e.g. `int8` / `float16`; `null` picks a sensible default per device |
| `capture_mic` | `true` | also transcribe your own mic |
| `energy_threshold` | `100` | RMS level above which audio counts as speech. **Raise this** (e.g. `300`+) if the mic hears your speakers and phrases never end (no pause is ever detected) |
| `speaker_max_seconds` | `30` | hard cap; a phrase with no detected pause is force-split here |
| `speaker_pause_threshold` | `0.8` | seconds of speaker silence that ends a phrase |
| `mic_pause_threshold` | `0.6` | seconds of mic silence that ends a phrase |
| `mic_max_seconds` | `25` | hard cap on a single mic phrase before it's force-split |
| `mic_resume_delay` | `0.8` | seconds to ignore the mic after the speaker stops (prevents echo on speakers). Set `0` if you use headphones |
| `live_transcription` | `true` | show an updating partial line while you're still speaking |
| `partial_interval` | `1.2` | seconds between live partial-transcript updates |
| `commit_after_seconds` | `8` | for long phrases, transcribe in chunks of this length instead of waiting for silence |
| `beam_size` | `1` | greedy = lowest latency; raise for accuracy |
| `audio_device_index` | `null` | fallback device index if `CABLE Output` isn't found |
