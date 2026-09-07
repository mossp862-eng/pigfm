"""P25 decoding: FEC primitives, trellis, TSBK and framing.

The round trip tests prove the algorithms. They cannot prove the two tables
taken from TIA-102 (the trellis constellation and the interleaver stride),
because they encode and decode with the same table; only live traffic with
passing CRCs can do that. See tasks/todo.md.
"""

import numpy as np

from pigfm.dsp.p25 import trellis
from pigfm.dsp.p25.constants import (DIBIT_TO_SYMBOL, DUID_TSDU, FRAME_SYNC,
									 FRAME_SYNC_DIBIT_SEQUENCE, FRAME_SYNC_SYMBOLS,
									 NID_BITS, TSBK_GRP_VCH_GRANT, TSBK_UU_VCH_GRANT)
from pigfm.dsp.p25.fec import (BCH_GENERATOR, BCH_K, BCH_N, bch_decode, bch_encode,
							   crc16_ccitt, deinterleave_symbols, int_to_bits,
							   interleave_symbols)
from pigfm.dsp.p25.framing import P25Framer, decode_nid, encode_nid
from pigfm.dsp.p25.symbols import (bits_to_dibits, c4fm_quality, find_sync_positions,
									slice_dibits, sync_correlation)
from pigfm.dsp.p25.tsbk import decode_tsbk, encode_tsbk

SEED = 1234


# --- constants ---------------------------------------------------------------

def test_frame_sync_decomposes_to_its_dibits():
	value = 0

	for dibit in FRAME_SYNC_DIBIT_SEQUENCE:
		value = (value << 2) | dibit

	assert value == FRAME_SYNC
	assert len(FRAME_SYNC_DIBIT_SEQUENCE) == 24


def test_frame_sync_is_all_outer_symbols():
	"""What makes correlation reliable: the sync never uses the +/-1 levels."""
	assert all(abs(s) == 3 for s in FRAME_SYNC_SYMBOLS)


# --- FEC ---------------------------------------------------------------------

def test_bch_generator_has_the_right_degree():
	"""Derived from the field rather than hardcoded, so this is the check that
	the derivation is right."""
	assert BCH_GENERATOR.bit_length() - 1 == BCH_N - BCH_K == 47


def test_bch_corrects_up_to_eleven_errors():
	rng = np.random.default_rng(SEED)

	for _ in range(100):
		data = int(rng.integers(0, 1 << 16))
		corrupted = bch_encode(data)

		for position in rng.choice(BCH_N, 11, replace = False):
			corrupted ^= 1 << int(position)

		recovered, errors = bch_decode(corrupted)

		assert recovered == data
		assert errors == 11


def test_bch_degrades_beyond_its_limit():
	"""A code that appeared to correct anything would mean the test is wrong."""
	rng = np.random.default_rng(SEED)
	failures = 0

	for _ in range(100):
		data = int(rng.integers(0, 1 << 16))
		corrupted = bch_encode(data)

		for position in rng.choice(BCH_N, 20, replace = False):
			corrupted ^= 1 << int(position)

		failures += bch_decode(corrupted)[0] != data

	assert failures > 50


def test_crc16_detects_single_bit_errors():
	rng = np.random.default_rng(SEED)
	payload = list(rng.integers(0, 2, 80))
	expected = crc16_ccitt(payload)

	assert crc16_ccitt(payload) == expected          # deterministic

	for position in range(0, 80, 7):
		flipped = list(payload)
		flipped[position] ^= 1

		assert crc16_ccitt(flipped) != expected


def test_interleaver_is_a_permutation_and_reversible():
	symbols = np.arange(49)

	assert sorted(interleave_symbols(symbols).tolist()) == list(range(49))
	assert deinterleave_symbols(interleave_symbols(symbols)).tolist() == list(range(49))


# --- trellis -----------------------------------------------------------------

def test_trellis_constellation_uses_every_symbol_once():
	"""Structural check on the table transcription: it must be a bijection."""
	assert sorted(trellis.CONSTELLATION) == list(range(16))


def test_trellis_round_trip_is_exact():
	rng = np.random.default_rng(SEED)

	for _ in range(200):
		dibits = [int(d) for d in rng.integers(0, 4, 49)]
		decoded, metric = trellis.decode(trellis.encode(dibits))

		assert decoded == dibits
		assert metric == 0


def test_trellis_recovers_from_corrupted_symbols():
	rng = np.random.default_rng(SEED)
	recovered = 0

	for _ in range(100):
		dibits = [int(d) for d in rng.integers(0, 4, 49)]
		symbols = trellis.encode(dibits)

		for position in rng.choice(49, 3, replace = False):
			symbols[int(position)] ^= 1 << int(rng.integers(0, 4))

		recovered += trellis.decode(symbols)[0] == dibits

	assert recovered >= 90


# --- NID ---------------------------------------------------------------------

def test_nid_round_trip():
	for nac, duid in ((0x293, DUID_TSDU), (0xFFF, 0x5), (0x000, 0x0), (0x4D2, 0xA)):
		assert decode_nid(encode_nid(nac, duid))[:2] == (nac, duid)


def test_nid_survives_bit_errors():
	rng = np.random.default_rng(SEED)

	for _ in range(100):
		nac = int(rng.integers(0, 1 << 12))
		duid = DUID_TSDU
		nid = encode_nid(nac, duid)

		for position in rng.choice(np.arange(1, 64), 9, replace = False):
			nid ^= 1 << int(position)

		assert decode_nid(nid)[:2] == (nac, duid)


# --- TSBK --------------------------------------------------------------------

