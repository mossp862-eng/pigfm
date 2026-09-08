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

---

# Session: P25 live bring-up (2026-09-08)

## Outcome: no P25 signal receivable at this site. Decoder unproven on air.

## What was measured
- **Receiver gain was the blocker.** Config `gain = 20` heard FM broadcast at
  30.5 dB / 25 strong bins; gain 40 heard 45.2 dB / 344 bins. Everything
  measured before this was near the noise floor.
- 172.8000 MHz and 158.4000 MHz are 6x and 5.5x the dongle's 28.8 MHz crystal:
  internal spurs, not transmissions.
- All 256 downlink channels, at gain 20 and gain 45, with a correct 12.5 kHz
  channel filter: **no 4800 baud clock line anywhere**.
- Duplex offsets tried: 4.50, 4.55, 4.60, 4.65 MHz. Nothing.
- Wideband sweep, 74-870 MHz in 58 steps at gain 45: 8 narrowband carriers,
  every one tested and none 4-level FSK at 2400, 4800 or 9600 baud.
- The strongest carriers deviate 333-527 Hz, essentially unmodulated.
- A P25 Phase 2 system still carries a Phase 1 C4FM control channel, so its
  absence rules out Phase 2 here as well.

## Shipped from this session
- [x] `--self-test`: FM broadcast reference check and gain recommendation
- [x] `baud_line_strength`: measures the symbol clock line. C4FM ~50x+,
      analogue FM ~2x, noise ~1.5x. Replaced a level-histogram heuristic that
      could not tell C4FM from analogue FM.
- [x] FM tap in `C4fmDemod`, ahead of the matched filter, for quality measurement
- [x] `Sighting` / `SightingLog`: radio ID with power and time, to `sightings.log`
- [x] `--gain` override, `--sightings`
- [x] 8 new tests (66 total)

## Still unproven
The trellis table and interleaver stride, as before. No live traffic to confirm
them against.

## Next
Needs a real P25 signal. Either a better or higher antenna, a location closer to
a site, a known-correct centre frequency for the local network, or an IQ
recording of a working P25 system to replay through `tests/` fixtures.

---

# Session: unattended hunt (2026-09-08, second stint)

## Confirmed from the public register
RadioReference system 7679: RMR is **P25 Phase II**, VHF, downlink
**163.6-168.2 MHz**, System ID 164, WACN BEE00, 130+ sites, run by Telstra.
Documented control channels include 168.000 (Benalla), 166.5125 (Allambee),
166.475 (Anabranch), 167.7625 (Apsley), 165.975 (Bendigo), 166.500 (Kinglake),
166.575 (Pretty Sally).

Two things follow. The config's `--base-station` window (165.2-168.4 MHz) sits
almost exactly on the RMR downlink band, and every documented frequency lands
on our 12.5 kHz channel grid. So the tuning and channelisation are right.

## 168.0000 MHz is not the Benalla control channel
It is the strongest carrier here and it sits on a documented RMR control channel
frequency, so it was worth a close look. It is not P25:

- occupied bandwidth **2.6 kHz** at -20 dB (P25 needs ~12.5 kHz)
- present for ~5 s then drops 20 dB; a control channel is continuous
- deviation 333-527 Hz, essentially unmodulated
- x^4 line 234x, consistent with a near-pure carrier

A narrowband local transmission that happens to share the frequency.

## Searched and ruled out this stint
- All 256 downlink channels for the **control channel shape** (8-18 kHz wide
  AND >85% duty): 0 matches.
- All 256 downlink channels for **linear modulation**: no 6000 Hz line in the
  squared envelope, so no P25 Phase 2 H-DQPSK either. This was a real gap in the
  detector, which until now only looked for FSK.
- 163.6-165.2 MHz, the part of the RMR band the earlier captures missed,
  captured at 164.4 MHz centre.
- Uplink monitored with per-channel adaptive baselines: **0 bursts in 331 s**,
  top channels only 1.5-2.7 dB over their own noise.

