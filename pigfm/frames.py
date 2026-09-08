"""Frame sources.

Everything downstream consumes per-channel power arrays, so it does not care
whether they came off a dongle or were made up. That is what lets the UI, the
scanner and the tests run with no hardware attached.
"""

import time
from typing import Protocol

import numpy as np
import zmq

from .config import BINS_PER_CHANNEL, RfConfig
from .dsp.channels import channel_powers

SPECTRUM_ENDPOINT = 'tcp://127.0.0.1:5555'
IQ_ENDPOINT = 'tcp://127.0.0.1:5556'
SYMBOL_ENDPOINT = 'tcp://127.0.0.1:5557'
FM_ENDPOINT = 'tcp://127.0.0.1:5558'

# Poll interval when waiting on the radio. Short enough that the UI stays
# responsive to keys, long enough not to spin the CPU.
DEFAULT_TIMEOUT_MS = 100

NOISE_DB = -45.0
NOISE_SPREAD = 1.2
SIGNAL_DB = -8.0

# Frames dropped per frame published by the flowgraph. Mirrors radio.py, kept
# here so the synthetic source can match the real frame rate without importing
# GNURadio.
KEEP_ONE_IN_N = 128

# Chance per frame that a channel keys up in the synthetic source. At ~6 frames
# per second that is roughly one transmission every five seconds, which is busy
# enough to exercise the UI without being absurd.
KEY_UP_CHANCE = 0.03


def nominal_frame_interval(rf: RfConfig, keep_one_in_n: int = KEEP_ONE_IN_N) -> float:
	"""Seconds between frames the radio actually publishes.

	At 3.2 MSPS with a 4096-point FFT keeping one frame in 128, that is 6.1 per
	second. The synthetic source paces itself to match, otherwise it free-runs at
	thousands of frames per second, pins a core and fabricates a wildly
	unrealistic rate of activity.
	"""
	return (rf.fft_size * keep_one_in_n) / rf.samp_rate


