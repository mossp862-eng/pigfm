"""Channel activity detection.

Pure logic: no curses, no file IO, no radio. It is handed levelled frames and
reports which channels started and stopped transmitting, leaving the app to
decide what that means (alarm, log line, display).
"""

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

# How many past events are retained for the activity display.
HISTORY_LENGTH = 30


class Event:
	"""One channel becoming active, and staying active until it drops out."""

	def __init__(self, channel: int, pwr: float, now: float | None = None):
		self.channel = int(channel)
		self.pwr = pwr
		self.max_pwr = pwr
		self.start_time = time.time() if now is None else now
		self.start_datetime = datetime.now()
		self.end_time: float | None = None
		self.active = True

	@property
	def duration(self) -> float:
		end = time.time() if self.active else self.end_time
		return end - self.start_time

	def set_inactive(self, now: float | None = None) -> None:
		self.active = False
		self.end_time = time.time() if now is None else now

	def update_power(self, pwr: float) -> None:
		self.pwr = pwr
		if pwr > self.max_pwr: self.max_pwr = pwr

	def describe(self) -> str:
		"""The one-line summary used by both the activity window and the log."""
		if self.active:
			return (f'{self.start_datetime:%H:%M:%S}: Channel {self.channel} is active '
					f'at {self.pwr:.1f}dB for {self.duration:.1f}s')

		return (f'{self.start_datetime:%H:%M:%S}: Channel {self.channel} was active '
				f'at {self.max_pwr:.1f}dB for {self.duration:.1f}s')


@dataclass
class ScanResult:
	"""What changed in a single frame."""

	started: list[Event] = field(default_factory = list)
	ended: list[Event] = field(default_factory = list)


class ChannelScanner:
	def __init__(self, threshold: int, release_margin: int = 4, ignore_list: set[int] | None = None,
				 history_length: int = HISTORY_LENGTH):
		self.threshold = threshold
		self.release_margin = release_margin
		self.ignore_list = set() if ignore_list is None else ignore_list
		self.history: deque[Event] = deque(maxlen = history_length)

	@property
	def active_events(self) -> list[Event]:
		return [e for e in self.history if e.active]

	def update(self, frame: np.ndarray) -> ScanResult:
		result = ScanResult()

		# Existing events: hold until the channel falls clear of the release
		# margin, so a signal sitting on the threshold does not chatter.
		for event in self.active_events:
			pwr = frame[event.channel]

			if pwr < self.threshold - self.release_margin:
				event.set_inactive()
				result.ended.append(event)
			else:
				event.update_power(pwr)

		# New events.
		active_channels = set(np.where(frame >= self.threshold)[0])
		held = {event.channel for event in self.active_events}

		# Sorted, unlike the original, which iterated a set directly. Same events,
		# but the order of channels that trigger in the same frame is now stable
		# from run to run, which keeps logs reproducible.
		for channel in sorted(active_channels - held):
			if channel in self.ignore_list:
				continue

			event = Event(channel = channel, pwr = frame[channel])
			self.history.appendleft(event)
			result.started.append(event)

		return result

	def most_recent(self) -> Event | None:
		"""Newest event, or None if nothing has happened yet.

		The original indexed self.events[0] directly, which raised AttributeError
		on a fresh start because the list was pre-filled with None. That error was
		then swallowed by a bare except, so pressing 'a' too early did nothing and
		said nothing.
		"""
		return self.history[0] if self.history else None
