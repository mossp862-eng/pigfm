"""Application wiring and the main loop.

Everything below is assembled from parts that can each be built and tested on
their own. The original did all of this inside PigFm.__init__, which finished by
calling self.run(), so the object could never exist without also running.
"""

import argparse
import curses
import sys

from . import config as config_module
from .eventlog import EventLog
from .frames import (DEFAULT_TIMEOUT_MS, FrameSource, SymbolSource, SyntheticFrameSource,
                     ZmqFrameSource, nominal_frame_interval)
from .scanner import ChannelScanner
from .ui import Ui


class App:
	def __init__(self, stdscr, config: config_module.Config, source: FrameSource,
				 radio = None):
		self.config = config
		self.source = source
		self.radio = radio
		self.running = True

		from .dsp.noise_floor import NoiseFloorLeveller

		self.leveller = NoiseFloorLeveller(config.rf.n_channels)
		self.scanner = ChannelScanner(threshold = config.scanner.active_threshold,
									  release_margin = config.scanner.release_margin,
									  ignore_list = config.alarm.ignore_list)
		self.log = EventLog(config.logging.filename, config.logging.do_log)
		self.ui = Ui(stdscr, config)

		self.ui.draw_control(self.scanner.threshold, config.alarm.mute, config.alarm.ignore_list)

	def run(self) -> None:
		try:
			while self.running:
				frame = self.source.get_frame(DEFAULT_TIMEOUT_MS)

				if frame is not None:
					self.process(frame)

				self.handle_input()

		except KeyboardInterrupt:
			pass

	def process(self, frame) -> None:
		levelled = self.leveller.level(frame)

		self.ui.draw_spectrum(levelled)

		result = self.scanner.update(levelled)

		if result.started and not self.config.alarm.mute:
			self.ui.beep()

		for event in result.ended:
			if event.channel not in self.config.alarm.ignore_list:
				self.log.write(event)

		self.ui.draw_activity(list(self.scanner.history), self.config.alarm.ignore_list)

	def handle_input(self) -> None:
		"""Drain every key waiting, so a burst does not lag behind the frames."""
		while True:
			key = self.ui.read_key()

			if key is None:
				return

			if self.dispatch(key):
				self.ui.draw_control(self.scanner.threshold, self.config.alarm.mute,
									 self.config.alarm.ignore_list)

	def dispatch(self, key: str) -> bool:
		"""Returns True when the control panel needs redrawing.

		The original wrapped this in a bare `except: pass`, so pressing 'a'
		before any event existed raised AttributeError and vanished silently,
		along with every other error in the handler.
		"""
		display = self.config.display

		if key == 'KEY_RESIZE':
			self.ui.resize()
			self.ui.draw_control(self.scanner.threshold, self.config.alarm.mute,
								 self.config.alarm.ignore_list)
			return False

		if key == 'm':
			self.config.alarm.mute = not self.config.alarm.mute

		elif key == 'c':
			self.config.alarm.ignore_list.clear()

		elif key == 'a':
			event = self.scanner.most_recent()

			if event is None:
				return False

			self.config.alarm.ignore_list.add(event.channel)

		elif key == 'KEY_UP' and self.scanner.threshold < display.max_amp:
			self.scanner.threshold += 1

		elif key == 'KEY_DOWN' and self.scanner.threshold > display.min_amp:
			self.scanner.threshold -= 1

		elif key == 'q':
			self.running = False

		else:
			return False

		self.config.scanner.active_threshold = self.scanner.threshold

		return True

	def close(self) -> None:
		self.log.close()


