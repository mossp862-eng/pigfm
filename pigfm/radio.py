"""GNURadio flowgraph: one dongle, two branches.

	rtlsdr_source
	  |
	  +-- stream_to_vector -> fft -> mag^2 -> iir -> keep_one_in_n -> log10
	  |     -> zmq push :5555   (spectrum, drives the display and scanner)
	  |
	  +-- freq_xlating_fir -> fir -> zmq push :5556   (one channel of raw IQ)

The spectrum branch is unchanged from the original. The IQ branch is new: the
original discarded raw IQ entirely, and P25 metadata decode needs it.
"""

from gnuradio import blocks, fft, filter as gr_filter, gr, zeromq
from gnuradio.fft import window
from gnuradio.filter import firdes

import osmosdr

from .config import RfConfig
from .dsp.p25.demod import C4fmDemod
from .frames import FM_ENDPOINT, IQ_ENDPOINT, SPECTRUM_ENDPOINT, SYMBOL_ENDPOINT

# Sample rate of the extracted channel. P25 C4FM runs at 4800 symbols/s, so this
# is a little over ten samples per symbol, which is comfortable for timing
# recovery. The channel itself is only 12.5 kHz wide, but decimating that far
# would leave under three samples per symbol and nothing to sync on.
IQ_RATE = 50_000

# Channel filter, applied in the second decimation stage.
CHANNEL_CUTOFF = 6_250
CHANNEL_TRANSITION = 2_000

# First-stage filter. The stopband is placed at the intermediate Nyquist rate so
# nothing can alias into the band before the sharp channel filter runs.
COARSE_CUTOFF = 60_000
COARSE_TRANSITION = 40_000

# Frames dropped for each one published. The original accepted this as an
# argument and then ignored it, hardcoding 128 in the block.
DEFAULT_KEEP_ONE_IN_N = 128

# ZMQ sink timeout in ms. A sink whose consumer is absent or slow drops after
# this long rather than blocking.
#
# Deliberately short. Every sink that is built applies backpressure to the whole
# flowgraph while it waits, so a branch nobody is reading will stall the ones
# that are being read, and the receiver then overruns and drops samples. Keeping
# it brief means an idle branch costs throughput rather than wedging the graph.
# Build only the branches you intend to consume as well.
SINK_TIMEOUT_MS = 20


