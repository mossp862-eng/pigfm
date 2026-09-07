"""Curses interface: spectrum plot, activity log and control panel.

Rendering of the spectrum is byte-identical to the original. The layout numbers
that were hardcoded (136 wide, 23 tall, 5 rows per label, ticks every 20 columns)
are derived here from the channel count and amplitude range instead, so a config
with a different n_channels actually draws correctly.
"""

import curses

import numpy as np

from .config import Config
from .scanner import Event

# Block-drawing characters indexed by a 4-bit mask: two channels wide, two
# half-rows tall. Order is load-bearing, do not reorder.
CHARS = np.array(list(' ▖▌▌▗▄▙▙▐▟██▐▟██'))

# Width of the '-20dB ' amplitude gutter to the left of the plot.
GUTTER = 6

# Spectrum axis ticks, in plot columns.
TICK_START = 4
TICK_SPACING = 20

PANEL_WIDTH = 64
CONTROL_HEIGHT = 9
CONTROL_X = 8
ACTIVITY_X = 74
ACTIVITY_MIN_HEIGHT = 9

TITLE = '| PiGFM by The New Radicals |'

COLOUR_ALERT = 1
COLOUR_TRIM = 2
_PINK = 10


class Layout:
	"""Window geometry derived from the config, not hardcoded."""

	def __init__(self, config: Config):
		self.n_cols = config.rf.n_channels // 2
		self.n_rows = config.display.n_rows

		self.spec_height = self.n_rows + 3          # top border, plot, axis, labels
		self.spec_width = self.n_cols + GUTTER + 2  # gutter plus both borders
		self.screen_width = self.spec_width + 4

		self.panel_top = 1 + self.spec_height + 1
		self.min_height = self.panel_top + ACTIVITY_MIN_HEIGHT + 1

	def activity_height(self, terminal_height: int) -> int:
		return terminal_height - self.panel_top - 1


def init_colours() -> None:
	"""The original called init_color unconditionally, which raises on any
	terminal that cannot redefine colours. Fall back to magenta there."""
	curses.init_pair(COLOUR_ALERT, curses.COLOR_RED, curses.COLOR_BLACK)

	trim = curses.COLOR_MAGENTA

	if curses.can_change_color() and curses.COLORS > _PINK:
		try:
			curses.init_color(_PINK, 1000, 412, 706)   # hot pink
			trim = _PINK
		except curses.error:
			pass

	curses.init_pair(COLOUR_TRIM, trim, curses.COLOR_BLACK)


def format_spectrum(frame: np.ndarray, thresholds: np.ndarray, n_cols: int) -> list[str]:
	"""Turn per-channel powers into block-character rows, top row first.

	Each character cell covers two channels horizontally and two amplitude steps
	vertically, giving quarter-block resolution. Unchanged from the original
	beyond deriving the column count.
	"""
	pairs = frame.reshape(n_cols, 2)

	left = pairs[:, 0: 1]
	right = pairs[:, 1: 2]

	bits = (left > thresholds) * 1 | \
		   (left > thresholds + 1) * 2 | \
		   (right > thresholds) * 4 | \
		   (right > thresholds + 1) * 8

	grid = CHARS[bits].T[::-1]

	return [''.join(row) for row in grid]


