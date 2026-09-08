"""Symbol level processing: normalisation, slicing and frame sync correlation."""

import numpy as np

from .constants import FRAME_SYNC_DIBIT_SEQUENCE, FRAME_SYNC_SYMBOLS

# Level index from np.digitize against these edges maps to -3, -1, +1, +3.
_SLICE_EDGES = np.array([-2.0, 0.0, 2.0])

# Dibit for each of those four levels, in the same order.
_LEVEL_TO_DIBIT = np.array([0b11, 0b10, 0b00, 0b01], dtype = np.uint8)

_SYNC_PATTERN = np.array(FRAME_SYNC_SYMBOLS, dtype = np.float32)

# A perfect match scores sum(3*3) over 24 symbols.
_SYNC_PEAK = float(np.sum(_SYNC_PATTERN ** 2))

# Correlation below this is not treated as a sync. 0.7 tolerates roughly a
# fifth of the sync symbols being wrong, which is well past the point where the
# rest of the frame is recoverable anyway.
DEFAULT_SYNC_THRESHOLD = 0.7


def slice_dibits(symbols: np.ndarray) -> np.ndarray:
	"""Hard decide each symbol into its dibit."""
	return _LEVEL_TO_DIBIT[np.digitize(symbols, _SLICE_EDGES)]


def sync_correlation(symbols: np.ndarray) -> np.ndarray:
	"""Normalised sync correlation at every offset. 1.0 is a perfect match.

	Correlating the soft symbols rather than sliced dibits keeps the confidence
	information, which matters at the SNR where this has to work.
	"""
	if symbols.size < _SYNC_PATTERN.size:
		return np.empty(0, dtype = np.float32)

	return np.correlate(symbols.astype(np.float32), _SYNC_PATTERN, mode = 'valid') / _SYNC_PEAK


def find_sync_positions(symbols: np.ndarray,
						threshold: float = DEFAULT_SYNC_THRESHOLD) -> np.ndarray:
	"""Offsets where a frame sync starts.

	Only local maxima count, so one sync cannot be reported at several adjacent
	offsets when the correlation peak is broad.
	"""
	correlation = sync_correlation(symbols)

	if correlation.size == 0:
		return np.empty(0, dtype = np.int64)

	candidates = correlation >= threshold

	if not candidates.any():
		return np.empty(0, dtype = np.int64)

	padded = np.concatenate(([-np.inf], correlation, [-np.inf]))
	is_peak = (correlation >= padded[: -2]) & (correlation > padded[2:])

	return np.flatnonzero(candidates & is_peak)


class SymbolNormaliser:
	"""Removes carrier offset and scales the outer symbols onto +/-3.

	A real receiver is never tuned exactly, and the residual frequency error
	appears as a DC offset on the quadrature demodulator's output. Gain varies
	with signal strength. Both have to come out before slicing, or every symbol
	decision is biased.
	"""

	# How far a block's own estimate may differ from the running one before the
	# running one is abandoned rather than crawled towards.
	SNAP_RATIO = 2.5

	# Below this the block carries no signal and must not be scaled up.
	#
	# The demodulator maps the outer symbols to +/-3, so anything real measures
	# around two or three here; what sits far below is the shaping filter
	# ringing down after the squelch shut. Normalising that divides by almost
	# nothing: a residue measuring 2e-4 came back out at full scale, and the
	# framer built hundreds of frames a minute out of the amplified nothing,
	# all reading NAC 0x000 because an all-zero NID is a valid codeword.
	SILENCE_LEVEL = 0.25

	def __init__(self, alpha: float = 0.02, initial_scale: float = 1.0,
				 snap_ratio: float = SNAP_RATIO, silence_level: float = SILENCE_LEVEL):
		self.alpha = alpha
		self.snap_ratio = snap_ratio
		self.silence_level = silence_level
		self.offset = 0.0
		self.scale = initial_scale
		self._started = False

	def __call__(self, symbols: np.ndarray) -> np.ndarray:
		return self.normalise(symbols)

	def normalise(self, symbols: np.ndarray) -> np.ndarray:
		if symbols.size == 0:
			return symbols

		block_offset = float(np.mean(symbols))

		# The 3s sit at the extremes, so a high percentile of the magnitude is a
		# robust estimate of where they are. Robust matters: a mean or a max
		# would be dragged around by noise spikes.
		block_scale = float(np.percentile(np.abs(symbols - block_offset), 95))

		if block_scale < self.silence_level:
			return np.zeros_like(symbols)

		block_scale = max(block_scale, 1e-6)

		# Crawling at a couple of percent a block is right for tracking a signal
		# that is already there, and wrong for arriving at one. After the tap
		# retunes, the first thing it sees is noise, whose spread is nothing like
		# a signal's; adapting slowly away from that estimate left every symbol
		# scaled to near zero and the frame sync unfindable for seconds. When a
		# block disagrees with the running estimate by this much, the running
		# estimate is about something else entirely, so it is replaced.
		changed = (block_scale > self.scale * self.snap_ratio
				   or block_scale * self.snap_ratio < self.scale)

		if not self._started or changed:
			self.offset, self.scale = block_offset, block_scale
			self._started = True
		else:
			self.offset += self.alpha * (block_offset - self.offset)
			self.scale += self.alpha * (block_scale - self.scale)

		return (symbols - self.offset) * (3.0 / self.scale)


