"""End to end loopback: TSBK -> C4FM -> the real GNURadio demodulator -> IDs.

This is the strongest evidence the decoder works. It builds the signal a P25
transmitter would radiate, pushes it through the same demodulation block the
receiver uses, and requires the exact talkgroup and radio IDs back out.

Slower than the rest of the suite because it runs a GNURadio flowgraph.
"""

import numpy as np
from gnuradio import blocks, gr

from pigfm.dsp.p25.constants import (DUID_TSDU, FRAME_SYNC_DIBIT_SEQUENCE, NID_BITS,
									 TSBK_GRP_VCH_GRANT)
from pigfm.dsp.p25.demod import C4fmDemod
from pigfm.dsp.p25.fec import int_to_bits
from pigfm.dsp.p25.framing import P25Framer, encode_nid
from pigfm.dsp.p25.symbols import bits_to_dibits
from pigfm.dsp.p25.tsbk import decode_tsbk, encode_tsbk

from .p25_tx import add_noise, dibits_to_symbols, modulate_c4fm

SAMPLE_RATE = 50_000
NAC = 0x293
CALLS = [(1234, 5551234), (99, 12345), (65535, 16777215)]

# Alternating outer symbols, so the symbol clock has something to lock to before
# the first frame arrives, as a real preamble provides.
PREAMBLE = [0b01, 0b11] * 40


def _build_tsdu(nac, calls):
	nid = encode_nid(nac, DUID_TSDU)
	dibits = list(FRAME_SYNC_DIBIT_SEQUENCE)
	dibits += list(bits_to_dibits(np.array(int_to_bits(nid, NID_BITS), dtype = np.uint8)))

	for i, (talkgroup, radio) in enumerate(calls):
		arguments = (0x100A << 40) | (talkgroup << 24) | radio
		dibits += list(encode_tsbk(TSBK_GRP_VCH_GRANT, 0, arguments,
								   last_block = i == len(calls) - 1))

	return dibits


def _demodulate(iq: np.ndarray) -> np.ndarray:
	flowgraph = gr.top_block()
	source = blocks.vector_source_c(iq.tolist(), False)
	sink = blocks.vector_sink_f()

	flowgraph.connect(source, C4fmDemod(SAMPLE_RATE), sink)
	flowgraph.run()

	return np.array(sink.data(), dtype = np.float32)


def _identities(symbols) -> list[tuple[int, int]]:
	framer = P25Framer()
	found = []

	for unit in framer.feed(symbols) + framer.flush():
		if unit.nac != NAC or unit.duid != DUID_TSDU:
			continue

		for start in range(0, len(unit.payload) - 97, 98):
			tsbk = decode_tsbk(unit.payload[start: start + 98])

			if tsbk is not None and tsbk.crc_ok and tsbk.talkgroup is not None:
				found.append((tsbk.talkgroup, tsbk.radio_id))

	return found


def _transmit(repeats: int = 3) -> np.ndarray:
	dibits = list(PREAMBLE)

	for _ in range(repeats):
		dibits += _build_tsdu(NAC, CALLS)

	return modulate_c4fm(dibits_to_symbols(dibits), SAMPLE_RATE)


def test_identities_survive_the_full_chain():
	found = _identities(_demodulate(_transmit()))

	assert found, 'no TSBKs recovered from a clean signal'

	for call in CALLS:
		assert call in found, f'{call} not recovered'


def test_identities_survive_noise():
	"""Down to the SNR where the front end still holds sync."""
	rng = np.random.default_rng(17)
	iq = _transmit()

	for snr_db in (20, 12, 8):
		found = _identities(_demodulate(add_noise(iq, snr_db, rng)))

		assert CALLS[0] in found, f'lost the first call at {snr_db} dB SNR'
