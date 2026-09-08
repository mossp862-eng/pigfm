"""Receiver self test and signal quality measurement.

These exist because a long debugging session was spent analysing what turned
out to be the receiver's own noise floor. Both the baud line detector and the
self test are the tools that would have caught it in a minute.
"""

import io
from contextlib import redirect_stdout

import numpy as np

from pigfm.config import load
from pigfm.diagnostics import HEALTHY_SNR_DB, WEAK_SNR_DB, measure, run_self_test
from pigfm.dsp.p25.symbols import baud_line_strength

from .p25_tx import dibits_to_symbols, modulate_c4fm

SAMPLE_RATE = 50_000
FULL_RATE = 3_200_000


def _instantaneous_frequency(iq: np.ndarray, rate: float) -> np.ndarray:
	return np.angle(iq[1:] * np.conj(iq[:-1])) * rate / (2 * np.pi)


def test_baud_line_finds_the_symbol_clock_in_real_c4fm():
	rng = np.random.default_rng(3)
	dibits = [int(d) for d in rng.integers(0, 4, 12000)]

	iq = modulate_c4fm(dibits_to_symbols(dibits), SAMPLE_RATE)
	strength, top = baud_line_strength(
		_instantaneous_frequency(iq, SAMPLE_RATE), SAMPLE_RATE)

	assert strength > 50, f'C4FM should show a strong clock line, got {strength:.1f}'
	assert abs(top - 4800) < 100, f'strongest line should be the symbol rate, got {top:.0f}'


def test_baud_line_rejects_noise_and_analogue_fm():
	"""The measurement that distinguishes a strong signal from a P25 one.

	A level histogram cannot do this: C4FM shaped for a 12.5 kHz channel does
	not show four clean peaks, so it looks much like analogue FM.
	"""
	rng = np.random.default_rng(3)
	size = 200_000

	noise = rng.normal(0, 1500, size)
	assert baud_line_strength(noise, SAMPLE_RATE)[0] < 10

	# Analogue FM: band limited audio as the modulating signal.
	audio = np.convolve(rng.normal(0, 1, size), np.ones(40) / 40, mode = 'same') * 3000
	assert baud_line_strength(audio, SAMPLE_RATE)[0] < 10


def test_baud_line_handles_short_input():
	assert baud_line_strength(np.zeros(10), SAMPLE_RATE) == (0.0, 0.0)


def _fake_capture(snr_db: float, tone_offset: float = 300e3):
	"""A capture function with one tone at a chosen SNR, for testing the self
	test without a radio."""
	rng = np.random.default_rng(11)

	def capture(centre: float, gain: float) -> np.ndarray:
		n = FULL_RATE // 8
		t = np.arange(n)
		amplitude = 10 ** ((snr_db + gain / 4) / 20)
		signal = amplitude * np.exp(2j * np.pi * tone_offset / FULL_RATE * t)
		noise = rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)

		return (signal + noise).astype(np.complex64)

	return capture


def test_measure_ignores_the_dc_spike():
	"""The tuner's own oscillator leaks into the centre of the passband. Counting
	it as a signal is what made an earlier version report a healthy receiver
	while hearing nothing."""
	rng = np.random.default_rng(5)
	n = FULL_RATE // 8

	dc_only = (np.full(n, 50.0) + rng.normal(0, 1, n)
			   + 1j * rng.normal(0, 1, n)).astype(np.complex64)

	result = measure(lambda c, g: dc_only, 100e6, FULL_RATE, 20)

	assert result is not None
	assert result['snr'] < 20, 'a DC spike alone must not look like a signal'


def _silent_self_test(config, capture) -> int:
	with redirect_stdout(io.StringIO()):
		return run_self_test(config, capture, gains = (20, 40))


def test_self_test_passes_on_a_healthy_receiver():
	assert _silent_self_test(load('config/rmr.ini'), _fake_capture(60)) == 0


def test_self_test_fails_when_nothing_is_connected():
	assert _silent_self_test(load('config/rmr.ini'), _fake_capture(-40)) == 1


def test_self_test_reports_the_reference_and_the_gain():
	buffer = io.StringIO()

	with redirect_stdout(buffer):
		run_self_test(load('config/rmr.ini'), _fake_capture(60), gains = (20, 40))

	output = buffer.getvalue()

	assert 'FM broadcast' in output
	assert 'gain' in output


def test_thresholds_are_ordered():
	assert WEAK_SNR_DB < HEALTHY_SNR_DB
