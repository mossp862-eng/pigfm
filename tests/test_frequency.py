"""Pin the channel-to-frequency mapping empirically.

This is the one place the refactor deliberately changes behaviour. The original
subtracted half a channel spacing, which put every label one whole channel out
and placed channel 0 outside the band the dongle can see. Rather than assert the
formula against itself, these tests synthesise a tone at the frequency the
formula claims and check which channel the real FFT reduction puts it in.
"""

import numpy as np

from pigfm.config import load
from pigfm.dsp.channels import channel_powers

CONFIG = load('config/rmr.ini')
RF = CONFIG.rf

# Every 8th channel plus both edges. Testing all 256 works but costs a 4096-point
# FFT each, which is a slow test for no extra signal.
SAMPLED_CHANNELS = list(range(0, RF.n_channels, 8)) + [RF.n_channels - 1]


def peak_channel(offset_hz: float) -> int:
	"""Which channel a baseband tone at this offset actually lands in."""
	n = np.arange(RF.fft_size)
	tone = np.exp(2j * np.pi * offset_hz * n / RF.samp_rate) * np.blackman(RF.fft_size)

	spectrum = np.fft.fftshift(np.fft.fft(tone))       # fft_vcc(..., shift = True)
	power_db = 10 * np.log10(np.abs(spectrum) ** 2 + 1e-20)

	return int(np.argmax(channel_powers(power_db.astype(np.float32), RF.n_channels)))


def test_a_tone_lands_in_the_channel_the_formula_names():
	for channel in SAMPLED_CHANNELS:
		assert peak_channel(RF.channel_offset(channel)) == channel, \
			f'tone for channel {channel} landed elsewhere'


def test_every_channel_is_inside_the_receivable_band():
	"""The original put channel 0 at -1,606,250 Hz, outside the +/-1.6 MHz the
	dongle sees at 3.2 MSPS, so it could never be received at all."""
	nyquist = RF.samp_rate / 2

	for channel in range(RF.n_channels):
		assert abs(RF.channel_offset(channel)) < nyquist


def test_band_is_centred_on_the_configured_frequency():
	lowest = RF.channel_to_freq(0)
	highest = RF.channel_to_freq(RF.n_channels - 1)

	assert abs((lowest + highest) / 2 - RF.centre_freq) < 1e-6


def test_channels_are_one_spacing_apart():
	assert RF.channel_to_freq(1) - RF.channel_to_freq(0) == RF.channel_spacing
