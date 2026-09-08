# Lessons

## Never use em dashes
Caught: 2026-09-07, self-observed during the PiGFM refactor session.

CLAUDE.md bans em dashes everywhere: chat, code comments, commit messages, docs.
Siq used them repeatedly in chat replies while summarising the code audit before
anyone pulled him up on it.

**Why:** em dashes read as AI-generated and Vape Slag does not want that fingerprint
on anything, including throwaway chat text.

**How to apply:** hyphen, comma, colon, or start a new sentence. Check chat replies
before sending, not just committed files. The ban is not scoped to deliverables.

## Build the golden master before touching the code
Caught: 2026-09-07, PiGFM refactor.

The refactor had to preserve DSP and rendering behaviour exactly. The reference copy
of the original functions was extracted from `git show main:pigfm.py` into
`tests/reference.py` *first*, before any restructuring began.

**Why:** once the original code is deleted or reshaped, there is nothing left to diff
against and "it looks about right" becomes the only available check. CLAUDE.md #3
requires diffing behaviour against main, which is only mechanically possible if the
old implementation is preserved somewhere runnable.

**How to apply:** on any behaviour-preserving refactor, copy the original pure logic
into a test-only reference module as step one, with a comment forbidding edits to it.

## Do not assert on values the program itself rewrites
Caught: 2026-09-07, PiGFM tuning offset work.

`test_loads_shipped_config` asserted `ignore_list == {248}` against
`config/rmr.ini`. Vape Slag ran PiGFM against real hardware, adjusted the
threshold and ignored a strong local channel, and the program saved those
changes back to that exact file, as designed. The test then failed.

**Why:** the file is both a checked-in example and live user state. Any test
asserting on the mutable half of it fails the first time someone actually uses
the program, which trains people to ignore failing tests.

**How to apply:** assert structure and immutable fields against checked-in
fixtures. Test mutable behaviour against a temporary copy. Before writing an
assertion on a file, ask whether the program under test writes to it.

## Verify the receiver before analysing the signal
Caught: 2026-09-08, PiGFM P25 decode session.

Hours went into analysing signals that turned out to be the receiver's own noise
and internal spurs, because the configured gain was 20 when the site needed 40.
At gain 20 the receiver heard FM broadcast only 30dB above the noise and found
25 strong bins; at gain 40 it was 45dB and 344 bins. Two of the "strong signals"
being analysed were 172.8000 MHz and 158.4000 MHz, which are 6x and 5.5x the
dongle's own 28.8 MHz reference crystal.

**Why:** "no signal in this band" and "the receiver is deaf" look identical from
inside the data. Nothing downstream can tell them apart, so every conclusion
drawn from the spectrum was unfounded.

**How to apply:** before analysing any RF capture, check the chain against a
known-loud reference. FM broadcast works almost anywhere. Check exact-multiple
relationships to the reference oscillator before believing a strong carrier is
real. Sanity-check the *instrument* before the measurement. This is now shipped
as `--self-test`.

## A ratio is only as good as its baseline
Caught: 2026-09-08, PiGFM P25 decode session.

The C4FM detector compared the 4800 Hz clock line against the median of the
whole 800-20000 Hz band. The signal it measured had been through a matched
filter that cuts everything above 2.9 kHz, so most of that band was empty
stopband, the median collapsed, and every channel scored 1288x to 7391x and was
declared P25. The tool was confidently wrong on all twelve channels.

**Why:** the "top line" column said 826-2638 Hz while the tool claimed a strong
4800 Hz line, which was visible in the output and contradicted the verdict.

**How to apply:** measure a peak against its own local shoulders, not a wide
median that may span a filter response. And when a tool reports a positive on
every input, treat that as a failure of the tool, not a discovery. Print the
supporting number next to the verdict so the contradiction is visible.

## Test for the modulation family you are actually looking for
Caught: 2026-09-08, PiGFM P25 hunt.

The C4FM detector only examined the instantaneous frequency. RMR is a P25
Phase II system, and a Phase 2 downlink is H-DQPSK: linearly modulated, so its
symbol clock is in the envelope and its instantaneous frequency looks like
noise. Hours were spent concluding "no P25 here" with a detector that was
structurally incapable of seeing half of P25.

