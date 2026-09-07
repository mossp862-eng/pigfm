"""Prove the refactored signal path and rendering match `main` exactly.

The reference implementation in tests/reference.py is copied verbatim from the
original single-file program. Anything that diverges from it here is either a
bug fix called out explicitly, or a regression.

Runs under pytest, or standalone via `python3 tests/run.py`.
"""

import numpy as np

from pigfm.config import load
from pigfm.dsp.channels import channel_powers
from pigfm.dsp.noise_floor import NoiseFloorLeveller
from pigfm.frames import make_frame
from pigfm.scanner import ChannelScanner
from pigfm.ui import Layout, Ui, format_spectrum

from . import reference

N_CHANNELS = 256
THRESHOLD = -13
SEED = 4242

# A plausible traffic pattern: channels keying up, overlapping and dropping,
# including one that sits in the ignore list.
TRAFFIC = [
	(10, 60, {17}),
	(30, 90, {200}),
	(120, 140, {3, 44}),
	(150, 260, {248}),
	(300, 330, {99}),
]


def traffic_schedule(n_frames = 400):
	for i in range(n_frames):
		yield {c for start, stop, chans in TRAFFIC if start <= i < stop for c in chans}


def test_noise_floor_and_channel_powers_match_main():
	rng = np.random.default_rng(SEED)
	ref = reference.Reference(n_channels = N_CHANNELS)
	leveller = NoiseFloorLeveller(N_CHANNELS)

	for active in traffic_schedule():
		raw = make_frame(active, N_CHANNELS, rng)

		expected = ref.level_noise_floor(ref.reduce_frame(raw))
		actual = leveller.level(channel_powers(raw, N_CHANNELS))

		assert np.array_equal(expected, actual), 'noise floor diverged from main'


def test_spectrum_rows_match_main():
	rng = np.random.default_rng(SEED)
	ref = reference.Reference(n_channels = N_CHANNELS)
	leveller = NoiseFloorLeveller(N_CHANNELS)

	for active in traffic_schedule(120):
		raw = make_frame(active, N_CHANNELS, rng)

		levelled = leveller.level(channel_powers(raw, N_CHANNELS))
		ref.level_noise_floor(ref.reduce_frame(raw))

		assert ref.format_fft_frame(levelled) == format_spectrum(
			levelled, ref.THRESHOLDS, N_CHANNELS // 2), 'spectrum rows diverged from main'


def test_scanner_events_match_main():
	rng = np.random.default_rng(SEED)
	ref = reference.Reference(n_channels = N_CHANNELS, active_threshold = THRESHOLD,
							  ignore_list = {248})
	leveller = NoiseFloorLeveller(N_CHANNELS)
	scanner = ChannelScanner(threshold = THRESHOLD, ignore_list = {248})

	for active in traffic_schedule():
		raw = make_frame(active, N_CHANNELS, rng)
		levelled = leveller.level(channel_powers(raw, N_CHANNELS))

		started_before, ended_before = ref.beeps, len(ref.logged)
		ref.channel_scanner(levelled)
		result = scanner.update(levelled)

		assert len(result.started) == ref.beeps - started_before
		assert len(result.ended) == len(ref.logged) - ended_before

	assert [e.channel for e in ref.events if e] == [e.channel for e in scanner.history]


def test_spectrum_chrome_matches_main():
	"""Border, amplitude gutter and axis art, from derived geometry rather than
	the original's hardcoded strings."""
	config = load('config/rmr.ini')
	layout = Layout(config)

	stub = object.__new__(Ui)
	stub.config = config
	stub.layout = layout

	lines = Ui._spectrum_lines(stub, [''] * layout.n_rows)

	assert lines[0] == '      ┌' + '─' * 128 + '┐'
	assert lines[1].startswith(' 20dB ┤')
	assert lines[6].startswith(' 10dB ┤')
	assert lines[11].startswith('  0dB ┤')
	assert lines[-2] == ('-20dB ┴────┬───────────────────┬───────────────────┬'
						 '───────────────────┬───────────────────┬───────────────────┬'
						 '───────────────────┬───┘')
	assert len(lines) == layout.spec_height == 23
	assert layout.spec_width == 136
	assert layout.screen_width == 140