class Radio(gr.top_block):
	def __init__(self, rf: RfConfig, spectrum_endpoint: str = SPECTRUM_ENDPOINT,
				 iq_endpoint: str = IQ_ENDPOINT, enable_iq: bool = False,
				 iq_rate: int = IQ_RATE, keep_one_in_n: int = DEFAULT_KEEP_ONE_IN_N,
				 decode_channel: int | None = None, device_args: str = '',
				 enable_decode: bool = False, symbol_endpoint: str = SYMBOL_ENDPOINT,
				 fm_endpoint: str = FM_ENDPOINT, enable_fm: bool = False):
		super().__init__('pigfm_radio', catch_exceptions = True)

		self.rf = rf
		self.iq_rate = iq_rate

		self.source = osmosdr.source(args = device_args)
		self.source.set_sample_rate(rf.samp_rate)
		self.source.set_center_freq(rf.tuned_freq)
		self.source.set_gain(rf.gain)

		self._build_spectrum_branch(rf, spectrum_endpoint, keep_one_in_n)

		self._xlate = None
		self.demod = None

		if enable_iq or enable_decode:
			channel = rf.n_channels // 2 if decode_channel is None else decode_channel
			self._build_iq_branch(rf, iq_endpoint, iq_rate, channel,
								  enable_iq = enable_iq, enable_decode = enable_decode,
								  symbol_endpoint = symbol_endpoint,
								  fm_endpoint = fm_endpoint, enable_fm = enable_fm)

	def _build_spectrum_branch(self, rf: RfConfig, endpoint: str, keep_one_in_n: int) -> None:
		"""Unchanged signal path. Do not alter without re-running the golden-master tests."""
		fft_size = rf.fft_size

		self.to_vector = blocks.stream_to_vector(gr.sizeof_gr_complex, fft_size)
		self.fft = fft.fft_vcc(fft_size, True, window.blackmanharris(fft_size), True, 1)
		self.mag_squared = blocks.complex_to_mag_squared(fft_size)
		self.averager = gr_filter.single_pole_iir_filter_ff(rf.iir_alpha, fft_size)
		self.decimator = blocks.keep_one_in_n(gr.sizeof_float * fft_size, keep_one_in_n)
		self.to_db = blocks.nlog10_ff(10, fft_size, 0)
		self.spectrum_sink = zeromq.push_sink(gr.sizeof_float, fft_size, endpoint,
											  SINK_TIMEOUT_MS, False, -1, True)

		self.connect(self.source, self.to_vector, self.fft, self.mag_squared,
					 self.averager, self.decimator, self.to_db, self.spectrum_sink)

	def _build_iq_branch(self, rf: RfConfig, endpoint: str, iq_rate: int, channel: int,
						 enable_iq: bool = True, enable_decode: bool = False,
						 symbol_endpoint: str = SYMBOL_ENDPOINT,
						 fm_endpoint: str = FM_ENDPOINT, enable_fm: bool = False) -> None:
		"""Extract one 12.5 kHz channel as raw IQ, for the decoder to consume.

		Decimation is split in two. A single stage from 3.2 MSPS straight down to
		50 kSPS with a 2 kHz transition would need thousands of taps running at
		the full input rate, which a Raspberry Pi will not sustain. Coarse first,
		sharp second, at a rate 16 times lower.
		"""
		total_decim = int(round(rf.samp_rate / iq_rate))
		coarse_decim, fine_decim = _split_decimation(total_decim)
		intermediate_rate = rf.samp_rate / coarse_decim

		coarse_taps = firdes.low_pass(1.0, rf.samp_rate, COARSE_CUTOFF, COARSE_TRANSITION)
		fine_taps = firdes.low_pass(1.0, intermediate_rate, CHANNEL_CUTOFF, CHANNEL_TRANSITION)

		self._xlate = gr_filter.freq_xlating_fir_filter_ccf(
			coarse_decim, coarse_taps, rf.channel_offset(channel), rf.samp_rate)
		self.channel_filter = gr_filter.fir_filter_ccf(fine_decim, fine_taps)

		self.connect(self.source, self._xlate, self.channel_filter)

		self.decode_channel = channel
		self.actual_iq_rate = intermediate_rate / fine_decim

		if enable_iq:
			self.iq_sink = zeromq.push_sink(gr.sizeof_gr_complex, 1, endpoint,
											SINK_TIMEOUT_MS, False, -1, True)
			self.connect(self.channel_filter, self.iq_sink)

		if enable_decode:
			# C4FM demodulation and symbol recovery run here, in C++. What
			# reaches Python is 4800 symbols per second, which is nothing.
			self.demod = C4fmDemod(self.actual_iq_rate)
			self.symbol_sink = zeromq.push_sink(gr.sizeof_float, 1, symbol_endpoint,
												SINK_TIMEOUT_MS, False, -1, True)
			self.connect(self.channel_filter, self.demod)
			self.connect((self.demod, 0), self.symbol_sink)

			# Port 1 is the demodulated signal ahead of clock recovery. Only
			# published when something is going to read it: a sink nobody drains
			# throttles the whole flowgraph.
			if enable_fm:
				self.fm_sink = zeromq.push_sink(gr.sizeof_float, 1, fm_endpoint,
												SINK_TIMEOUT_MS, False, -1, True)
				self.connect((self.demod, 1), self.fm_sink)
			else:
				self.fm_null = blocks.null_sink(gr.sizeof_float)
				self.connect((self.demod, 1), self.fm_null)

	@property
	def iq_enabled(self) -> bool:
		return self._xlate is not None

	@property
	def decode_enabled(self) -> bool:
		return self.demod is not None

	def set_decode_channel(self, channel: int) -> None:
		"""Point the IQ branch at a different channel without retuning the dongle."""
		if self._xlate is None:
			raise RuntimeError('IQ branch is not enabled on this flowgraph')

		self._xlate.set_center_freq(self.rf.channel_offset(channel))
		self.decode_channel = channel

	def set_gain(self, gain: float) -> None:
		self.source.set_gain(gain)
		self.rf.gain = gain

	def close(self) -> None:
		self.stop()
		self.wait()


def _split_decimation(total: int) -> tuple[int, int]:
	"""Split a total decimation into (coarse, fine), preferring a fine stage of
	about four so the sharp filter runs at a manageable rate."""
	for fine in (4, 5, 8, 2, 10, 1):
		if total % fine == 0 and total // fine >= 1:
			return total // fine, fine

	return total, 1