**Why:** "no signal" and "signal my detector cannot see" produce identical
output. A negative result is only as strong as the detector's coverage, and the
detector's blind spots are invisible in its own results.

**How to apply:** before trusting a negative, write down what the detector
would fail to see and check that list against the thing you are looking for.
`detect.classify` now tests both families, and the test suite asserts that each
family is invisible to the other test, so the blind spot cannot come back.

## Average over a burst and you average it away
Caught: 2026-09-08, PiGFM P25 hunt.

The exhaustive channel scans integrated over whole captures including the
silence. Measured on a synthetic C4FM burst surrounded by six times its length
of quiet: **1.9x ungated, 41x gated**. A bursty traffic channel could have been
sitting in front of the scan the whole time.

**Why:** integrating longer feels like it can only help sensitivity. It does for
a continuous signal, and it actively destroys a bursty one, because the noise
you add grows while the signal does not.

**How to apply:** gate on the envelope and judge a channel on the samples where
it is transmitting. Watch the constant-envelope case: FM has a flat envelope by
definition, so a continuously keyed channel has no bursts to find and must fall
back to the whole span rather than reporting nothing.

## Every ZMQ sink you build must be drained
Caught: 2026-09-08, PiGFM P25 hunt.

A GNURadio ZMQ sink applies backpressure to the whole flowgraph while it waits
on its send timeout. `--decode-scan` built the demodulator but read only the IQ
sink, so the unread symbol and FM sinks stalled the graph, the receiver overran
continuously, and nine of twelve channels reported "no data". The same mistake
produced unexplained overruns in a diagnostic earlier the same day.

**Why:** an unused branch looks free. It is not: it throttles the branches that
are in use, and the symptom appears somewhere else entirely.

**How to apply:** build only the branches you intend to consume, and keep the
sink timeout short so an idle one costs throughput rather than wedging the
graph.

## Check the analysis grid against the signal's grid
Caught: 2026-09-08, PiGFM P25 hunt.

The capture of the lower RMR band was centred on an exact 12.5 kHz multiple,
which put the analysis channel grid exactly half a channel off the real one.
Every real channel straddled two analysis channels and was clipped by the
6.25 kHz filter. The other two captures happened to be centred on half-multiples
and were aligned correctly, which is why the error stayed hidden.

**Why:** the scan produced plausible-looking output either way. Only the printed
channel frequencies gave it away, by not landing on 12.5 kHz multiples.

**How to apply:** print the frequencies a scan is actually examining and check
them against the channel plan you are searching. Getting the right answer on two
captures out of three by luck is not the same as being right.

## A fix that removes noise can manufacture signal
Caught: 2026-09-08, PiGFM uplink bring-up.

Feeding the clock recovery noise stopped it locking onto any transmission that
began after silence, which meant the decoder could never read a channel it had
just retuned to. A power squelch fixed that outright. It also introduced a worse
bug: the squelch outputs zeros when shut, the shaping filter rings down through
them, and the symbol normaliser scaled that residue up by fifteen thousand.
The framer then built frames out of amplified nothing at a dozen a second, all
reading NAC 0x000, because an all-zero Network Identifier is itself a valid BCH
codeword and decodes with zero corrected errors.

**Why:** the normaliser's job is to scale whatever it is given to full
amplitude. Given nothing, it scales nothing to full amplitude. Nothing in its
own terms was wrong, and the failure appeared two stages downstream as
confident, plausible frames.

**How to apply:** any stage that divides by a measured level needs a floor below
which it declines to act. And when a change makes a detector produce *more*
output, check the new output is real before believing the change helped: this
one looked like progress for several minutes.

## Validity checks belong on every field the standard constrains
Caught: 2026-09-08, PiGFM uplink bring-up.

Watching a live analogue channel produced `NAC 0x882 unknown(0x6)` with eight
corrected NID errors and no TSBK passing CRC. The BCH decoder always returns its
nearest codeword, so noise correlating with the sync pattern still yields a
confident NAC and DUID.

**Why:** an error-correcting decoder never says "this is not a codeword". It
says "the nearest codeword is this one", however far away it was.

**How to apply:** check the decoded fields against what the standard permits.
Nine of the sixteen DUID values do not exist, and rejecting them costs nothing
and throws out most false frames. Report the corrected error count alongside the
result so an implausible decode is visible rather than silently trusted.
