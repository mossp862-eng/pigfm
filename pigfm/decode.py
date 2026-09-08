"""P25 metadata decoding: channel following, statistics and the diagnostic mode.

This is the part that turns "channel 172 is busy" into "talkgroup 1234, radio
5551234". It reuses the existing scanner rather than detecting activity again.
"""

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config
from .dsp.noise_floor import NoiseFloorLeveller
from .dsp.p25.constants import DUID_NAMES, DUID_TSDU, TSBK_ENCODED_DIBITS
from .dsp.p25.framing import P25Framer
from .dsp.p25.symbols import baud_line_strength, c4fm_quality
from .dsp.p25.tsbk import decode_tsbk
from .scanner import ChannelScanner

# A real C4FM signal puts a clock line at 4800 Hz tens to hundreds of times
# above the surrounding spectrum. Measured: genuine C4FM scores about 330,
# analogue FM about 3, noise about 1.5. Twenty is far outside anything a
# non-keyed signal produces.
C4FM_BAUD_THRESHOLD = 20.0

# Symbol clock and framing take a moment to settle after the IQ tap moves, so
# symbols arriving immediately after a retune are discarded rather than fed to
# the framer as garbage.
RETUNE_SETTLE_SECONDS = 0.4

# Minimum time on a channel before following a newer event elsewhere. Without
# it a busy system makes the tap hop continuously and never decode anything.
CHANNEL_HOLD_SECONDS = 2.0


@dataclass
class ChannelStats:
	"""What has been seen on one channel. The DUID mix is what identifies a
	control channel: it is the one producing TSDUs."""

	units: int = 0
	duids: Counter = field(default_factory = Counter)
	tsbks: int = 0
	crc_pass: int = 0
	nacs: Counter = field(default_factory = Counter)

	@property
	def crc_rate(self) -> float:
		return self.crc_pass / self.tsbks if self.tsbks else 0.0

	@property
	def looks_like_control(self) -> bool:
		return self.duids.get(DUID_TSDU, 0) > 0 and self.crc_pass > 0

	def summary(self) -> str:
		mix = ', '.join(f'{DUID_NAMES.get(d, hex(d))} x{n}'
						for d, n in self.duids.most_common(4)) or 'nothing'
		nac = f'NAC 0x{self.nacs.most_common(1)[0][0]:03X}' if self.nacs else 'no NAC'

		return (f'{self.units} units [{mix}], {nac}, '
				f'TSBK {self.crc_pass}/{self.tsbks} CRC pass ({self.crc_rate * 100:.0f}%)')


@dataclass
class Sighting:
	"""A radio heard near the receiver.

	This is the point of the whole exercise: not mapping the network, but
	noticing that a particular radio is close by, how strong it was, and when.
	"""

	when: datetime
	channel: int
	frequency: float
	power_db: float
	radio_id: int
	talkgroup: int | None = None

	def __str__(self) -> str:
		group = f' talkgroup {self.talkgroup}' if self.talkgroup is not None else ''

		return (f'{self.when:%H:%M:%S}  radio {self.radio_id}{group}  '
				f'{self.power_db:+.1f}dB  ch {self.channel} '
				f'({self.frequency / 1e6:.4f} MHz)')


class SightingLog:
	"""Append sightings to a file. A no-op when no filename is given."""

	def __init__(self, filename: str | None):
		self._file = Path(filename).open('a') if filename else None

	def write(self, sighting: Sighting) -> None:
		if self._file is None:
			return

		self._file.write(f'{sighting.when:%Y-%m-%d} {sighting}\n')
		self._file.flush()

	def close(self) -> None:
		if self._file is not None:
			self._file.close()
			self._file = None


