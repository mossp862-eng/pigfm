"""Receiver health checks.

Written after a long session spent analysing what turned out to be the
receiver's own noise. The configured gain was far too low, and there was no way
to tell that apart from "no signal here" without a known-good reference. FM
broadcast is that reference: it is the loudest thing in the spectrum almost
anywhere, so if it cannot be heard, nothing else will be either.
"""

import sys
import time

import numpy as np

FFT_SIZE = 4096

# FM broadcast, used as the reference signal. Present and strong essentially
# everywhere, which is what makes it a fair test of the antenna.
REFERENCE_BAND = (88.0e6, 108.0e6)
REFERENCE_STEP = 2.8e6

# A healthy setup hears FM broadcast far above the noise. Below the weak
# threshold something is connected but not working properly.
HEALTHY_SNR_DB = 35.0
WEAK_SNR_DB = 20.0

# The tuner's own local oscillator leaks into the middle of the passband. It is
# not a signal and must be excluded from every measurement.
DC_GUARD_HZ = 60e3


def _spectrum(samples: np.ndarray, centre: float, sample_rate: float):
	blocks = samples.size // FFT_SIZE

	if blocks == 0:
		return np.empty(0), np.empty(0)

	grid = samples[: blocks * FFT_SIZE].reshape(blocks, FFT_SIZE)
	grid = grid * np.blackman(FFT_SIZE).astype(np.float32)
	power = 10 * np.log10(
		np.abs(np.fft.fftshift(np.fft.fft(grid, axis = 1), axes = 1)) ** 2 + 1e-20)

	frequencies = centre + (np.arange(FFT_SIZE) - FFT_SIZE / 2) * (sample_rate / FFT_SIZE)
	keep = np.abs(frequencies - centre) > DC_GUARD_HZ

	return frequencies[keep], power.mean(axis = 0)[keep]


def measure(capture, centre: float, sample_rate: float, gain: float):
	"""Noise floor, strongest signal and how far above the floor it sits."""
	frequencies, power = _spectrum(capture(centre, gain), centre, sample_rate)

	if power.size == 0:
		return None

	floor = float(np.percentile(power, 30))
	index = int(np.argmax(power))

	return {
		'floor': floor,
		'peak': float(power[index]),
		'snr': float(power[index]) - floor,
		'peak_freq': float(frequencies[index]),
		'strong_bins': int((power > floor + 20).sum()),
	}


def run_self_test(config, capture, gains = (0, 20, 30, 40, 49.6)) -> int:
	"""Prove the receive chain works, and find a gain that suits this site.

	`capture(centre_hz, gain_db) -> np.ndarray` supplies IQ, so this is testable
	without a radio attached.
	"""
	rf = config.rf
	print('PiGFM receiver self test')
	print(f'  configured: {rf.tuned_freq / 1e6:.5f} MHz, '
		  f'{rf.samp_rate / 1e6:.2f} MSPS, gain {rf.gain:.0f}\n')

	print('Reference check: can the receiver hear FM broadcast?')
	print('  FM broadcast is the loudest signal in the spectrum almost anywhere,')
	print('  so this tests the antenna and the gain rather than the band you want.\n')
	print(f'  {"gain":>6} {"floor dB":>9} {"best SNR":>9} {"strong bins":>12}')

	best_snr = 0.0
	best_gain = rf.gain

	for gain in gains:
		snr = 0.0
		floor = 0.0
		strong = 0

		centre = REFERENCE_BAND[0] + REFERENCE_STEP / 2

		while centre < REFERENCE_BAND[1]:
			result = measure(capture, centre, rf.samp_rate, gain)

			if result and result['snr'] > snr:
				snr, floor, strong = result['snr'], result['floor'], result['strong_bins']

			centre += REFERENCE_STEP

		print(f'  {gain:>6.1f} {floor:>9.1f} {snr:>8.1f} {strong:>12}')

		if snr > best_snr:
			best_snr, best_gain = snr, gain

	print()

	if best_snr >= HEALTHY_SNR_DB:
		print(f'  PASS: FM broadcast is {best_snr:.0f} dB above the noise at gain {best_gain:.0f}.')
		print('  The antenna and receiver are working.')
	elif best_snr >= WEAK_SNR_DB:
		print(f'  WEAK: best SNR {best_snr:.0f} dB. Something is connected but not working well.')
		print('  Check the antenna, the connector, and any attenuator in line.')
	else:
		print(f'  FAIL: best SNR {best_snr:.0f} dB. FM broadcast should be 40 dB or more.')
		print('  The antenna is probably not connected, or is the wrong one entirely.')
		print('  Nothing meaningful is reaching the receiver, so no band will decode.')

	if abs(best_gain - rf.gain) > 5:
		print(f'\n  Your config uses gain {rf.gain:.0f}, but {best_gain:.0f} hears '
			  f'{best_snr:.0f} dB here.')
		print(f'  Consider setting gain = {best_gain:.0f} in the [rf] section, or pass '
			  f'--gain {best_gain:.0f}.')

	return 0 if best_snr >= WEAK_SNR_DB else 1


def osmosdr_capture(sample_rate: float, seconds: float = 1.0):
	"""Real capture function for run_self_test."""
	from gnuradio import blocks, gr

	import osmosdr

	def capture(centre: float, gain: float) -> np.ndarray:
		flowgraph = gr.top_block()
		source = osmosdr.source()
		source.set_sample_rate(sample_rate)
		source.set_center_freq(centre)
		source.set_gain(gain)

		head = blocks.head(gr.sizeof_gr_complex, int(sample_rate * seconds))
		sink = blocks.vector_sink_c()

		flowgraph.connect(source, head, sink)
		flowgraph.run()

		return np.array(sink.data(), dtype = np.complex64)

	return capture
