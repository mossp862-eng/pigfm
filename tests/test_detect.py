"""Modulation classification.

Testing only for frequency keying is how a P25 Phase 2 downlink gets written off
as noise: it is linearly modulated, so its symbol clock is in the envelope, not
in the instantaneous frequency.
"""

import numpy as np

from pigfm.dsp.p25.detect import (DETECTION_THRESHOLD, classify, fsk_clock,
								  instantaneous_frequency, linear_clock)

from .p25_tx import dibits_to_symbols, modulate_c4fm, modulate_qpsk

SAMPLE_RATE = 50_000
SEED = 3


def _c4fm(n_dibits = 12000):
	rng = np.random.default_rng(SEED)

	return modulate_c4fm(
		dibits_to_symbols([int(d) for d in rng.integers(0, 4, n_dibits)]), SAMPLE_RATE)


def _noise(n = 200_000):
	rng = np.random.default_rng(SEED)

	return (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)).astype(np.complex64)


def test_c4fm_is_recognised_as_phase_1():
	verdict = classify(_c4fm(), SAMPLE_RATE)

	assert verdict.modulation == 'p25-c4fm'
	assert verdict.is_p25
	assert verdict.fsk_4800 >= DETECTION_THRESHOLD


def test_shaped_qpsk_is_recognised_as_phase_2():
	verdict = classify(modulate_qpsk(30_000, SAMPLE_RATE,
									 rng = np.random.default_rng(SEED)), SAMPLE_RATE)

	assert verdict.modulation == 'p25-phase2'
	assert verdict.is_p25
	assert verdict.linear_6000 >= DETECTION_THRESHOLD


def test_noise_is_recognised_as_nothing():
	verdict = classify(_noise(), SAMPLE_RATE)

	assert verdict.modulation == 'none'
	assert not verdict.is_p25
	assert verdict.fsk_4800 < DETECTION_THRESHOLD
	assert verdict.linear_6000 < DETECTION_THRESHOLD


def test_each_family_is_blind_to_the_other():
	"""The reason both tests are needed rather than either one alone."""
	rng = np.random.default_rng(SEED)

	assert linear_clock(_c4fm(), SAMPLE_RATE) < DETECTION_THRESHOLD
	assert fsk_clock(modulate_qpsk(30_000, SAMPLE_RATE, rng = rng), SAMPLE_RATE) \
		< DETECTION_THRESHOLD


def test_short_input_is_not_classified():
	assert classify(np.zeros(100, dtype = np.complex64), SAMPLE_RATE).modulation == 'none'


def test_instantaneous_frequency_recovers_a_tone():
	rate = SAMPLE_RATE
	# A 1200 Hz tone should read as a constant 1200 Hz instantaneous frequency.
	t = np.arange(20000)
	tone = np.exp(2j * np.pi * 1200 / rate * t).astype(np.complex64)

	measured = instantaneous_frequency(tone, rate)

	assert abs(float(np.mean(measured)) - 1200) < 5
