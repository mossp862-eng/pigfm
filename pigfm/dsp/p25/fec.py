"""Forward error correction primitives for P25.

The BCH generator is derived from the field rather than hardcoded, so it checks
itself: if the derivation were wrong the polynomial would not come out at degree
47, and construction asserts exactly that.
"""

import numpy as np

# GF(2^6) with primitive polynomial x^6 + x + 1, the field BCH(63,16) lives in.
GF_POLY = 0b1000011
GF_M = 6
BCH_N = 63
BCH_K = 16
BCH_DESIGNED_DISTANCE = 23        # t = 11 correctable errors

# CRC-16/CCITT as P25 uses it for TSBK: x^16 + x^12 + x^5 + 1, all ones in,
# complemented out.
CRC16_POLY = 0x1021
CRC16_INIT = 0xFFFF
CRC16_XOROUT = 0xFFFF


def bits_to_int(bits) -> int:
	value = 0

	for bit in bits:
		value = (value << 1) | int(bit)

	return value


def int_to_bits(value: int, width: int) -> list[int]:
	return [(value >> shift) & 1 for shift in range(width - 1, -1, -1)]


def crc16_ccitt(bits) -> int:
	"""CRC over a sequence of bits, most significant first."""
	crc = CRC16_INIT

	for bit in bits:
		feedback = ((crc >> 15) & 1) ^ (int(bit) & 1)
		crc = (crc << 1) & 0xFFFF

		if feedback:
			crc ^= CRC16_POLY

	return crc ^ CRC16_XOROUT


# --- BCH(63,16) for the Network Identifier -----------------------------------

def _gf_tables():
	alog = [0] * BCH_N
	log = [0] * (1 << GF_M)
	x = 1

	for i in range(BCH_N):
		alog[i] = x
		log[x] = i
		x <<= 1

		if x & (1 << GF_M):
			x ^= GF_POLY

	return alog, log


_ALOG, _LOG = _gf_tables()


def _gf_mul(a: int, b: int) -> int:
	if a == 0 or b == 0:
		return 0

	return _ALOG[(_LOG[a] + _LOG[b]) % BCH_N]


def _minimal_poly(exponent: int) -> int:
	"""Minimal polynomial over GF(2) of a^exponent, via its cyclotomic coset."""
	coset = []
	c = exponent % BCH_N

	while c not in coset:
		coset.append(c)
		c = (c * 2) % BCH_N

	poly = [1]

	for c in coset:
		root = _ALOG[c]
		product = [0] * (len(poly) + 1)

		for i, coeff in enumerate(poly):
			product[i] ^= _gf_mul(coeff, root)
			product[i + 1] ^= coeff

		poly = product

	assert all(c in (0, 1) for c in poly), 'minimal polynomial must be over GF(2)'

	return sum(c << i for i, c in enumerate(poly))


def _poly_mul(a: int, b: int) -> int:
	result = 0

	for i in range(a.bit_length()):
		if (a >> i) & 1:
			result ^= b << i

	return result


def _bch_generator() -> int:
	"""LCM of the minimal polynomials of a^1 .. a^22."""
	generator = 1
	seen = set()

	for exponent in range(1, BCH_DESIGNED_DISTANCE):
		poly = _minimal_poly(exponent)

		if poly in seen:
			continue

		seen.add(poly)
		generator = _poly_mul(generator, poly)

	degree = generator.bit_length() - 1

	assert degree == BCH_N - BCH_K, f'BCH generator degree {degree}, expected {BCH_N - BCH_K}'

	return generator


BCH_GENERATOR = _bch_generator()
_PARITY_BITS = BCH_N - BCH_K


def bch_encode(data: int) -> int:
	"""Systematic BCH(63,16): 16 data bits followed by 47 parity bits."""
	shifted = (data & 0xFFFF) << _PARITY_BITS
	remainder = shifted

	while remainder.bit_length() > _PARITY_BITS:
		remainder ^= BCH_GENERATOR << (remainder.bit_length() - BCH_GENERATOR.bit_length())

	return shifted | remainder


def _build_codebook() -> np.ndarray:
	"""All 65536 valid codewords, as uint64. A 63 bit codeword fits one word,
	which is what makes exhaustive decoding a single vectorised operation."""
	basis = np.array([bch_encode(1 << i) for i in range(BCH_K)], dtype = np.uint64)
	index = np.arange(1 << BCH_K, dtype = np.uint32)
	codebook = np.zeros(1 << BCH_K, dtype = np.uint64)

	for i in range(BCH_K):
		selected = ((index >> i) & 1).astype(bool)
		codebook[selected] ^= basis[i]

	return codebook


_CODEBOOK = None


def bch_decode(received: int) -> tuple[int, int]:
	"""Maximum likelihood decode of a 63 bit word. Returns (data, error count).

	Exhaustive over all 65536 codewords rather than a Berlekamp-Massey syndrome
	decoder. The codebook is 512 kB and the search is one XOR and one popcount
	over it, so this is both faster to run and far harder to get wrong than the
	algebraic version, and it is optimal by construction.
	"""
	global _CODEBOOK

	if _CODEBOOK is None:
		_CODEBOOK = _build_codebook()

	distances = np.bitwise_count(_CODEBOOK ^ np.uint64(received & ((1 << BCH_N) - 1)))
	best = int(np.argmin(distances))

	return best, int(distances[best])


# --- Block interleaver for trellis coded blocks -------------------------------

# A trellis coded block is 196 bits, which is 49 four bit symbols, one per input
# dibit. Interleaving is a stride across those symbols, which spreads a burst of
# adjacent bit errors across the trellis so the Viterbi decoder sees them as
# isolated.
#
# 13 and 49 are coprime, so the stride visits every position exactly once. This
# stride is the second of the two TIA-102 tables that the loopback tests cannot
# validate on their own, since they interleave and deinterleave with the same
# value; live CRC pass rate is what confirms it.
INTERLEAVE_SYMBOLS = 49
INTERLEAVE_STRIDE = 13

_INTERLEAVE = np.array([(i * INTERLEAVE_STRIDE) % INTERLEAVE_SYMBOLS
						for i in range(INTERLEAVE_SYMBOLS)], dtype = np.int64)
_DEINTERLEAVE = np.argsort(_INTERLEAVE)

assert len(set(_INTERLEAVE.tolist())) == INTERLEAVE_SYMBOLS, 'stride must be a permutation'


def interleave_symbols(symbols) -> np.ndarray:
	return np.asarray(symbols)[_DEINTERLEAVE]


def deinterleave_symbols(symbols) -> np.ndarray:
	return np.asarray(symbols)[_INTERLEAVE]
