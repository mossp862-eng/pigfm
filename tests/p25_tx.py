"""P25 C4FM modulator, test only.

Generates the signal a real P25 transmitter would, so the decoder can be proven
end to end without a radio. This is the transmit side of the loopback: it is
deliberately independent of the receive code, so a shared misunderstanding
cannot make a broken decoder look correct.
"""

import numpy as np

from pigfm.dsp.p25.constants import (DIBIT_TO_SYMBOL, FRAME_SYNC_DIBIT_SEQUENCE,
									 SYMBOL_RATE, UNIT_DEVIATION_HZ)


def dibits_to_symbols(dibits) -> np.ndarray:
	return np.array([DIBIT_TO_SYMBOL[int(d)] for d in dibits], dtype = np.float32)


def modulate_c4fm(symbols, sample_rate: float, symbol_rate: int = SYMBOL_RATE,
				  shape: bool = True) -> np.ndarray:
	"""Frequency modulate symbol levels into complex baseband.

	Each symbol level is a frequency deviation of level * 600 Hz, held for one
	symbol period. The phase is the running integral of that frequency, which is
	what makes it FM rather than FSK with phase discontinuities.
	"""
	symbols = np.asarray(symbols, dtype = np.float64)
	sps = sample_rate / symbol_rate

	positions = np.arange(int(round(len(symbols) * sps)))
	indices = np.minimum((positions / sps).astype(int), len(symbols) - 1)
	deviation = symbols[indices] * UNIT_DEVIATION_HZ

	if shape:
		# Real transmitters band limit the deviation rather than stepping it, so
		# a decoder that only works on hard steps would be flattered.
		window = np.hanning(int(round(sps)))
		deviation = np.convolve(deviation, window / window.sum(), mode = 'same')

	phase = 2 * np.pi * np.cumsum(deviation) / sample_rate

	return np.exp(1j * phase).astype(np.complex64)


def add_noise(iq: np.ndarray, snr_db: float, rng = None) -> np.ndarray:
	"""Add complex AWGN at a given SNR, for sensitivity measurement."""
	rng = np.random.default_rng() if rng is None else rng

	signal_power = float(np.mean(np.abs(iq) ** 2))
	noise_power = signal_power / (10 ** (snr_db / 10))
	sigma = np.sqrt(noise_power / 2)

	noise = rng.normal(0, sigma, iq.size) + 1j * rng.normal(0, sigma, iq.size)

	return (iq + noise).astype(np.complex64)


def frame_dibits(payload_dibits) -> list[int]:
	"""Prefix a payload with the frame sync, as every data unit is."""
	return list(FRAME_SYNC_DIBIT_SEQUENCE) + [int(d) for d in payload_dibits]
