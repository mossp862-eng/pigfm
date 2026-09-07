"""Scanner event lifecycle, independent of the golden master."""

import numpy as np

from pigfm.scanner import ChannelScanner, Event

N = 256
THRESHOLD = -13


def frame(levels: dict[int, float], floor = -45.0) -> np.ndarray:
	out = np.full(N, floor, dtype = np.float32)

	for channel, level in levels.items():
		out[channel] = level

	return out


def test_event_starts_when_channel_crosses_threshold():
	scanner = ChannelScanner(threshold = THRESHOLD)

	result = scanner.update(frame({40: -5.0}))

	assert [e.channel for e in result.started] == [40]
	assert result.ended == []
	assert [e.channel for e in scanner.active_events] == [40]


def test_event_is_held_within_the_release_margin():
	"""Between the threshold and threshold minus the margin the event persists,
	which is what stops a signal sitting on the limit chattering on and off."""
	scanner = ChannelScanner(threshold = THRESHOLD, release_margin = 4)
	scanner.update(frame({40: -5.0}))

	result = scanner.update(frame({40: -16.0}))   # below threshold, inside margin

	assert result.ended == []
	assert scanner.active_events[0].active is True

	result = scanner.update(frame({40: -18.0}))   # clear of the margin

	assert [e.channel for e in result.ended] == [40]
	assert scanner.active_events == []


def test_max_power_is_retained_after_the_event_ends():
	scanner = ChannelScanner(threshold = THRESHOLD)
	scanner.update(frame({40: -9.0}))
	scanner.update(frame({40: -3.0}))
	scanner.update(frame({40: -11.0}))
	result = scanner.update(frame({40: -40.0}))

	assert result.ended[0].max_pwr == -3.0


def test_ignored_channels_never_raise_events():
	scanner = ChannelScanner(threshold = THRESHOLD, ignore_list = {40})

	result = scanner.update(frame({40: -2.0, 41: -2.0}))

	assert [e.channel for e in result.started] == [41]


def test_history_is_capped_and_newest_first():
	scanner = ChannelScanner(threshold = THRESHOLD, history_length = 5)

	for channel in range(10):
		scanner.update(frame({channel: -2.0}))
		scanner.update(frame({}))

	assert len(scanner.history) == 5
	assert [e.channel for e in scanner.history] == [9, 8, 7, 6, 5]


def test_most_recent_is_none_before_anything_happens():
	"""The original indexed events[0] into a list pre-filled with None, so
	pressing 'a' on a fresh start raised AttributeError into a bare except."""
	assert ChannelScanner(threshold = THRESHOLD).most_recent() is None


def test_most_recent_returns_newest_event():
	scanner = ChannelScanner(threshold = THRESHOLD)
	scanner.update(frame({40: -2.0}))
	scanner.update(frame({40: -2.0, 77: -2.0}))

	assert scanner.most_recent().channel == 77


def test_describe_changes_tense_when_the_event_ends():
	event = Event(channel = 3, pwr = -7.0)

	assert 'is active' in event.describe()

	event.set_inactive()

	assert 'was active' in event.describe()
