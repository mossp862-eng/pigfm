"""Config loading, derived geometry, and comment-preserving save."""

import tempfile
from pathlib import Path

from pigfm.config import BINS_PER_CHANNEL, load

SAMPLE = """\
# Regional Mobile Radio, Victoria
# Centre frequency derived per the README formula.

[rf]
centre_freq = 171293750
channel_spacing = 12500
gain = 20
iir_alpha = 0.02
n_channels = 256

[display]
system_name = Regional Mobile Radio (VIC)
min_amp = -20
amp_range = 40
amp_step = 10

[scanner]
# Raise this in high-noise environments
active_threshold = -13

[alarm]
mute = False
ignore_list = 248

[logging]
do_log = True
filename = rmr.log
"""


def _write(text = SAMPLE):
	handle = tempfile.NamedTemporaryFile('w', suffix = '.ini', delete = False)
	handle.write(text)
	handle.close()

	return Path(handle.name)


def test_loads_shipped_config():
	"""Structure only.

	Deliberately asserts nothing about active_threshold, mute or ignore_list:
	the program rewrites those three keys on exit, so any run of PiGFM against
	real hardware legitimately changes them. Their behaviour is covered against
	a temporary file below instead.
	"""
	config = load('config/rmr.ini')

	assert config.rf.n_channels == 256
	assert config.rf.centre_freq == 171_293_750
	assert config.rf.channel_spacing == 12_500
	assert config.display.system_name == 'Regional Mobile Radio (VIC)'
	assert config.logging.filename == 'rmr.log'
	assert isinstance(config.alarm.ignore_list, set)


def test_sample_rate_and_fft_size_are_derived():
	"""Both were hardcoded in the original, and both are functions of the
	channel plan. This is what makes n_channels actually configurable."""
	config = load('config/rmr.ini')

	assert config.rf.samp_rate == 256 * 12500 == 3_200_000
	assert config.rf.fft_size == 256 * BINS_PER_CHANNEL == 4096


def test_display_geometry_is_derived():
	config = load('config/rmr.ini')

	assert config.display.max_amp == 20
	assert config.display.n_rows == 20        # was hardcoded via SPEC_WIN_HEIGHT
	assert config.display.rows_per_step == 5  # was the literal 5 in scale_map


def test_save_preserves_comments_and_ordering():
	path = _write()

	try:
		config = load(path)
		config.scanner.active_threshold = -9
		config.alarm.mute = True
		config.alarm.ignore_list = {12, 248}
		config.save_mutable()

		text = path.read_text()

		assert '# Raise this in high-noise environments' in text
		assert '# Regional Mobile Radio, Victoria' in text
		assert 'active_threshold = -9' in text
		assert 'mute = True' in text
		assert 'ignore_list = 12, 248' in text
		assert text.index('[rf]') < text.index('[display]') < text.index('[scanner]')

		# And it must still load back to what we saved.
		again = load(path)
		assert again.scanner.active_threshold == -9
		assert again.alarm.mute is True
		assert again.alarm.ignore_list == {12, 248}

	finally:
		path.unlink()


def test_save_appends_absent_key():
	path = _write(SAMPLE.replace('ignore_list = 248\n', ''))

	try:
		config = load(path)
		config.alarm.ignore_list = {7}
		config.save_mutable()

		assert load(path).alarm.ignore_list == {7}

	finally:
		path.unlink()


def test_missing_file_raises():
	try:
		load('config/does-not-exist.ini')
	except FileNotFoundError:
		return

	raise AssertionError('expected FileNotFoundError')
