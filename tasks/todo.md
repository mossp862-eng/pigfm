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

---

# Feature: P25 metadata decode

- [x] `dsp/p25/constants.py`, verified against TIA-102 (sync decomposition round trips)
- [x] `dsp/p25/fec.py`: CRC-16, BCH(63,16) with the generator derived from the
      field rather than hardcoded, 196-bit interleaver
- [x] `dsp/p25/trellis.py`: rate 1/2 Viterbi decoder
- [x] `dsp/p25/demod.py`: C4FM demod + symbol sync as a gr.hier_block2
- [x] `dsp/p25/symbols.py`: slicing, sync correlation, normalisation, C4FM quality
- [x] `dsp/p25/framing.py`: streaming framer, NID decode
- [x] `dsp/p25/tsbk.py`: TSBK decode, talkgroup and radio IDs
- [x] `decode.py`: channel following, diagnostic mode, C4FM scan
- [x] `--decode`, `--decode-channel`, `--decode-scan`
- [x] 25 new tests including a full RF loopback (58 total)

## Verification results

- BCH generator comes out at degree 47 by construction, corrects all 11 errors,
  degrades correctly past its limit (12 err: 1/200, 18 err: 200/200 fail)
- Trellis Viterbi: exact round trip, 289/300 recovery with 8 of 49 symbols corrupted
- 0 of 500 random blocks pass the TSBK CRC, so a live pass rate is meaningful
- **Full RF loopback**: TSBK -> C4FM -> real GNURadio demod -> exact talkgroup
  and radio IDs recovered, clean and at 20/12/8 dB SNR
- Front end holds sync to 6 dB SNR, loses it at 3 dB

## Live validation: NOT ACHIEVED

There is no receivable P25 Phase 1 signal here. Measured, not assumed:

- Swept the 10 strongest channels on the mobile uplink and the base downlink,
  and at -4.50 / -4.55 / -4.60 / -4.65 MHz duplex offsets. No C4FM anywhere.
- The strongest carrier (168.0000 MHz at -0.7 dB) was examined directly: it
  occupies exactly 12.5 kHz and deviates 1155 Hz, but its instantaneous
  frequency is a single Gaussian peak rather than four clusters at
  +/-600/+/-1800 Hz. That is analogue narrowband FM, not C4FM.

**Consequence:** the trellis constellation table and the interleaver stride are
still unproven. Round trip tests encode and decode with the same table, so they
pass either way. Only live traffic with passing CRCs confirms them. If a real
system is found and sync and NAC look stable while CRCs fail, those two
constants are the first suspects.