## Monitor bug found and fixed
v1 kept a burst open whenever the channel had been hot within the last 0.5 s, so
a channel hovering at the threshold produced one fake burst hundreds of seconds
long: it reported 171.8500 MHz at 76% duty when the channel was actually present
in 0.3% of frames. v2 judges each channel against its own rolling 20th
percentile and requires a burst to be hot for at least half its span.

## Long watch results (several hours, 57 supervisor cycles)

Every channel judged against its own rolling baseline. **1441 transmissions
logged.** The distribution is the useful part:

| band | bursts | what it means |
|---|---|---|
| base station downlink | 1437 | 12 channels, up to 1354 s airtime, +12 to +20 dB |
| mobile uplink | 4 | effectively nothing |

Base stations are high power on hilltops and carry a long way; portables are low
power and held in a hand. Hearing 1437 base transmissions and 4 mobile ones is
the signature of a receiver a long way from any actual activity. That matters
directly for the goal of noticing nearby radios: there are none to notice here.

Every busy channel was demodulated **during its bursts**, not averaged across
the silence: 5 to 17 second transmissions, deviation 1631-2434 Hz, no symbol
clock in either family. Burst lengths that long are voice, not signalling.

## Second detector bug, found and fixed
`--decode-scan` built the demodulator but only drained the IQ sink. A ZMQ sink
backpressures the whole flowgraph while it waits on its timeout, so the unread
symbol and FM sinks made the receiver overrun continuously and nine of twelve
channels reported "no data". Fixed by building only the branch being read, and
by cutting the sink timeout from 100 ms to 20 ms so an idle branch costs
throughput instead of wedging the graph.

## An analysis error worth recording
The lower RMR band capture was centred on an exact 12.5 kHz multiple, which put
the analysis channel grid exactly half a channel off the real one. Every real
channel straddled two analysis channels and was clipped by the 6.25 kHz filter.
The downlink and uplink captures happened to be centred on half-multiples and
were correctly aligned; only this one was wrong. Rescanned with the grid fixed.

## Burst-gated exhaustive scans (the last hole closed)

The earlier exhaustive scans averaged across whole captures, which buries a
bursty channel. Both bands were rescanned with per-channel burst gating:

| band | channels | C4FM max | Phase2 max | classified P25 |
|---|---|---|---|---|
| downlink 165.2-168.4 | 256 | 3.20x | 3.59x | 0 |
| uplink 169.7-172.9 | 256 | 3.10x | 3.63x | 0 |

Detection needs 20x; real C4FM scores 50x and above. The only uplink channel
showing any activity at all is 172.8000 MHz, which is the dongle's own crystal
spur.

Separately, the six busiest downlink channels were burst-classified over a full
55 s window: 5-17 second transmissions, deviation 1631-2434 Hz, clock lines
1.4-2.0x on both families. Burst lengths that long are voice.

## Conclusion

There is no receivable P25 signal at this location, of either phase, on any
channel of the RMR band or anywhere else swept between 74 and 870 MHz. The band
is busy, the receiver works, the tuning and channel grid are right, and every
transmission in it is analogue.

The uplink carries essentially nothing at all: 4 bursts against 1437 on the
downlink. Since the goal is noticing radios *near the receiver*, and nearby
radios would have to transmit on the uplink, that is the finding that matters
most. Nothing is transmitting nearby.

## Final pass: full-length burst-gated scan

The 30 s gated scan risked missing bursts that fell later in the capture, so the
downlink was rescanned across the full 60 s:

- C4FM column: median 1.81x, **max 3.31x**
- Phase2 column: median 1.86x, **max 3.47x**
- channels classified as P25: **0**

That is the last hole closed. Every channel of both bands has now been examined
with long integration and with burst gating, in both modulation families.

## Shipped this stint
- `detect.py`: classify() tests both P25 families; classify_bursts() judges a
  channel on its transmissions
- `watch.py`: `--watch`, activity logging against per-channel adaptive
  baselines, verified live (7 transmissions in 100 s on the RMR downlink)
- `--decode-scan` rewritten to classify from raw IQ, 8 s dwell, reports the
  active fraction and both clock lines
- Flowgraph: sink timeout 100 ms -> 20 ms, FM tap opt-in, scan builds only the
  branch it reads
- 84 tests (was 66)