class DecodeMonitor:
	"""Follows channel activity and decodes P25 metadata from the IQ tap."""

	def __init__(self, config: Config, radio, spectrum_source, symbol_source,
				 pinned_channel: int | None = None):
		self.config = config
		self.radio = radio
		self.spectrum = spectrum_source
		self.symbols = symbol_source

		self.leveller = NoiseFloorLeveller(config.rf.n_channels)
		self.scanner = ChannelScanner(
			threshold = config.scanner.active_threshold,
			release_margin = config.scanner.release_margin,
			ignore_list = config.alarm.ignore_list)

		self.framer = P25Framer()
		self.stats: dict[int, ChannelStats] = {}
		self.sightings: list[Sighting] = []
		self.radios: Counter = Counter()

		# Most recent levelled power per channel, so a decoded identity can be
		# reported with how strong that radio was when it transmitted.
		self.power: dict[int, float] = {}

		self.pinned = pinned_channel is not None
		self.channel = pinned_channel if self.pinned else radio.decode_channel
		self._tuned_at = time.monotonic()

	# --- channel following --------------------------------------------------

	def _consider(self, channel: int) -> bool:
		"""Retune to a newly active channel, unless pinned or too soon."""
		if self.pinned or channel == self.channel:
			return False

		if time.monotonic() - self._tuned_at < CHANNEL_HOLD_SECONDS:
			return False

		self.retune(channel)

		return True

	def retune(self, channel: int) -> None:
		self.radio.set_decode_channel(channel)
		self.channel = channel
		self._tuned_at = time.monotonic()

		# The old channel's partial frame means nothing on the new one.
		self.framer = P25Framer()

	@property
	def settled(self) -> bool:
		return time.monotonic() - self._tuned_at >= RETUNE_SETTLE_SECONDS

	def channel_stats(self) -> ChannelStats:
		return self.stats.setdefault(self.channel, ChannelStats())

	# --- polling ------------------------------------------------------------

	def poll_spectrum(self, timeout_ms: int = 0) -> list:
		"""Advance the scanner. Returns events that started this frame."""
		frame = self.spectrum.get_frame(timeout_ms)

		if frame is None:
			return []

		levelled = self.leveller.level(frame)
		result = self.scanner.update(levelled)

		if self.channel is not None and 0 <= self.channel < len(levelled):
			self.power[self.channel] = float(levelled[self.channel])

		if result.started:
			# Newest event wins, as requested.
			self._consider(result.started[-1].channel)

		return result.started

	def poll_symbols(self, timeout_ms: int = 0) -> list:
		"""Decode whatever symbols have arrived. Returns (unit, tsbks) pairs."""
		symbols = self.symbols.get_symbols(timeout_ms)

		if symbols.size == 0 or not self.settled:
			return []

		stats = self.channel_stats()
		decoded = []

		for unit in self.framer.feed(symbols):
			stats.units += 1
			stats.duids[unit.duid] += 1
			stats.nacs[unit.nac] += 1

			tsbks = self._decode_tsbks(unit, stats) if unit.duid == DUID_TSDU else []

			for tsbk in tsbks:
				self._note_sighting(tsbk)

			decoded.append((unit, tsbks))

		return decoded

	def _note_sighting(self, tsbk) -> None:
		if tsbk.radio_id is None:
			return

		sighting = Sighting(
			when = datetime.now(),
			channel = self.channel,
			frequency = self.config.rf.channel_to_freq(self.channel),
			power_db = self.power.get(self.channel, float('nan')),
			radio_id = tsbk.radio_id,
			talkgroup = tsbk.talkgroup)

		self.sightings.append(sighting)
		self.radios[tsbk.radio_id] += 1

	def _decode_tsbks(self, unit, stats: ChannelStats) -> list:
		tsbks = []
		payload = unit.payload

		for start in range(0, len(payload) - TSBK_ENCODED_DIBITS + 1, TSBK_ENCODED_DIBITS):
			tsbk = decode_tsbk(payload[start: start + TSBK_ENCODED_DIBITS])

			if tsbk is None:
				break

			stats.tsbks += 1

			if tsbk.crc_ok:
				stats.crc_pass += 1
				tsbks.append(tsbk)

				if tsbk.last_block:
					break

		return tsbks


def run_diagnostic(config: Config, radio, spectrum_source, symbol_source,
				   pinned_channel: int | None = None, summary_seconds: float = 10.0,
				   sightings_file: str | None = 'sightings.log') -> int:
	"""Terminal decode mode. Prints identities as they arrive, and a periodic
	per-channel summary so the control channel is easy to spot."""
	monitor = DecodeMonitor(config, radio, spectrum_source, symbol_source, pinned_channel)
	rf = config.rf

	def describe(channel: int) -> str:
		return f'ch {channel} ({rf.channel_to_freq(channel) / 1e6:.4f} MHz)'

	print(f'Decoding {describe(monitor.channel)}'
		  + ('  [pinned]' if monitor.pinned else '  [following activity]'))
	print('Ctrl-C to stop.\n')

	last_summary = time.monotonic()
	seen_sightings = 0
	log = SightingLog(sightings_file)

	try:
		while True:
			for event in monitor.poll_spectrum():
				print(f'  {time.strftime("%H:%M:%S")}  channel {event.channel} active '
					  f'at {event.pwr:.1f}dB'
					  + ('' if monitor.pinned else f', following to {describe(monitor.channel)}'))

			for unit, tsbks in monitor.poll_symbols(timeout_ms = 50):
				stamp = time.strftime('%H:%M:%S')
				print(f'  {stamp}  {describe(monitor.channel)}  {unit}')

				for tsbk in tsbks:
					print(f'  {stamp}      -> {tsbk}')

				for sighting in monitor.sightings[seen_sightings:]:
					print(f'  * RADIO NEARBY  {sighting}')
					log.write(sighting)

				seen_sightings = len(monitor.sightings)

			now = time.monotonic()

			if now - last_summary >= summary_seconds:
				last_summary = now
				print('\n  --- summary ---')

				for channel, stats in sorted(monitor.stats.items()):
					marker = '  <- control channel' if stats.looks_like_control else ''
					print(f'  {describe(channel):<28} {stats.summary()}{marker}')

				if monitor.radios:
					print(f'  radios heard: ' + ', '.join(
						f'{r} (x{n})' for r, n in monitor.radios.most_common(8)))

				print()

	except KeyboardInterrupt:
		print('\nstopped')

	finally:
		log.close()

	if monitor.radios:
		print(f'\n{len(monitor.radios)} distinct radios heard, '
			  f'{len(monitor.sightings)} sightings logged')

	return 0


