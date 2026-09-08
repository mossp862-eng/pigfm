"""Activity watching: log every transmission, whether or not it is P25.

The scanner in scanner.py compares every channel against one threshold, which is
what the spectrum display needs. For logging over hours that is the wrong
instrument: receiver response and local noise differ channel to channel, so a
single threshold chatters on the noisy channels and stays silent on the quiet
ones. This judges each channel against its own recent history instead.

Identity needs P25. Noticing that something transmitted nearby, when, for how
long and how strongly, does not.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

# How far above its own baseline a channel must rise to count as transmitting.
DEFAULT_MARGIN_DB = 10.0

# History used for the per-channel baseline. At about six frames a second this
# is a hundred seconds, long enough that a transmission cannot raise the floor
# it is being measured against.
BASELINE_FRAMES = 600

# The baseline is a low percentile, not a mean, so activity on the channel does
# not drag it upward.
BASELINE_PERCENTILE = 20

# Frames below the threshold before a transmission is treated as finished.
GAP_FRAMES = 3

# Shorter than this is a click, not a transmission.
MIN_DURATION_S = 0.3

# A burst must be above the threshold for at least this proportion of its span.
# Without it, a channel hovering at the threshold produces a single "burst"
# hundreds of seconds long.
MIN_ON_FRACTION = 0.5


@dataclass
class Burst:
	"""One transmission heard on one channel."""

	channel: int
	frequency: float
	started: datetime
	duration: float
	peak_db: float
	on_fraction: float

	def __str__(self) -> str:
		return (f'{self.started:%H:%M:%S}  ch {self.channel} '
				f'({self.frequency / 1e6:.4f} MHz)  {self.duration:5.1f}s  '
				f'+{self.peak_db:.1f}dB')


@dataclass
class _Open:
	start: float
	last: float
	peak: float
	on: int = 1
	span: int = 1
	started: datetime = field(default_factory = datetime.now)


class ActivityWatcher:
	"""Turns a stream of levelled frames into completed transmissions."""

	def __init__(self, n_channels: int, channel_to_freq, margin_db: float = DEFAULT_MARGIN_DB,
				 baseline_frames: int = BASELINE_FRAMES, min_duration: float = MIN_DURATION_S):
		self.n_channels = n_channels
		self.channel_to_freq = channel_to_freq
		self.margin_db = margin_db
		self.min_duration = min_duration

		self.frames = 0
		self.bursts = 0
		self.hot_frames = np.zeros(n_channels, dtype = np.int64)
		self.airtime = np.zeros(n_channels)
		self.counts = np.zeros(n_channels, dtype = np.int64)

		self._history: deque = deque(maxlen = baseline_frames)
		self._baseline: np.ndarray | None = None
		self._open: dict[int, _Open] = {}

	@property
	def baseline(self) -> np.ndarray | None:
		return self._baseline

	def update(self, frame: np.ndarray, now: float | None = None) -> list[Burst]:
		"""Feed one levelled frame. Returns any transmissions that just ended."""
		now = time.monotonic() if now is None else now

		self._history.append(np.asarray(frame, dtype = np.float32))
		self.frames += 1

		# Recomputing every frame would be wasted work; the floor moves slowly.
		if len(self._history) >= 30 and (self._baseline is None or self.frames % 30 == 0):
			self._baseline = np.percentile(np.array(self._history), BASELINE_PERCENTILE, axis = 0)

		if self._baseline is None:
			return []

		excess = np.asarray(frame) - self._baseline
		hot = excess >= self.margin_db
		self.hot_frames += hot

		for channel in np.flatnonzero(hot):
			channel = int(channel)
			entry = self._open.get(channel)

			if entry is None:
				self._open[channel] = _Open(start = now, last = now, peak = float(excess[channel]))
			else:
				entry.last = now
				entry.peak = max(entry.peak, float(excess[channel]))
				entry.on += 1
				entry.span += 1

		return self._close_finished(hot, now)

	def _close_finished(self, hot: np.ndarray, now: float) -> list[Burst]:
		finished = []

		for channel in list(self._open):
			entry = self._open[channel]

			if hot[channel]:
				continue

			entry.span += 1

			if entry.span - entry.on < GAP_FRAMES:
				continue

			duration = entry.last - entry.start
			fraction = entry.on / max(entry.span, 1)

			if duration >= self.min_duration and fraction >= MIN_ON_FRACTION:
				self.counts[channel] += 1
				self.airtime[channel] += duration
				self.bursts += 1

				finished.append(Burst(
					channel = channel,
					frequency = self.channel_to_freq(channel),
					started = entry.started,
					duration = duration,
					peak_db = entry.peak,
					on_fraction = fraction))

			del self._open[channel]

		return finished

	def summary(self, limit: int = 12) -> list[tuple[int, float, int, float, float]]:
		"""Busiest channels: (channel, MHz, bursts, airtime, duty percent)."""
		active = np.flatnonzero(self.counts)
		order = sorted(active, key = lambda c: -self.airtime[c])[: limit]

		return [(int(c), self.channel_to_freq(int(c)), int(self.counts[c]),
				 float(self.airtime[c]),
				 float(self.hot_frames[c]) / max(self.frames, 1) * 100)
				for c in order]


class BurstLog:
	"""Append transmissions to a file. A no-op when no filename is given."""

	def __init__(self, filename: str | None):
		self._file = Path(filename).open('a') if filename else None

	def write(self, burst: Burst) -> None:
		if self._file is None:
			return

		self._file.write(f'{burst.started:%Y-%m-%d} {burst}  '
						 f'on {burst.on_fraction * 100:.0f}%\n')
		self._file.flush()

	def close(self) -> None:
		if self._file is not None:
			self._file.close()
			self._file = None


def run_watch(config, source, summary_seconds: float = 60.0,
			  log_file: str | None = 'activity.log', margin_db: float = DEFAULT_MARGIN_DB) -> int:
	"""Terminal activity log: every transmission, with time, power and duration."""
	rf = config.rf

	# Deliberately not stacked on NoiseFloorLeveller. That exists to flatten the
	# band for a single display threshold, and it re-estimates periodically,
	# which steps every channel at once. The watcher tracks each channel's own
	# floor already, so it wants the raw channel powers: the tilt it would have
	# removed is constant per channel and disappears into the baseline anyway.
	watcher = ActivityWatcher(rf.n_channels, rf.channel_to_freq, margin_db = margin_db)
	log = BurstLog(log_file)

	print(f'Watching {rf.n_channels} channels around {rf.tuned_freq / 1e6:.5f} MHz '
		  f'at gain {rf.gain:.0f}')
	print(f'Each channel is judged against its own noise floor, {margin_db:.0f} dB margin.')
	print('Ctrl-C to stop.\n')

	last_summary = time.monotonic()

	try:
		while True:
			frame = source.get_frame(300)

			if frame is None:
				continue

			for burst in watcher.update(frame):
				print(f'  {burst}')
				log.write(burst)

			now = time.monotonic()

			if now - last_summary >= summary_seconds:
				last_summary = now
				rows = watcher.summary()

				print(f'\n  --- {watcher.bursts} transmissions in '
					  f'{watcher.frames} frames ---')

				for channel, frequency, count, airtime, duty in rows:
					print(f'  ch {channel:>3} {frequency / 1e6:>10.4f} MHz  {count:>4} bursts  '
						  f'{airtime:>7.1f}s  {duty:>5.1f}% of the time')

				if not rows:
					print('  nothing heard yet')

				print()

	except KeyboardInterrupt:
		print('\nstopped')

	finally:
		log.close()

	print(f'\n{watcher.bursts} transmissions logged across '
		  f'{int((watcher.counts > 0).sum())} channels')

	return 0
