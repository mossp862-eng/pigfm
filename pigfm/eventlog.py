"""Append completed events to the log file."""

from pathlib import Path

from .scanner import Event


class EventLog:
	"""No-op when logging is disabled, so callers never branch on it.

	The original opened the file only when do_log was true but closed it
	unconditionally on quit, so exiting with logging off raised AttributeError.
	"""

	def __init__(self, filename: str | None, enabled: bool = True):
		self._file = None

		if enabled and filename:
			self._file = Path(filename).open('a')

	def write(self, event: Event) -> None:
		if self._file is None:
			return

		self._file.write(f'{event.start_datetime:%Y-%m-%d} {event.describe()}\n')
		self._file.flush()

	def close(self) -> None:
		if self._file is not None:
			self._file.close()
			self._file = None

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		self.close()