def scan_channels(config: Config, radio, spectrum_source, symbol_source,
				  fm_source = None, n_channels: int = 12, dwell_seconds: float = 3.0,
				  survey_seconds: float = 10.0) -> int:
	"""Sweep the strongest channels and score each for P25 C4FM.

	Finding the control channel is the hard part of using a trunking decoder,
	and a strong carrier is very often not a P25 one. This measures the symbol
	clock line rather than guessing from signal strength.
	"""
	import numpy as np

	from .dsp.noise_floor import NoiseFloorLeveller

	rf = config.rf
	leveller = NoiseFloorLeveller(rf.n_channels)
	quality_source = fm_source if fm_source is not None else symbol_source
	rate = radio.actual_iq_rate if fm_source is not None else 4800.0

	print(f'Surveying {rf.n_channels} channels around '
		  f'{rf.tuned_freq / 1e6:.5f} MHz for {survey_seconds:.0f}s '
		  f'at gain {rf.gain:.0f}...')

	peak = None
	deadline = time.monotonic() + survey_seconds

	while time.monotonic() < deadline:
		frame = spectrum_source.get_frame(200)

		if frame is not None:
			levelled = leveller.level(frame)
			peak = levelled if peak is None else np.maximum(peak, levelled)

		quality_source.get_symbols(0)

	if peak is None:
		import sys
		print('pigfm: no spectrum frames received', file = sys.stderr)
		return 1

	candidates = [int(c) for c in np.argsort(peak)[-n_channels:][::-1]]

	print(f'\nTesting the {len(candidates)} strongest channels for a 4800 baud '
		  f'clock line ({dwell_seconds:.0f}s each)\n')
	print(f'{"ch":>4} {"MHz":>11} {"peak dB":>8} {"4800 Hz":>9} {"top line":>9} {"verdict":>12}')

	found = []

	for channel in candidates:
		radio.set_decode_channel(channel)
		time.sleep(RETUNE_SETTLE_SECONDS)

		while quality_source.get_symbols(0).size:
			spectrum_source.get_frame(0)

		blocks = []
		deadline = time.monotonic() + dwell_seconds

		while time.monotonic() < deadline:
			spectrum_source.get_frame(0)
			block = quality_source.get_symbols(20)

			if block.size:
				blocks.append(block)

		samples = np.concatenate(blocks) if blocks else np.empty(0, dtype = np.float32)
		strength, top = baud_line_strength(samples, rate)

		is_c4fm = strength >= C4FM_BAUD_THRESHOLD
		verdict = 'P25 C4FM' if is_c4fm else ('no data' if samples.size == 0 else 'not C4FM')

		if is_c4fm:
			found.append(channel)

		print(f'{channel:>4} {rf.channel_to_freq(channel) / 1e6:>11.4f} {peak[channel]:>8.1f} '
			  f'{strength:>8.1f}x {top:>8.0f} {verdict:>12}')

	print()

	if found:
		print('P25 found on: ' + ', '.join(
			f'channel {c} ({rf.channel_to_freq(c) / 1e6:.4f} MHz)' for c in found))
		print(f'Decode it with:  --decode --decode-channel {found[0]}')
	else:
		print('No 4800 baud clock line found, so nothing here is P25 C4FM.')
		print('For reference, genuine C4FM scores around 330x, analogue FM around 3x.')
		print('Try: a higher gain (--gain 45), --base-station, a different centre_freq,')
		print('or --self-test to confirm the antenna is working.')

	return 0
