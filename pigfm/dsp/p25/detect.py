"""Deciding what modulation, if any, a channel is carrying.

Signal strength says nothing about whether a carrier is P25, and the level
histogram cannot separate C4FM from analogue FM once the signal has been shaped
for a 12.5 kHz channel. What does separate them is the symbol clock, and where
that clock shows up depends on the modulation family:

  frequency keyed (P25 Phase 1 C4FM, DMR, NXDN)
      the clock appears in the squared instantaneous frequency

  linearly keyed (P25 Phase 2 H-DQPSK, and PSK generally)
      the instantaneous frequency shows nothing useful; the clock appears in
      the squared magnitude envelope instead

Testing only the first is how a Phase 2 downlink gets mistaken for noise, so
both are tested here.
"""

from dataclasses import dataclass

import numpy as np

from .symbols import baud_line_strength

# P25 Phase 1 C4FM, and DMR and NXDN96, all run at this rate.
FSK_SYMBOL_RATE = 4800.0

# P25 Phase 2 TDMA, both H-DQPSK downlink and H-CPM uplink.
PHASE2_SYMBOL_RATE = 6000.0

# A clock line this far above its local background is a real one. Measured:
# genuine C4FM reaches 50x and above, analogue FM about 2x, noise about 1.5x.
DETECTION_THRESHOLD = 20.0


def _line_strength(signal: np.ndarray, sample_rate: float, target: float,
				   low: float = 800.0) -> tuple[float, float]:
	"""Strength of a spectral line at `target`, against its local shoulders."""
	x = np.asarray(signal, dtype = np.float64)

	if x.size < 4096:
		return 0.0, 0.0

	x = x - x.mean()
	size = 1 << int(np.floor(np.log2(x.size)))
	spectrum = np.abs(np.fft.rfft(x[: size] * np.hanning(size)))
	frequencies = np.fft.rfftfreq(size, 1 / sample_rate)

	band = (frequencies > low) & (frequencies < min(20000.0, sample_rate / 2))

	if not band.any():
		return 0.0, 0.0

	index = int(np.argmin(np.abs(frequencies - target)))
	lo, hi = max(0, index - 4), index + 5
	span = max(8, int(1500 / (frequencies[1] - frequencies[0])))

	shoulders = np.concatenate([spectrum[max(0, lo - span): lo], spectrum[hi: hi + span]])

	if shoulders.size == 0:
		return 0.0, 0.0

	strongest = int(np.argmax(spectrum[band]))

	return (float(spectrum[lo: hi].max()) / (float(np.median(shoulders)) + 1e-20),
			float(frequencies[band][strongest]))


def instantaneous_frequency(iq: np.ndarray, sample_rate: float) -> np.ndarray:
	iq = np.asarray(iq)

	if iq.size < 2:
		return np.zeros(0)

	return np.angle(iq[1:] * np.conj(iq[:-1])) * sample_rate / (2 * np.pi)


def fsk_clock(iq: np.ndarray, sample_rate: float,
			  symbol_rate: float = FSK_SYMBOL_RATE) -> float:
	"""Clock line strength for a frequency keyed signal."""
	return baud_line_strength(
		instantaneous_frequency(iq, sample_rate), sample_rate, symbol_rate)[0]


def linear_clock(iq: np.ndarray, sample_rate: float,
				 symbol_rate: float = PHASE2_SYMBOL_RATE) -> float:
	"""Clock line strength for a linearly keyed signal.

	The timing of a PSK-family signal lives in its envelope, not its frequency.
	"""
	return _line_strength(np.abs(np.asarray(iq)) ** 2, sample_rate, symbol_rate)[0]


@dataclass
class Verdict:
	modulation: str            # 'p25-c4fm', 'p25-phase2', 'fsk', 'linear', 'none'
	confidence: float          # the winning line strength
	fsk_4800: float
	linear_6000: float
	detail: str

	@property
	def is_p25(self) -> bool:
		return self.modulation in ('p25-c4fm', 'p25-phase2')

	def __str__(self) -> str:
		return f'{self.modulation} ({self.detail})'


def classify(iq: np.ndarray, sample_rate: float,
			 threshold: float = DETECTION_THRESHOLD) -> Verdict:
	"""Say what a channel is carrying, testing both modulation families."""
	iq = np.asarray(iq)

	if iq.size < 8192:
		return Verdict('none', 0.0, 0.0, 0.0, 'too few samples')

	fsk = fsk_clock(iq, sample_rate, FSK_SYMBOL_RATE)
	linear = linear_clock(iq, sample_rate, PHASE2_SYMBOL_RATE)

	if fsk >= threshold and fsk >= linear:
		return Verdict('p25-c4fm', fsk, fsk, linear,
					   f'4800 baud clock line {fsk:.0f}x')

	if linear >= threshold:
		return Verdict('p25-phase2', linear, fsk, linear,
					   f'6000 baud envelope clock {linear:.0f}x')

	return Verdict('none', max(fsk, linear), fsk, linear,
				   f'no clock line: 4800 {fsk:.1f}x, 6000 {linear:.1f}x')
