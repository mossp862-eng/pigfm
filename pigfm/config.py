"""Typed configuration for PiGFM.

One object per INI section, plus the geometry that the rest of the program derives
from it. Saving writes back only the keys the UI can change, editing the INI in
place so user comments and ordering survive.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

# Each channel occupies this many FFT bins. The scanner keeps the middle eight of
# them (see dsp.channels), which is what rejects adjacent-channel splatter.
BINS_PER_CHANNEL = 16

# The spectrum display packs two channels into one character cell, and each cell
# row spans this many dB.
DB_PER_ROW = 2


@dataclass
class RfConfig:
	centre_freq: float
	channel_spacing: float
	gain: float
	iir_alpha: float
	n_channels: int

	@property
	def samp_rate(self) -> float:
		"""Span the whole channel block exactly once.

		The original hardcoded 3.2 MSPS. That was not arbitrary: 256 channels at
		12.5 kHz spacing is 3.2 MHz, so the rate was always a function of the
		channel plan. Deriving it is what makes n_channels genuinely configurable.
		"""
		return self.n_channels * self.channel_spacing

	@property
	def fft_size(self) -> int:
		"""Likewise 4096 was always n_channels * BINS_PER_CHANNEL."""
		return self.n_channels * BINS_PER_CHANNEL

	def channel_to_freq(self, channel: float) -> float:
		"""Centre frequency of a channel index.

		The original subtracted half a channel here. That was off by one whole
		channel: a tone placed at the old channel_to_freq(N) lands in channel
		N-1, and channel 0 came out at -1,606,250 Hz, outside the +/-1.6 MHz
		Nyquist band the dongle can even see. Verified empirically for all 256
		channels in tests/test_frequency.py.
		"""
		return (self.centre_freq
				+ (channel - self.n_channels / 2) * self.channel_spacing
				+ self.channel_spacing / 2)

	def channel_offset(self, channel: float) -> float:
		"""Baseband offset of a channel from the tuned centre, for the IQ tap."""
		return self.channel_to_freq(channel) - self.centre_freq


@dataclass
class DisplayConfig:
	system_name: str
	min_amp: int
	amp_range: int
	amp_step: int

	@property
	def max_amp(self) -> int:
		return self.min_amp + self.amp_range

	@property
	def n_rows(self) -> int:
		"""Character rows in the spectrum plot."""
		return self.amp_range // DB_PER_ROW

	@property
	def rows_per_step(self) -> int:
		"""Rows between amplitude scale labels. Was hardcoded as 5."""
		return self.amp_step // DB_PER_ROW


@dataclass
class ScannerConfig:
	active_threshold: int

	# A channel is held active until it falls this far below the trigger level,
	# which stops an event flickering on and off at the threshold.
	release_margin: int = 4


@dataclass
class AlarmConfig:
	mute: bool
	ignore_list: set[int] = field(default_factory = set)


@dataclass
class LoggingConfig:
	do_log: bool
	filename: str


@dataclass
class Config:
	rf: RfConfig
	display: DisplayConfig
	scanner: ScannerConfig
	alarm: AlarmConfig
	logging: LoggingConfig
	path: Path

	def save_mutable(self) -> None:
		"""Persist only the settings the UI can change.

		The original rewrote the whole file through configparser on exit, which
		silently destroyed every comment and reordered the sections. Config files
		here are hand-written and shared between users, so they are edited in
		place instead.
		"""
		updates = {
			('scanner', 'active_threshold'): str(self.scanner.active_threshold),
			('alarm', 'mute'): str(self.alarm.mute),
			('alarm', 'ignore_list'): ', '.join(str(c) for c in sorted(self.alarm.ignore_list)),
		}
		_update_ini_in_place(self.path, updates)


def load(path: str | Path) -> Config:
	import configparser

	path = Path(path)
	parser = configparser.ConfigParser()

	if not parser.read(path):
		raise FileNotFoundError(f'Config file not found or unreadable: {path}')

	raw_ignore = parser.get('alarm', 'ignore_list', fallback = '')

	return Config(
		rf = RfConfig(
			centre_freq = parser.getfloat('rf', 'centre_freq'),
			channel_spacing = parser.getfloat('rf', 'channel_spacing'),
			gain = parser.getfloat('rf', 'gain'),
			iir_alpha = parser.getfloat('rf', 'iir_alpha'),
			n_channels = parser.getint('rf', 'n_channels'),
		),
		display = DisplayConfig(
			system_name = parser.get('display', 'system_name'),
			min_amp = parser.getint('display', 'min_amp'),
			amp_range = parser.getint('display', 'amp_range'),
			amp_step = parser.getint('display', 'amp_step'),
		),
		scanner = ScannerConfig(
			active_threshold = parser.getint('scanner', 'active_threshold'),
		),
		alarm = AlarmConfig(
			mute = parser.getboolean('alarm', 'mute'),
			ignore_list = {int(x) for x in raw_ignore.split(',') if x.strip()},
		),
		logging = LoggingConfig(
			do_log = parser.getboolean('logging', 'do_log'),
			filename = parser.get('logging', 'filename'),
		),
		path = path,
	)


def _update_ini_in_place(path: Path, updates: dict[tuple[str, str], str]) -> None:
	"""Rewrite `key = value` lines for the given (section, key) pairs, leaving
	comments, blank lines, ordering and unrelated keys untouched."""
	lines = path.read_text().splitlines(keepends = True)
	section = None
	pending = dict(updates)

	for i, line in enumerate(lines):
		header = re.match(r'\s*\[([^\]]+)\]', line)

		if header:
			section = header.group(1).strip()
			continue

		entry = re.match(r'(\s*)([^#;=\s][^=]*?)(\s*=\s*)(.*?)(\r?\n?)$', line)

		if not entry:
			continue

		key = (section, entry.group(2).strip())

		if key in pending:
			indent, name, sep, _, eol = entry.groups()
			lines[i] = f'{indent}{name}{sep}{pending.pop(key)}{eol or chr(10)}'

	# A key that was absent from the file gets appended under its section.
	for (want_section, want_key), value in pending.items():
		lines = _append_to_section(lines, want_section, want_key, value)

	path.write_text(''.join(lines))


def _append_to_section(lines: list[str], section: str, key: str, value: str) -> list[str]:
	insert_at = None

	for i, line in enumerate(lines):
		header = re.match(r'\s*\[([^\]]+)\]', line)

		if header:
			if header.group(1).strip() == section:
				insert_at = i + 1
			elif insert_at is not None:
				break

		elif insert_at is not None and line.strip():
			insert_at = i + 1

	if insert_at is None:
		return lines + [f'\n[{section}]\n', f'{key} = {value}\n']

	return lines[: insert_at] + [f'{key} = {value}\n'] + lines[insert_at:]
