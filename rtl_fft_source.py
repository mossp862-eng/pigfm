import osmosdr
from gnuradio import gr
from gnuradio import blocks
from gnuradio import fft
from gnuradio.fft import window
from gnuradio import filter
from gnuradio import zeromq

class RtlFftSource(gr.top_block):
	def __init__(self, samp_rate, centre_freq, rf_gain, fft_size, iir_alpha, keep_one_in_n, zmq_address):
		gr.top_block.__init__(self, 'rtl_fft_source', catch_exceptions = True)
		
		self.rtlsdr_source = osmosdr.source()
		self.rtlsdr_source.set_sample_rate(samp_rate)
		self.rtlsdr_source.set_center_freq(centre_freq)
		self.rtlsdr_source.set_gain(rf_gain)

		self.blocks_stream_to_vector = blocks.stream_to_vector(gr.sizeof_gr_complex, fft_size)
		self.fft_vcc = fft.fft_vcc(fft_size, True, window.blackmanharris(fft_size), True, 1)
		self.blocks_complex_to_mag_squared = blocks.complex_to_mag_squared(fft_size)
		self.single_pole_iir_filter_ff = filter.single_pole_iir_filter_ff(iir_alpha, fft_size)
		self.blocks_keep_one_in_n = blocks.keep_one_in_n(gr.sizeof_float * fft_size, 128)
		self.blocks_nlog10_ff = blocks.nlog10_ff(10, fft_size, 0)
		self.zeromq_push_sink = zeromq.push_sink(gr.sizeof_float, fft_size, zmq_address, 100, False, (-1), True)

		self.connect(self.rtlsdr_source, self.blocks_stream_to_vector)
		self.connect(self.blocks_stream_to_vector, self.fft_vcc)
		self.connect(self.fft_vcc, self.blocks_complex_to_mag_squared)
		self.connect(self.blocks_complex_to_mag_squared, self.single_pole_iir_filter_ff)
		self.connect(self.single_pole_iir_filter_ff, self.blocks_keep_one_in_n)
		self.connect(self.blocks_keep_one_in_n, self.blocks_nlog10_ff)
		self.connect(self.blocks_nlog10_ff, self.zeromq_push_sink)

	def close(self):
		self.stop()
		self.wait()

def main():
	rtl_source = RtlFftSource(samp_rate = 3200000,
							  centre_freq = 166693750,
							  rf_gain = 10,
							  fft_size = 4096,
							  iir_alpha = 0.02,
							  keep_one_in_n = 128,
							  zmq_address = 'tcp://127.0.0.1:5555')

	rtl_source.start()

	try:
		input('Press Enter to quit: ')
	except EOFError:
		pass

	rtl_source.close()

if __name__ == '__main__':
	main()