"""P25 metadata decoding: channel following, statistics and the diagnostic mode.

This is the part that turns "channel 172 is busy" into "talkgroup 1234, radio
5551234". It reuses the existing scanner rather than detecting activity again.
"""

import time
from collections import Counter
from dataclasses import dataclass, field

from .config import Config
from .dsp.noise_floor import NoiseFloorLeveller
from .dsp.p25.constants import DUID_NAMES, DUID_TSDU, TSBK_ENCODED_DIBITS
from .dsp.p25.framing import P25Framer
from .dsp.p25.symbols import c4fm_quality
from .dsp.p25.tsbk import decode_tsbk
from .scanner import ChannelScanner

# A channel scoring at least this on both measures is carrying C4FM. Noise sits
# near 0.25 outer and 0.5 correlation, so these clear it comfortably.
C4FM_OUTER_THRESHOLD = 0.40
C4FM_SYNC_THRESHOLD = 0.75

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

		result = self.scanner.update(self.leveller.level(frame))

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
			decoded.append((unit, tsbks))

		return decoded

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
				   pinned_channel: int | None = None, summary_seconds: float = 10.0) -> int:
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

			now = time.monotonic()

			if now - last_summary >= summary_seconds:
				last_summary = now
				print('\n  --- summary ---')

				for channel, stats in sorted(monitor.stats.items()):
					marker = '  <- control channel' if stats.looks_like_control else ''
					print(f'  {describe(channel):<28} {stats.summary()}{marker}')

				print()

	except KeyboardInterrupt:
		print('\nstopped')

	return 0


def scan_channels(config: Config, radio, spectrum_source, symbol_source,
				  n_channels: int = 10, dwell_seconds: float = 3.0,
				  survey_seconds: float = 10.0) -> int:
	"""Sweep the strongest channels and score each for P25 C4FM.

	Finding the control channel is the hard part of using a trunking decoder,
	and a strong carrier is not necessarily a P25 one. This says which is which
	rather than leaving you to guess.
	"""
	import numpy as np

	from .dsp.noise_floor import NoiseFloorLeveller

	rf = config.rf
	leveller = NoiseFloorLeveller(rf.n_channels)

	print(f'Surveying {rf.n_channels} channels around '
		  f'{rf.tuned_freq / 1e6:.5f} MHz for {survey_seconds:.0f}s...')

	peak = None
	deadline = time.monotonic() + survey_seconds

	while time.monotonic() < deadline:
		frame = spectrum_source.get_frame(200)

		if frame is not None:
			levelled = leveller.level(frame)
			peak = levelled if peak is None else np.maximum(peak, levelled)

		symbol_source.get_symbols(0)

	if peak is None:
		print('pigfm: no spectrum frames received', file = __import__('sys').stderr)
		return 1

	candidates = [int(c) for c in np.argsort(peak)[-n_channels:][::-1]]

	print(f'\nTesting the {len(candidates)} strongest channels for C4FM '
		  f'({dwell_seconds:.0f}s each)\n')
	print(f'{"ch":>4} {"MHz":>11} {"peak dB":>8} {"4-level":>8} {"sync":>6} {"verdict":>14}')

	found = []

	for channel in candidates:
		radio.set_decode_channel(channel)
		time.sleep(RETUNE_SETTLE_SECONDS)

		while symbol_source.get_symbols(0).size:
			spectrum_source.get_frame(0)

		blocks = []
		deadline = time.monotonic() + dwell_seconds

		while time.monotonic() < deadline:
			spectrum_source.get_frame(0)
			block = symbol_source.get_symbols(20)

			if block.size:
				blocks.append(block)

		symbols = np.concatenate(blocks) if blocks else np.empty(0, dtype = np.float32)
		outer, correlation = c4fm_quality(symbols)

		is_c4fm = outer >= C4FM_OUTER_THRESHOLD and correlation >= C4FM_SYNC_THRESHOLD
		verdict = 'P25 C4FM' if is_c4fm else ('no symbols' if symbols.size == 0 else 'not C4FM')

		if is_c4fm:
			found.append(channel)

		print(f'{channel:>4} {rf.channel_to_freq(channel) / 1e6:>11.4f} {peak[channel]:>8.1f} '
			  f'{outer:>8.2f} {correlation:>6.2f} {verdict:>14}')

	print()

	if found:
		print('P25 found on: ' + ', '.join(
			f'channel {c} ({rf.channel_to_freq(c) / 1e6:.4f} MHz)' for c in found))
		print(f'Decode it with:  --decode --decode-channel {found[0]}')
	else:
		print('No P25 C4FM found. The strong signals here are something else.')
		print('Try --base-station (control channels are on the base downlink), a')
		print('different centre_freq, or a better antenna.')

	return 0