def dibits_to_bits(dibits: np.ndarray) -> np.ndarray:
	"""Expand dibits into bits, most significant first."""
	return np.column_stack(((dibits >> 1) & 1, dibits & 1)).ravel().astype(np.uint8)


def bits_to_dibits(bits: np.ndarray) -> np.ndarray:
	pairs = np.asarray(bits, dtype = np.uint8).reshape(-1, 2)

	return ((pairs[:, 0] << 1) | pairs[:, 1]).astype(np.uint8)


SYNC_DIBITS = np.array(FRAME_SYNC_DIBIT_SEQUENCE, dtype = np.uint8)


def c4fm_quality(symbols: np.ndarray) -> tuple[float, float]:
	"""Score a symbol stream for whether it is actually C4FM.

	Returns (outer level fraction, best sync correlation).

	C4FM spends half its symbols on the outer +/-3 levels, so a genuine signal
	scores near 0.5 and shows a sync correlation close to 1. Noise sits near
	zero and scores about 0.25 with correlation under 0.6, and an analogue FM
	carrier scores lower still because its instantaneous frequency clusters
	around the middle rather than on four discrete levels.

	This is the measurement that distinguishes "no signal" from "a strong
	signal that is not P25", which is otherwise an easy few hours to lose.
	"""
	if symbols.size == 0:
		return 0.0, 0.0

	normalised = SymbolNormaliser().normalise(symbols)
	outer = float(np.mean(np.abs(normalised) > 2.0))
	correlation = sync_correlation(normalised)

	return outer, float(correlation.max()) if correlation.size else 0.0


def baud_line_strength(demodulated: np.ndarray, sample_rate: float,
					   symbol_rate: float = 4800.0) -> tuple[float, float]:
	"""Look for the symbol clock in a demodulated FSK signal.

	Squaring the instantaneous frequency of any digitally keyed signal produces
	a spectral line at its symbol rate. Returns (strength at symbol_rate as a
	multiple of the median, frequency of the strongest line found).

	This is far more reliable than judging the level histogram, which is what it
	replaced: a C4FM signal shaped for a 12.5 kHz channel does not show four
	clean peaks in a histogram of every sample, so a histogram cannot tell C4FM
	from analogue FM. The clock line can.
	"""
	x = np.asarray(demodulated, dtype = np.float64)

	if x.size < 4096:
		return 0.0, 0.0

	squared = x - x.mean()
	squared = squared * squared
	squared -= squared.mean()

	size = 1 << int(np.floor(np.log2(squared.size)))
	spectrum = np.abs(np.fft.rfft(squared[: size] * np.hanning(size)))
	frequencies = np.fft.rfftfreq(size, 1 / sample_rate)

	band = (frequencies > 800) & (frequencies < min(20000, sample_rate / 2))

	if not band.any():
		return 0.0, 0.0

	target = int(np.argmin(np.abs(frequencies - symbol_rate)))
	peak_lo, peak_hi = max(0, target - 4), target + 5

	# Compare against the shoulders either side of the line rather than a median
	# across the whole band. A wide median is wrecked by any filter stopband in
	# the band, which inflates the ratio for every channel and makes noise look
	# like a signal.
	span = max(8, int(1500 / (frequencies[1] - frequencies[0])))
	local = np.concatenate([
		spectrum[max(0, peak_lo - span): peak_lo],
		spectrum[peak_hi: peak_hi + span]])

	if local.size == 0:
		return 0.0, 0.0

	baseline = float(np.median(local)) + 1e-20
	strength = float(spectrum[peak_lo: peak_hi].max()) / baseline

	strongest = int(np.argmax(spectrum[band]))

	return strength, float(frequencies[band][strongest])
