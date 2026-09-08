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


# How far above its own noise floor a channel has to rise to count as
# transmitting. Ten dB is comfortably clear of the noise without discarding a
# weak but real signal.
BURST_MARGIN_DB = 10.0

# Ignore anything shorter than this; it is a click, not a transmission.
MIN_BURST_SECONDS = 0.05


def burst_samples(iq: np.ndarray, sample_rate: float,
				  margin_db: float = BURST_MARGIN_DB,
				  min_seconds: float = MIN_BURST_SECONDS) -> np.ndarray:
	"""Return only the samples where the channel is actually transmitting.

	A channel that is quiet 90% of the time will be judged on its silence if you
	average across a whole dwell, which is how real traffic gets missed. This
	keeps the transmissions and throws the gaps away.
	"""
	iq = np.asarray(iq)

	if iq.size < 1024:
		return iq

	envelope = 20 * np.log10(np.abs(iq) + 1e-12)

	window = max(1, int(sample_rate * 0.003))
	smoothed = np.convolve(envelope, np.ones(window) / window, mode = 'same')

	floor = float(np.percentile(smoothed, 30))

	# Frequency modulation has a constant envelope by definition, so a channel
	# transmitting without pause looks perfectly flat. So does an empty one.
	# Neither has bursts to gate on, and the whole span is the right answer for
	# both: a control channel is on throughout, and silence classifies as
	# nothing anyway.
	if float(smoothed.max()) - floor < margin_db:
		return iq

	active = smoothed > floor + margin_db

	if active.mean() < 0.002:
		return iq[:0]

	# Drop runs too short to be a transmission.
	edges = np.diff(active.astype(np.int8))
	starts = np.flatnonzero(edges == 1)
	stops = np.flatnonzero(edges == -1)

	if active[0]:
		starts = np.r_[0, starts]
	if active[-1]:
		stops = np.r_[stops, active.size - 1]

	minimum = int(min_seconds * sample_rate)
	keep = [iq[a: b] for a, b in zip(starts, stops) if b - a >= minimum]

	return np.concatenate(keep) if keep else iq[:0]


def classify_bursts(iq: np.ndarray, sample_rate: float,
					threshold: float = DETECTION_THRESHOLD) -> tuple[Verdict, float]:
	"""Classify a channel on its transmissions alone.

	Returns the verdict and the fraction of the dwell that was active. Falls
	back to the whole dwell for a channel that is on continuously, which is what
	a control channel looks like.
	"""
	iq = np.asarray(iq)
	bursts = burst_samples(iq, sample_rate)
	duty = bursts.size / max(iq.size, 1)

	if bursts.size < 8192:
		return classify(iq, sample_rate, threshold), duty

	return classify(bursts, sample_rate, threshold), duty
