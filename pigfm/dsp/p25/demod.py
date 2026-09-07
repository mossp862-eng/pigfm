"""C4FM demodulation and symbol recovery, as a GNURadio hierarchical block.

Complex baseband in at the channel rate, one float per symbol out at 4800/s.
Everything in here runs in C++; the Python side of the decoder only ever sees
4800 symbols per second, which is why it can afford to be straightforward.

Being a hier_block2 rather than inline flowgraph code is what lets the tests
drive it from a vector source with no dongle attached.
"""

import math

from gnuradio import analog, digital, filter as gr_filter, gr
from gnuradio.filter import firdes

from .constants import SYMBOL_RATE, UNIT_DEVIATION_HZ

# Symbol clock recovery loop bandwidth, as a fraction of the symbol rate. Low
# enough to be steady on noise, quick enough to reacquire after the IQ tap is
# retuned to a different channel.
DEFAULT_LOOP_BW = 0.045

# The shaping filter ahead of clock recovery. C4FM occupies a little over the
# symbol rate, so this passes the signal and rejects the noise beyond it.
SHAPING_CUTOFF_RATIO = 0.6
SHAPING_TRANSITION_RATIO = 0.5


class C4fmDemod(gr.hier_block2):
	def __init__(self, sample_rate: float, symbol_rate: int = SYMBOL_RATE,
				 loop_bw: float = DEFAULT_LOOP_BW):
		super().__init__(
			'c4fm_demod',
			gr.io_signature(1, 1, gr.sizeof_gr_complex),
			gr.io_signature(1, 1, gr.sizeof_float))

		self.sample_rate = sample_rate
		self.symbol_rate = symbol_rate
		self.sps = sample_rate / symbol_rate

		# Scale so the deviations land on the symbol levels: +/-600 Hz becomes
		# +/-1 and +/-1800 Hz becomes +/-3, which is what the slicer expects.
		self.demod = analog.quadrature_demod_cf(
			sample_rate / (2 * math.pi * UNIT_DEVIATION_HZ))

		self.shaping = gr_filter.fir_filter_fff(1, firdes.low_pass(
			1.0, sample_rate,
			symbol_rate * SHAPING_CUTOFF_RATIO,
			symbol_rate * SHAPING_TRANSITION_RATIO))

		# Gardner needs no symbol decisions, which suits a 4 level signal whose
		# gain and DC offset are not yet normalised at this point in the chain.
		self.clock = digital.symbol_sync_ff(
			digital.TED_GARDNER, self.sps, loop_bw,
			1.0,      # damping
			1.0,      # TED gain
			1.5,      # maximum deviation from nominal sps
			1)        # one output sample per symbol

		self.connect(self, self.demod, self.shaping, self.clock, self)
