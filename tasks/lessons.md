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
