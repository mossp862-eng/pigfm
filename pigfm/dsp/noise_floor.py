"""Noise floor estimation and removal.

The RTL-SDR's response is not flat across the band, so a fixed dB threshold would
trigger at different real signal strengths depending on where in the block a
channel sits. This tracks the floor and subtracts it, leaving a frame where the
threshold means the same thing everywhere.
"""

import numpy as np

# Bins more than this far above the running average are assumed to be signal, not
# noise, and are excluded from the estimate so a live transmission cannot pull the
# floor up around itself.
SIGNAL_REJECT_DB = 4

# Weighting of the newest bin in the running average. High, so the estimate tracks
# the shape of the band rather than smoothing it away.
SMOOTHING_ALPHA = 0.9

# Frames between recalculations. The floor moves slowly, so this is cheap.
RECAL_INTERVAL = 100


class NoiseFloorLeveller:
	"""Stateful: holds the current floor estimate between frames."""

	def __init__(self, n_channels: int, recal_interval: int = RECAL_INTERVAL):
		self.n_channels = n_channels
		self.recal_interval = recal_interval
		self.noise_floor = np.zeros(n_channels)
		self._countdown = 0

	def __call__(self, frame: np.ndarray) -> np.ndarray:
		return self.level(frame)

	def level(self, frame: np.ndarray) -> np.ndarray:
		"""Return the frame with the estimated noise floor removed."""
		if self._countdown == 0:
			self._countdown = self.recal_interval
			self._recalculate(frame)

		self._countdown -= 1

		return np.subtract(frame, self.noise_floor)

	def _recalculate(self, frame: np.ndarray) -> None:
		# Sequential by nature: each bin's decision depends on the average of the
		# bins before it, so this cannot be vectorised without changing results.
		noise_avg = frame[0]
		reference = frame[0]

		for i in range(self.n_channels):
			if (frame[i] - noise_avg) < SIGNAL_REJECT_DB:
				self.noise_floor[i] = frame[i]
				noise_avg = noise_avg * (1 - SMOOTHING_ALPHA) + frame[i] * SMOOTHING_ALPHA
			else:
				self.noise_floor[i] = noise_avg

			# Normalise to the first bin so levelling removes the band's shape
			# without shifting its absolute level.
			self.noise_floor[i] -= reference
