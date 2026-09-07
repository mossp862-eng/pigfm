"""Reduce a raw FFT frame to one power value per channel."""

import numpy as np

from ..config import BINS_PER_CHANNEL

# Of the 16 bins covering each channel, only the middle eight are considered.
# The outer bins carry energy from the neighbouring channels, so ignoring them
# is what stops a strong transmitter lighting up the channels either side of it.
_KEEP_FIRST = 4
_KEEP_LAST = 12


def channel_powers(frame: np.ndarray, n_channels: int) -> np.ndarray:
	"""Fold an fft_size-long frame into n_channels peak powers.

	Raises ValueError if the frame is not the expected length, which is how a
	truncated or mis-sized ZMQ packet gets rejected.
	"""
	expected = n_channels * BINS_PER_CHANNEL

	if frame.size != expected:
		raise ValueError(f'Expected {expected} bins for {n_channels} channels, got {frame.size}')

	grid = frame.reshape(n_channels, BINS_PER_CHANNEL)

	return np.max(grid[:, _KEEP_FIRST: _KEEP_LAST], axis = 1)
