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