class Ui:
	def __init__(self, stdscr, config: Config):
		self.stdscr = stdscr
		self.config = config
		self.layout = Layout(config)
		self.thresholds = np.arange(config.display.min_amp, config.display.max_amp, 2)

		self.main_win = None
		self.spec_win = None
		self.activity_win = None
		self.control_win = None
		self.activity_rows = 0
		self._spec_cache: list[str] = []
		self._activity_cache: list[str] = []

		curses.curs_set(0)
		curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
		init_colours()

		self.build_windows()

	# Window construction ---------------------------------------------------

	def build_windows(self) -> None:
		height, width = self.stdscr.getmaxyx()
		self.stdscr.clear()

		main_width = min(self.layout.screen_width, width)
		self.main_win = curses.newwin(height, main_width, 0, 0)
		self.main_win.keypad(True)
		self.main_win.nodelay(True)
		self.main_win.attron(curses.color_pair(COLOUR_TRIM))
		self.main_win.box()
		self.main_win.attroff(curses.color_pair(COLOUR_TRIM))

		# Centred on the window that actually exists. The original centred on the
		# nominal screen width, which threw curses.error on a narrow terminal.
		title_x = max(0, (main_width - len(TITLE)) // 2)
		self._safe_addstr(self.main_win, 0, title_x, TITLE[: max(0, main_width - title_x - 1)],
						  curses.A_BOLD | curses.color_pair(COLOUR_TRIM))

		self.spec_win = self.activity_win = self.control_win = None
		self._spec_cache = []
		self._activity_cache = []

		if height < self.layout.min_height or width < self.layout.screen_width:
			self._safe_addstr(self.main_win, 1, 1,
							  f'Resize terminal to at least {self.layout.screen_width} x {self.layout.min_height}')
			self.main_win.refresh()
			return

		self.spec_win = self.main_win.derwin(self.layout.spec_height, self.layout.spec_width, 1, 2)

		activity_height = self.layout.activity_height(height)
		self.activity_rows = activity_height - 2
		self.activity_win = self.main_win.derwin(activity_height, PANEL_WIDTH,
												 self.layout.panel_top, ACTIVITY_X)
		self.activity_win.box()
		self._centre_title(self.activity_win, self.config.display.system_name)

		self.control_win = self.main_win.derwin(CONTROL_HEIGHT, PANEL_WIDTH,
												self.layout.panel_top, CONTROL_X)
		self.control_win.box()
		self._centre_title(self.control_win, 'Control')

		self.main_win.refresh()

	def resize(self) -> None:
		curses.update_lines_cols()
		self.build_windows()

	# Rendering -------------------------------------------------------------

	def draw_spectrum(self, frame: np.ndarray) -> None:
		if self.spec_win is None:
			return

		rows = self._spectrum_lines(format_spectrum(frame, self.thresholds, self.layout.n_cols))

		# Only redraw rows that changed. Curses over a slow terminal is the
		# bottleneck here, not the numpy above it.
		for i, line in enumerate(rows):
			if i < len(self._spec_cache) and self._spec_cache[i] == line:
				continue

			self._safe_addstr(self.spec_win, i, 0, line)

		self.spec_win.refresh()
		self._spec_cache = rows

	def _spectrum_lines(self, plot_rows: list[str]) -> list[str]:
		display = self.config.display
		n_cols = self.layout.n_cols

		lines = [' ' * GUTTER + '┌' + '─' * n_cols + '┐']

		labels = range(display.max_amp, display.min_amp - display.amp_step, -display.amp_step)
		scale = {i * display.rows_per_step: f'{v:>3}dB ┤' for i, v in enumerate(labels)}

		for i, row in enumerate(plot_rows):
			lines.append(f'{scale.get(i, " " * GUTTER + "│")}{row}│')

		lines.append(f'{display.min_amp:>3}dB ' + self._axis_line(n_cols))
		lines.append(self._frequency_labels(n_cols))

		return lines

	def _axis_line(self, n_cols: int) -> str:
		body = ['─'] * n_cols

		for col in range(TICK_START, n_cols, TICK_SPACING):
			body[col] = '┬'

		return '┴' + ''.join(body) + '┘'

	def _frequency_labels(self, n_cols: int) -> str:
		line = [' '] * (self.layout.spec_width)

		for col in range(TICK_START, n_cols, TICK_SPACING):
			channel = col * 2
			text = f'{self.config.rf.channel_to_freq(channel) / 1e6:.3f}MHz'
			start = col + 2

			for j, ch in enumerate(text):
				if start + j < len(line):
					line[start + j] = ch

		return ''.join(line).rstrip()

	def draw_activity(self, events: list[Event], ignore_list: set[int]) -> None:
		if self.activity_win is None:
			return

		width = PANEL_WIDTH - 3
		lines = []

		for event in events[: self.activity_rows]:
			attr = curses.A_BOLD

			if event.active and event.channel not in ignore_list:
				attr |= curses.color_pair(COLOUR_ALERT)

			lines.append((event.describe()[: width].ljust(width), attr))

		# Blank out rows the list has shrunk past. The original left stale text
		# on screen when events aged out.
		while len(lines) < self.activity_rows:
			lines.append((' ' * width, curses.A_BOLD))

		for i, (text, attr) in enumerate(lines):
			self._safe_addstr(self.activity_win, i + 1, 2, text, attr)

		# One refresh for the whole panel, not one per row as the original did.
		self.activity_win.refresh()

	def draw_control(self, threshold: int, muted: bool, ignore_list: set[int]) -> None:
		if self.control_win is None:
			return

		bold = curses.A_BOLD
		key_x, action_x, status_x = 2, 10, 42
		win = self.control_win

		self._safe_addstr(win, 1, key_x, 'Key', bold)
		self._safe_addstr(win, 1, action_x, 'Action', bold)
		self._safe_addstr(win, 1, status_x, 'Status', bold)

		self._safe_addstr(win, 3, key_x, '⬆⬇', bold)
		self._safe_addstr(win, 3, action_x, 'Alarm threshold', bold)
		self._safe_addstr(win, 3, status_x, f'{threshold}dB  ', bold)

		self._safe_addstr(win, 4, key_x, 'm', bold)
		self._safe_addstr(win, 4, action_x, 'Alarm mute', bold)

		if muted:
			self._safe_addstr(win, 4, status_x, 'Muted    ', bold | curses.color_pair(COLOUR_ALERT))
		else:
			self._safe_addstr(win, 4, status_x, 'Not muted', bold)

		self._safe_addstr(win, 5, key_x, 'c', bold)
		self._safe_addstr(win, 5, action_x, 'Clear ignore list', bold)

		max_len = 18
		text = ', '.join(str(c) for c in sorted(ignore_list))
		text = text[: max_len] + '...' if len(text) > max_len else text
		self._safe_addstr(win, 5, status_x, f'{text:<{max_len + 3}}', bold)

		self._safe_addstr(win, 6, key_x, 'a', bold)
		self._safe_addstr(win, 6, action_x, 'Add channel to ignore list', bold)
		self._safe_addstr(win, 7, key_x, 'q', bold)
		self._safe_addstr(win, 7, action_x, 'Quit', bold)

		win.refresh()

	# Input -----------------------------------------------------------------

	def read_key(self) -> str | None:
		"""Non-blocking. Returns None when nothing is waiting."""
		try:
			return self.main_win.getkey()
		except curses.error:
			return None

	def beep(self) -> None:
		curses.beep()

	# Helpers ---------------------------------------------------------------

	def _centre_title(self, win, title: str) -> None:
		self._safe_addstr(win, 0, max(0, (PANEL_WIDTH - len(title)) // 2), title, curses.A_BOLD)

	@staticmethod
	def _safe_addstr(win, y: int, x: int, text: str, attr: int = 0) -> None:
		"""Writing the last cell of a window always raises. The original tried to
		guard this with `except curses.error()`, which calls the exception class
		and raises TypeError instead of catching anything."""
		try:
			win.addstr(y, x, text, attr)
		except curses.error:
			pass