def test_group_voice_grant_carries_talkgroup_and_radio():
	arguments = (0x100A << 40) | (1234 << 24) | 5551234
	tsbk = decode_tsbk(encode_tsbk(TSBK_GRP_VCH_GRANT, 0, arguments))

	assert tsbk.crc_ok
	assert tsbk.talkgroup == 1234
	assert tsbk.radio_id == 5551234
	assert tsbk.channel == 0x100A
	assert tsbk.last_block


def test_unit_to_unit_grant_carries_both_parties():
	arguments = (0x100A << 48) | (777777 << 24) | 888888

	tsbk = decode_tsbk(encode_tsbk(TSBK_UU_VCH_GRANT, 0, arguments))

	assert tsbk.crc_ok
	assert tsbk.target_id == 777777
	assert tsbk.radio_id == 888888


def test_heavily_corrupted_tsbk_fails_its_crc():
	"""Light corruption is corrected by the trellis, which is the point of it.
	This destroys the block well past that, and the CRC must then reject."""
	rng = np.random.default_rng(SEED)
	arguments = (0x100A << 40) | (1234 << 24) | 5551234

	rejected = 0

	for _ in range(20):
		dibits = encode_tsbk(TSBK_GRP_VCH_GRANT, 0, arguments)

		for position in rng.choice(98, 40, replace = False):
			dibits[int(position)] = int(rng.integers(0, 4))

		rejected += decode_tsbk(dibits).crc_ok is False

	assert rejected >= 19


def test_random_blocks_essentially_never_pass_crc():
	"""The false positive rate is what makes a live CRC pass rate meaningful.

	A 16 bit CRC should let roughly one random block in 65536 through, so a
	stream of passing CRCs off the air cannot be noise, and confirms the trellis
	table and interleaver are right.
	"""
	rng = np.random.default_rng(SEED)
	passed = 0

	for _ in range(500):
		passed += decode_tsbk(rng.integers(0, 4, 98).astype(np.uint8)).crc_ok

	assert passed == 0


def test_tsbk_needs_a_full_block():
	assert decode_tsbk(np.zeros(50, dtype = np.uint8)) is None


# --- framing -----------------------------------------------------------------

def _symbols_for(dibits) -> np.ndarray:
	return np.array([DIBIT_TO_SYMBOL[int(d)] for d in dibits], dtype = np.float32)


def _build_tsdu(nac, calls):
	nid = encode_nid(nac, DUID_TSDU)
	dibits = list(FRAME_SYNC_DIBIT_SEQUENCE)
	dibits += list(bits_to_dibits(np.array(int_to_bits(nid, NID_BITS), dtype = np.uint8)))

	for i, (talkgroup, radio) in enumerate(calls):
		arguments = (0x100A << 40) | (talkgroup << 24) | radio
		dibits += list(encode_tsbk(TSBK_GRP_VCH_GRANT, 0, arguments,
								   last_block = i == len(calls) - 1))

	return dibits


def test_framer_recovers_identities_from_a_symbol_stream():
	calls = [(1234, 5551234), (99, 12345), (65535, 16777215)]

	dibits = []

	for _ in range(3):
		dibits += _build_tsdu(0x293, calls)

	framer = P25Framer(normalise = False)
	units = framer.feed(_symbols_for(dibits)) + framer.flush()

	assert len(units) == 3
	assert all(u.nac == 0x293 and u.duid == DUID_TSDU for u in units)

	recovered = []

	for unit in units:
		for start in range(0, len(unit.payload) - 97, 98):
			tsbk = decode_tsbk(unit.payload[start: start + 98])

			if tsbk is not None and tsbk.crc_ok:
				recovered.append((tsbk.talkgroup, tsbk.radio_id))

	for call in calls:
		assert call in recovered


def test_framer_is_indifferent_to_block_boundaries():
	"""ZMQ delivers arbitrary chunks, so framing must not depend on them."""
	dibits = []

	for _ in range(4):
		dibits += _build_tsdu(0x111, [(7, 1)])

	symbols = _symbols_for(dibits)

	framer = P25Framer(normalise = False)
	units = []

	for start in range(0, symbols.size, 37):
		units += framer.feed(symbols[start: start + 37])

	units += framer.flush()

	assert len(units) == 4
	assert all(u.nac == 0x111 for u in units)


def test_framer_rejects_noise():
	rng = np.random.default_rng(SEED)
	framer = P25Framer(normalise = False)

	units = framer.feed(rng.normal(0, 1.5, 20000).astype(np.float32)) + framer.flush()

	assert len(units) == 0


# --- signal quality ----------------------------------------------------------

def test_c4fm_quality_separates_signal_from_noise():
	"""The measurement that says whether a channel is P25 at all, rather than
	just strong."""
	rng = np.random.default_rng(SEED)

	dibits = _build_tsdu(0x293, [(1234, 5551234)]) * 4
	outer, correlation = c4fm_quality(_symbols_for(dibits))

	assert outer > 0.40
	assert correlation > 0.95

	noise_outer, noise_correlation = c4fm_quality(
		rng.normal(0, 1.0, 20000).astype(np.float32))

	assert noise_outer < 0.40
	assert noise_correlation < 0.75


def test_sync_correlation_peaks_only_on_the_real_sync():
	rng = np.random.default_rng(SEED)

	data = rng.choice([-3.0, -1.0, 1.0, 3.0], 300).astype(np.float32)
	stream = np.concatenate([data[:100], _symbols_for(FRAME_SYNC_DIBIT_SEQUENCE), data[100:]])

	assert find_sync_positions(stream).tolist() == [100]
	assert abs(float(sync_correlation(stream).max()) - 1.0) < 1e-6


def test_slicer_maps_levels_to_dibits():
	symbols = np.array([3.0, 1.0, -1.0, -3.0], dtype = np.float32)

	assert slice_dibits(symbols).tolist() == [0b01, 0b00, 0b10, 0b11]
