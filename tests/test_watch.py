"""Activity watching.

The per-channel baseline is the point: a single threshold across all channels
chatters on the noisy ones and stays silent on the quiet ones, which is what
made an earlier version report a channel as 76% busy when it was present in
0.3% of frames.
"""

import numpy as np

from pigfm.watch import ActivityWatcher, BurstLog

N_CHANNELS = 16
FRAME_INTERVAL = 0.165


def _freq(channel: int) -> float:
	return 170e6 + channel * 12500


def _watcher(margin_db = 10.0):
	return ActivityWatcher(N_CHANNELS, _freq, margin_db = margin_db)


def _run(watcher, plan, noise_db = -40.0, seed = 1):
	"""plan maps frame index -> {channel: level}. Returns completed bursts."""
	rng = np.random.default_rng(seed)
	bursts = []
	now = 0.0

	for i in range(len(plan)):
		frame = rng.normal(noise_db, 1.0, N_CHANNELS)

		for channel, level in plan[i].items():
			frame[channel] = level

		bursts += watcher.update(frame.astype(np.float32), now = now)
		now += FRAME_INTERVAL

	return bursts


def test_a_transmission_is_logged_once_with_its_duration():
	plan = [{} if not (100 <= i < 160) else {7: -20.0} for i in range(400)]
	watcher = _watcher()

	bursts = _run(watcher, plan)

	assert len(bursts) == 1
	assert bursts[0].channel == 7
	assert bursts[0].frequency == _freq(7)
	assert 8.0 < bursts[0].duration < 11.0        # 60 frames at 165 ms
	assert bursts[0].peak_db > 15


def test_quiet_band_produces_nothing():
	watcher = _watcher()

	assert _run(watcher, [{} for _ in range(400)]) == []
	assert watcher.bursts == 0


def test_a_noisy_channel_does_not_trigger_on_its_own_noise():
	"""One channel sits 8 dB hotter than the rest throughout. Judged against a
	global threshold it would look permanently busy; judged against its own
	baseline it is simply a noisier channel."""
	plan = [{3: -32.0} for _ in range(400)]
	watcher = _watcher(margin_db = 10.0)

	assert _run(watcher, plan) == []
	assert watcher.bursts == 0


def test_a_weak_signal_on_a_quiet_channel_is_still_found():
	plan = [{} if not (150 <= i < 200) else {11: -26.0} for i in range(400)]

	bursts = _run(_watcher(), plan)

	assert len(bursts) == 1
	assert bursts[0].channel == 11


def test_a_channel_hovering_at_the_threshold_is_not_one_long_burst():
	"""The bug this replaced: an event stayed open through every gap, so a
	marginal channel reported a single burst hundreds of seconds long."""
	plan = [{5: -20.0} if 100 <= i < 300 and i % 3 == 0 else {} for i in range(400)]

	bursts = _run(_watcher(), plan)

	assert all(b.duration < 20.0 for b in bursts), 'gaps must end a burst'


def test_short_clicks_are_ignored():
	plan = [{2: -15.0} if i in (100, 200, 300) else {} for i in range(400)]

	assert _run(_watcher(), plan) == []


def test_summary_ranks_by_airtime():
	plan = []

	for i in range(600):
		active = {}

		if 100 <= i < 300: active[4] = -20.0     # long
		if 400 <= i < 430: active[9] = -20.0     # short
		plan.append(active)

	watcher = _watcher()
	_run(watcher, plan)

	rows = watcher.summary()

	assert rows[0][0] == 4
	assert rows[0][3] > rows[1][3]


def test_burst_log_is_a_no_op_without_a_filename():
	log = BurstLog(None)
	plan = [{} if not (10 <= i < 80) else {1: -20.0} for i in range(200)]

	for burst in _run(_watcher(), plan):
		log.write(burst)

	log.close()


def test_burst_log_writes_lines(tmp_path = None):
	import tempfile
	from pathlib import Path

	path = Path(tempfile.mkdtemp()) / 'activity.log'
	log = BurstLog(str(path))

	plan = [{} if not (10 <= i < 80) else {1: -20.0} for i in range(200)]
	written = 0

	for burst in _run(_watcher(), plan):
		log.write(burst)
		written += 1

	log.close()

	assert written >= 1
	assert path.read_text().count('\n') == written
	assert 'MHz' in path.read_text()
