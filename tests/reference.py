"""Verbatim behavioural reference lifted from `main` (commit 355b26c).

This is the golden master. It exists only so the refactored code can be proven
byte-identical to the original on the same inputs. The curses and ZMQ coupling
of the original has been stripped, but every line of arithmetic below is copied
unchanged from `pigfm.py` on `main`. Do not "improve" anything in this file.
"""

import time
from datetime import datetime

import numpy as np


class RefEvent:
	"""Copy of `Event` from main:pigfm.py:49."""

	def __init__(self, channel, pwr):
		self.start_time = time.time()
		self.start_datetime = datetime.now()
		self.channel = channel
		self.pwr = pwr
		self.max_pwr = pwr
		self.active = True

	def set_inactive(self):
		self.active = False
		self.end_time = time.time()

	def update_power(self, pwr):
		self.pwr = pwr
		if pwr > self.max_pwr: self.max_pwr = pwr


class Reference:
	"""The original signal path and scanner, with curses/ZMQ removed."""

	def __init__(self, n_channels = 256, min_amp = -20, amp_range = 40, active_threshold = -13, ignore_list = None):
		self.n_channels = n_channels
		self.min_amp = min_amp
		self.max_amp = min_amp + amp_range
		self.active_threshold = active_threshold
		self.ignore_list = set() if ignore_list is None else set(ignore_list)

		# main:pigfm.py:127
		self.cal_cnt = 0
		self.noise_floor = np.zeros(self.n_channels)

		# main:pigfm.py:131
		self.events = [None] * 30

		# main:pigfm.py:147
		self.CHARS = np.array(list(' ▖▌▌▗▄▙▙▐▟██▐▟██'))
		self.THRESHOLDS = np.arange(self.min_amp, self.max_amp, 2)

		# Side effects the original pushed into curses / the log file.
		self.beeps = 0
		self.logged = []

	def reduce_frame(self, frame):
		"""main:pigfm.py:204 get_fft_frame, packet reshape and bin selection."""
		grid = frame.reshape(self.n_channels, 16)
		return np.max(grid[:, 4: 12], axis = 1)

	def level_noise_floor(self, fft_frame):
		"""main:pigfm.py:236, copied unchanged."""
		if self.cal_cnt == 0:
			self.cal_cnt = 100  # Cal every frames

			alpha = 0.9
			noise_avg = fft_frame[0] # Moving average, starting at the first bin level

			for i in range(self.n_channels):
				if (fft_frame[i] - noise_avg) < 4:
					self.noise_floor[i] = fft_frame[i]
					noise_avg = noise_avg * (1 - alpha) + fft_frame[i] * alpha
				else:
					self.noise_floor[i] = noise_avg

				self.noise_floor[i] -= fft_frame[0]  # Normalise to the first bin

		self.cal_cnt -= 1

		return np.subtract(fft_frame, self.noise_floor)

	def format_fft_frame(self, fft_frame):
		"""main:pigfm.py:219, copied unchanged."""
		pairs = fft_frame.reshape(128, 2) # Reshape into 128 pairs (column pairs)

		b0 = pairs[:, 0: 1] # Left bins
		b1 = pairs[:, 1: 2] # Right bins

		bits = (b0 > self.THRESHOLDS) * 1 | \
			   (b0 > self.THRESHOLDS + 1) * 2 | \
			   (b1 > self.THRESHOLDS) * 4 | \
			   (b1 > self.THRESHOLDS + 1) * 8

		grid = self.CHARS[bits].T[::-1] # Map to characters, transpose to 20x128, and flip vertically

		return [''.join(row) for row in grid]

	def channel_scanner(self, fft_frame):
		"""main:pigfm.py:161, copied unchanged. curses.beep() becomes a counter."""
		active_events = [e for e in self.events if e and e.active]

		for event in active_events:
			pwr = fft_frame[event.channel]

			if pwr < self.active_threshold - 4:
				event.set_inactive()

				if event.channel not in self.ignore_list:
					self.logged.append(event.channel)

			else:
				event.update_power(pwr)

		active_channels = set(np.where(fft_frame >= self.active_threshold)[0])
		active_events_channels = set([event.channel for event in active_events])
		newly_active_channels = active_channels - active_events_channels

		for channel in newly_active_channels:
			if channel not in self.ignore_list:
				self.events.insert(0, RefEvent(channel = channel, pwr = fft_frame[channel]))
				self.events.pop()
				self.beeps += 1

	def channel_to_freq(self, channel, centre_freq, channel_spacing):
		"""main:pigfm.py:190, copied unchanged."""
		return centre_freq + (channel - self.n_channels / 2) * channel_spacing - channel_spacing / 2
