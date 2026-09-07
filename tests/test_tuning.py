"""Temporary tuning offsets, used to listen to the base stations for testing."""

import tempfile
from pathlib import Path

from pigfm.app import apply_tuning_offset, parse_args
from pigfm.config import DEFAULT_DUPLEX_OFFSET, load

from .test_config import SAMPLE


def _write(text = SAMPLE) -> Path:
	handle = tempfile.NamedTemporaryFile('w', suffix = '.ini', delete = False)
	handle.write(text)
	handle.close()

	return Path(handle.name)


def test_duplex_offset_falls_back_when_absent():
	"""The shipped config gained the key, but older user configs will not have
	it and must keep working."""
	config = load('config/rmr.ini')
	assert config.rf.duplex_offset == 4_500_000

	path = _write()

	try:
		assert load(path).rf.duplex_offset == DEFAULT_DUPLEX_OFFSET
	finally:
		path.unlink()


def test_no_offset_by_default():
	config = load('config/rmr.ini')

	assert config.rf.tuning_offset == 0.0
	assert config.rf.tuned_freq == config.rf.centre_freq
	assert config.is_temporarily_tuned is False


def test_base_station_mode_tunes_down_by_the_duplex_offset():
	"""Matches the debugging line the original had commented out:
	`self.centre_freq -= 4500000`."""
	config = load('config/rmr.ini')
	config.rf.tuning_offset = config.rf.base_station_offset

	assert config.rf.base_station_offset == -4_500_000
	assert config.rf.tuned_freq == 171_293_750 - 4_500_000 == 166_793_750
	assert config.is_temporarily_tuned is True


def test_every_channel_shifts_with_the_offset():
	config = load('config/rmr.ini')
	before = [config.rf.channel_to_freq(c) for c in range(0, 256, 16)]

	config.rf.tuning_offset = config.rf.base_station_offset
	after = [config.rf.channel_to_freq(c) for c in range(0, 256, 16)]

	for was, now in zip(before, after):
		assert now == was - 4_500_000


def test_baseband_channel_offset_is_unaffected():
	"""The IQ tap works in baseband, so a channel sits in the same place in the
	passband no matter where the receiver is tuned."""
	config = load('config/rmr.ini')
	before = [config.rf.channel_offset(c) for c in range(0, 256, 16)]

	config.rf.tuning_offset = config.rf.base_station_offset

	assert [config.rf.channel_offset(c) for c in range(0, 256, 16)] == before


def test_offset_run_never_writes_to_the_config():
	"""Channel numbers mean different frequencies with an offset applied, so an
	ignore list built while testing must not leak into the next normal run."""
	path = _write()

	try:
		original = path.read_text()

		config = load(path)
		config.rf.tuning_offset = config.rf.base_station_offset
		config.scanner.active_threshold = -3
		config.alarm.ignore_list = {1, 2, 3}
		config.alarm.mute = True

		assert config.save_mutable() is False
		assert path.read_text() == original

	finally:
		path.unlink()


def test_normal_run_still_writes():
	path = _write()

	try:
		config = load(path)
		config.scanner.active_threshold = -3

		assert config.save_mutable() is True
		assert load(path).scanner.active_threshold == -3

	finally:
		path.unlink()


def test_cli_base_station_flag():
	config = load('config/rmr.ini')
	apply_tuning_offset(config, parse_args(['config/rmr.ini', '--base-station']))

	assert config.rf.tuning_offset == -4_500_000


def test_cli_manual_offset():
	config = load('config/rmr.ini')
	apply_tuning_offset(config, parse_args(['config/rmr.ini', '--tuning-offset', '-1e6']))

	assert config.rf.tuning_offset == -1_000_000
	assert config.rf.tuned_freq == 170_293_750


def test_cli_offsets_are_mutually_exclusive():
	try:
		parse_args(['config/rmr.ini', '--base-station', '--tuning-offset', '100'])
	except SystemExit:
		return

	raise AssertionError('expected argparse to reject both offset options together')


def test_cli_default_leaves_tuning_alone():
	config = load('config/rmr.ini')
	apply_tuning_offset(config, parse_args(['config/rmr.ini']))

	assert config.rf.tuning_offset == 0.0
