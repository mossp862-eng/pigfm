# PiGFM refactor: core restructure

Branch: `refactor/core-restructure`
Plan: restructure for P25 metadata decode, no new user-facing features this phase.

## Phase 1: safety net
- [x] Golden-master reference lifted verbatim from `main` into `tests/reference.py`
- [x] Golden-master tests proving new code is byte-identical to `main`

## Phase 2: structure
- [x] `pigfm/config.py` typed config, comment-preserving save
- [x] `pigfm/dsp/channels.py` FFT frame to per-channel power
- [x] `pigfm/dsp/noise_floor.py` noise floor leveller
- [x] `pigfm/scanner.py` Event + ChannelScanner, pure logic
- [x] `pigfm/eventlog.py` event to file
- [x] `pigfm/frames.py` ZMQ consumer, FrameSource protocol, synthetic source
- [x] `pigfm/radio.py` flowgraph with FFT + IQ tee
- [x] `pigfm/ui.py` curses windows, layout, key dispatch
- [x] `pigfm/app.py` wiring, main loop, ordered shutdown
- [x] `pigfm.py` entry shim + `pigfm/__main__.py`
- [x] `pyproject.toml`, requires-python >= 3.10

## Phase 3: bugs
- [x] `except curses.error():` calls the class (main:331)
- [x] `log_file.close()` when `do_log = False` (main:202)
- [x] `keep_one_in_n` parameter ignored (rtl_fft_source:22)
- [x] bare `except: pass` in key handler (main:423)
- [x] `curses.init_color` unguarded (main:140)
- [x] title centred on `SCREEN_WIDTH` not window width (main:283)
- [x] dead `refresh_all()` (main:260)
- [x] `socket.recv()` with no timeout hangs UI if flowgraph dies (main:208)
- [x] `refresh()` per row in activity loop (main:333)
- [x] activity window leaves stale rows (main:318)
- [x] hardcoded 4096/256/128/16 and 25/26 offsets, `n_channels` not really configurable
- [x] 3.12-only nested f-string quotes (main:374), Pi OS ships 3.11
- [x] config write-back destroys comments
- [x] no SIGINT handling

## Phase 4: verification
- [x] Unit tests: config round-trip, scanner lifecycle, ignore list, threshold clamp
- [x] Headless run via synthetic source
- [x] Real hardware run against the attached RTL2838
- [x] IQ branch: 12.5 kSPS confirmed, `set_decode_channel` verified

## Phase 5: delivery
- [ ] Fork to `mossp862-eng`, add remote, push branch

## Verification results

- 22 tests passing (`python3 tests/run.py`)
- Noise floor, channel powers, spectrum rows and scanner events proven
  byte-identical to `main` across 400 frames of simulated traffic
- Headless run via `--synthetic` renders and exits 0
- Real hardware (RTL2838, R820T tuner):
  - spectrum branch 6.00 fps, matching the expected 3.2e6/4096/128
  - IQ branch 49,148 Hz against a 50,000 Hz target
  - no receiver overruns with the IQ branch either on or off
  - `set_decode_channel` verified: extracted IQ is 7.3 dB hotter on the
    channels the spectrum reports as strong than on the quiet ones

## Deliberate behaviour changes from `main`

1. Channel frequencies were half a spacing low, putting the axis one whole
   channel out and channel 0 outside the receivable band. Corrected, and pinned
   empirically in `tests/test_frequency.py`.
2. Channels that trigger within the same frame are now processed in sorted
   order rather than set-iteration order, so logs are reproducible. Only
   observable when more than 30 channels trigger in a single frame.
3. Config saving no longer rewrites the whole INI, so comments survive.

---

# Feature: temporary tuning offsets

- [x] `duplex_offset` config key in `[rf]`, defaults to 4.5MHz when absent
- [x] `RfConfig.tuned_freq`, everything downstream derives from it
- [x] `--base-station` tunes down by the duplex offset
- [x] `--tuning-offset HZ` for an arbitrary shift, mutually exclusive with the above
- [x] Activity window title shows the offset in red so the mode is unmissable
- [x] `save_mutable` refuses to write while offset, guard lives in config not the caller
- [x] 11 tests
- [x] Verified on hardware: retunes 171.29375 -> 166.79375 MHz, receives a
      populated band with a strong base signal at 168.000 MHz (-0.5 dB)