def parse_args(argv = None):
	parser = argparse.ArgumentParser(
		prog = 'pigfm',
		description = 'Police, intelligence and government frequency monitor.')

	parser.add_argument('config', help = 'path to a config .ini file, e.g. config/rmr.ini')
	parser.add_argument('--synthetic', action = 'store_true',
						help = 'run against fabricated traffic instead of a dongle')
	parser.add_argument('--seed', type = int, default = None,
						help = 'seed for --synthetic, for reproducible runs')
	parser.add_argument('--iq', action = 'store_true',
						help = 'enable the raw IQ branch of the flowgraph (for decoding)')
	parser.add_argument('--iq-channel', type = int, default = None,
						help = 'channel the IQ branch extracts, defaults to band centre')
	parser.add_argument('--device-args', default = '',
						help = 'osmosdr device arguments, e.g. "rtl=0"')

	parser.add_argument('--decode', action = 'store_true',
						help = 'decode P25 metadata (talkgroup and radio IDs) and print '
							   'it to the terminal instead of running the spectrum display')
	parser.add_argument('--decode-scan', action = 'store_true',
						help = 'sweep the strongest channels and report which carry P25 '
							   'C4FM, to find a control channel worth decoding')
	parser.add_argument('--decode-channel', type = int, default = None, metavar = 'N',
						help = 'pin decoding to one channel instead of following activity. '
							   'Use this for a control channel, which is where the '
							   'trunking identities are sent')

	# Testing aids. Both shift what the receiver tunes to without touching the
	# config file, and neither is persisted on exit.
	tuning = parser.add_mutually_exclusive_group()
	tuning.add_argument('--base-station', action = 'store_true',
						help = 'listen to the base station downlink instead of the '
							   'portables, by tuning down the duplex offset. Base '
							   'stations transmit often, so this is the quickest way '
							   'to confirm a working setup')
	tuning.add_argument('--tuning-offset', type = float, default = None, metavar = 'HZ',
						help = 'shift the tuned frequency by this many Hz, for testing')

	return parser.parse_args(argv)


def apply_tuning_offset(config, args) -> None:
	"""Both options are temporary and neither is written back to the config."""
	if args.base_station:
		config.rf.tuning_offset = config.rf.base_station_offset

	elif args.tuning_offset:
		config.rf.tuning_offset = args.tuning_offset


def run_decode(config, args) -> int:
	"""Terminal decode mode. Needs a receiver: there is nothing to decode in
	fabricated traffic."""
	if args.synthetic:
		print('pigfm: --decode needs a real receiver, not --synthetic', file = sys.stderr)
		return 1

	from .decode import run_diagnostic, scan_channels
	from .radio import Radio

	channel = args.decode_channel if args.decode_channel is not None else args.iq_channel

	radio = Radio(config.rf, enable_iq = args.iq, enable_decode = True,
				  decode_channel = channel, device_args = args.device_args)
	radio.start()

	spectrum = ZmqFrameSource(config.rf.n_channels)
	symbols = SymbolSource()

	try:
		if args.decode_scan:
			return scan_channels(config, radio, spectrum, symbols)

		return run_diagnostic(config, radio, spectrum, symbols,
							  pinned_channel = args.decode_channel)
	finally:
		spectrum.close()
		symbols.close()
		radio.close()


def main(argv = None) -> int:
	args = parse_args(argv)

	try:
		config = config_module.load(args.config)
	except (FileNotFoundError, KeyError) as exc:
		print(f'pigfm: {exc}', file = sys.stderr)
		return 1

	apply_tuning_offset(config, args)

	if args.decode or args.decode_scan:
		return run_decode(config, args)

	radio = None

	if args.synthetic:
		source: FrameSource = SyntheticFrameSource(
			config.rf.n_channels, seed = args.seed,
			frame_interval = nominal_frame_interval(config.rf))
	else:
		# Built and started before curses takes the terminal, so GNURadio's
		# startup chatter lands on a normal screen. The original started it with
		# curses already active and had to suspend and restore the terminal.
		from .radio import Radio

		radio = Radio(config.rf, enable_iq = args.iq, decode_channel = args.iq_channel,
					  device_args = args.device_args)
		radio.start()
		source = ZmqFrameSource(config.rf.n_channels)

	app = None

	try:
		def _boot(stdscr):
			nonlocal app
			app = App(stdscr, config, source, radio)
			app.run()

		curses.wrapper(_boot)

	finally:
		if app is not None:
			app.close()

		source.close()

		if radio is not None:
			radio.close()

		if not config.save_mutable():
			print(f'pigfm: tuned {config.rf.tuning_offset / 1e6:+.4f} MHz off {args.config}, '
				  f'so settings were not saved back to it', file = sys.stderr)

	return 0