class FrameSource(Protocol):
	"""Yields one array of per-channel powers per call."""

	def get_frame(self, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> np.ndarray | None:
		"""Return a frame, or None if none arrived within the timeout."""

	def close(self) -> None: ...


class ZmqFrameSource:
	"""Pulls FFT frames published by the GNURadio flowgraph."""

	def __init__(self, n_channels: int, endpoint: str = SPECTRUM_ENDPOINT):
		self.n_channels = n_channels
		self.expected_bins = n_channels * BINS_PER_CHANNEL

		self._context = zmq.Context.instance()
		self._socket = self._context.socket(zmq.PULL)
		self._socket.connect(endpoint)

		self._poller = zmq.Poller()
		self._poller.register(self._socket, zmq.POLLIN)

	def get_frame(self, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> np.ndarray | None:
		"""Return the next well-formed frame, or None on timeout.

		The original called recv() with no timeout inside a while-True that
		swallowed malformed packets. If the flowgraph died the UI hung with no
		way to quit. Returning None lets the caller keep servicing the keyboard.
		"""
		if not self._poller.poll(timeout_ms):
			return None

		try:
			raw = self._socket.recv(zmq.NOBLOCK)
		except zmq.ZMQError:
			return None

		frame = np.frombuffer(raw, dtype = np.float32)

		if frame.size != self.expected_bins:
			return None

		return channel_powers(frame, self.n_channels)

	def close(self) -> None:
		self._poller.unregister(self._socket)
		self._socket.close(linger = 0)


class SyntheticFrameSource:
	"""Fabricates plausible traffic so PiGFM runs with no dongle attached.

	Used by the test suite and by `--synthetic` for working on the UI.
	"""

	def __init__(self, n_channels: int = 256, seed: int | None = None,
				 noise_db: float = NOISE_DB, signal_db: float = SIGNAL_DB,
				 tilt_db: float = 2.0, key_up_chance: float = KEY_UP_CHANCE,
				 frame_interval: float = 0.0):
		self.n_channels = n_channels
		self.frame_interval = frame_interval
		self._next_frame_at = time.monotonic()
		self.noise_db = noise_db
		self.signal_db = signal_db
		self.tilt_db = tilt_db
		self.key_up_chance = key_up_chance
		self.rng = np.random.default_rng(seed)
		self._active: dict[int, int] = {}   # channel -> frames remaining

	def get_frame(self, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> np.ndarray | None:
		if self.frame_interval:
			wait = self._next_frame_at - time.monotonic()

			# Behave like the ZMQ source: report nothing rather than block past
			# the caller's timeout, so the UI keeps servicing the keyboard.
			if wait > timeout_ms / 1000:
				time.sleep(timeout_ms / 1000)
				return None

			if wait > 0:
				time.sleep(wait)

			self._next_frame_at = time.monotonic() + self.frame_interval

		self._advance_traffic()

		return channel_powers(
			make_frame(self._active, self.n_channels, self.rng,
					   self.noise_db, self.signal_db, self.tilt_db),
			self.n_channels)

	def _advance_traffic(self) -> None:
		for channel in list(self._active):
			self._active[channel] -= 1

			if self._active[channel] <= 0:
				del self._active[channel]

		if self.rng.random() < self.key_up_chance:
			channel = int(self.rng.integers(0, self.n_channels))
			self._active[channel] = int(self.rng.integers(15, 120))

	def close(self) -> None:
		pass


def make_frame(active_channels = (), n_channels: int = 256, rng = None,
			   noise_db: float = NOISE_DB, signal_db: float = SIGNAL_DB,
			   tilt_db: float = 2.0) -> np.ndarray:
	"""One raw FFT frame: noise floor with a gentle frontend tilt, plus signals
	filling the middle bins of the given channels."""
	rng = np.random.default_rng() if rng is None else rng
	size = n_channels * BINS_PER_CHANNEL

	frame = rng.normal(noise_db, NOISE_SPREAD, size)
	frame += np.linspace(0.0, tilt_db, size)

	for channel in active_channels:
		lo = channel * BINS_PER_CHANNEL + 4
		hi = channel * BINS_PER_CHANNEL + 12
		frame[lo: hi] = signal_db + rng.normal(0.0, 0.3, hi - lo)

	return frame.astype(np.float32)


class SymbolSource:
	"""Pulls recovered C4FM symbols published by the flowgraph.

	Unlike the spectrum source this returns whatever has arrived rather than
	fixed size frames, because the framer downstream is indifferent to block
	boundaries.
	"""

	def __init__(self, endpoint: str = SYMBOL_ENDPOINT):
		self.endpoint = endpoint
		self._context = zmq.Context.instance()
		self._socket = self._context.socket(zmq.PULL)
		self._socket.connect(endpoint)

		self._poller = zmq.Poller()
		self._poller.register(self._socket, zmq.POLLIN)

	def get_symbols(self, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> np.ndarray:
		"""Every symbol waiting, or an empty array. Never blocks past the timeout."""
		if not self._poller.poll(timeout_ms):
			return np.empty(0, dtype = np.float32)

		blocks = []

		while True:
			try:
				blocks.append(np.frombuffer(self._socket.recv(zmq.NOBLOCK), dtype = np.float32))
			except zmq.ZMQError:
				break

		return np.concatenate(blocks) if blocks else np.empty(0, dtype = np.float32)

	def close(self) -> None:
		self._poller.unregister(self._socket)
		self._socket.close(linger = 0)


class IqSource:
	"""Pulls raw channel IQ published by the flowgraph.

	Signal classification needs complex samples: the envelope carries the timing
	of a linearly modulated signal, and the FM demodulated stream has thrown
	that away.
	"""

	def __init__(self, endpoint: str = IQ_ENDPOINT):
		self.endpoint = endpoint
		self._context = zmq.Context.instance()
		self._socket = self._context.socket(zmq.PULL)
		self._socket.connect(endpoint)

		self._poller = zmq.Poller()
		self._poller.register(self._socket, zmq.POLLIN)

	def get_iq(self, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> np.ndarray:
		if not self._poller.poll(timeout_ms):
			return np.empty(0, dtype = np.complex64)

		blocks = []

		while True:
			try:
				blocks.append(np.frombuffer(self._socket.recv(zmq.NOBLOCK), dtype = np.complex64))
			except zmq.ZMQError:
				break

		return np.concatenate(blocks) if blocks else np.empty(0, dtype = np.complex64)

	def close(self) -> None:
		self._poller.unregister(self._socket)
		self._socket.close(linger = 0)
