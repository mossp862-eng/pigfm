"""Trunking Signalling Blocks: the control channel messages that name identities.

A TSBK is 96 bits, of which the last 16 are a CRC. The CRC is what makes this
decoder trustworthy: it is a 16 bit check over the block, so a wrong trellis
table, a wrong interleaver or simply a badly received block essentially never
produces a passing CRC. A stream of TSBKs with passing CRCs is proof the whole
chain is right.
"""

from dataclasses import dataclass

import numpy as np

from . import trellis
from .constants import (TSBK_BITS, TSBK_CRC_BITS, TSBK_ENCODED_DIBITS,
						TSBK_OPCODE_NAMES, TSBK_PAYLOAD_BITS,
						TSBK_GRP_VCH_GRANT, TSBK_GRP_VCH_GRANT_UPDATE,
						TSBK_UU_ANS_REQ, TSBK_UU_VCH_GRANT)
from .fec import bits_to_int, crc16_ccitt, deinterleave_symbols, interleave_symbols
from .symbols import bits_to_dibits, dibits_to_bits

# One flush dibit follows the 48 data dibits so the encoder ends in state 0.
TSBK_DIBITS = TSBK_BITS // 2
FLUSH_DIBITS = 1


@dataclass
class Tsbk:
	last_block: bool
	protected: bool
	opcode: int
	mfid: int
	arguments: int              # the 64 argument bits
	crc_ok: bool
	path_metric: int

	talkgroup: int | None = None
	radio_id: int | None = None
	target_id: int | None = None
	channel: int | None = None

	@property
	def opcode_name(self) -> str:
		return TSBK_OPCODE_NAMES.get(self.opcode, f'opcode 0x{self.opcode:02X}')

	def __str__(self) -> str:
		parts = [self.opcode_name]

		if self.talkgroup is not None:
			parts.append(f'talkgroup {self.talkgroup}')
		if self.radio_id is not None:
			parts.append(f'radio {self.radio_id}')
		if self.target_id is not None:
			parts.append(f'target {self.target_id}')
		if self.channel is not None:
			parts.append(f'channel 0x{self.channel:04X}')

		return ', '.join(parts)


def _parse_arguments(tsbk: Tsbk) -> None:
	"""Pull identities out of the 64 argument bits, per opcode."""
	args = tsbk.arguments

	if tsbk.opcode in (TSBK_GRP_VCH_GRANT,):
		# options(8) channel(16) group(16) source(24)
		tsbk.channel = (args >> 40) & 0xFFFF
		tsbk.talkgroup = (args >> 24) & 0xFFFF
		tsbk.radio_id = args & 0xFFFFFF

	elif tsbk.opcode == TSBK_GRP_VCH_GRANT_UPDATE:
		# channel(16) group(16) channel(16) group(16); no source address
		tsbk.channel = (args >> 48) & 0xFFFF
		tsbk.talkgroup = (args >> 32) & 0xFFFF

	elif tsbk.opcode in (TSBK_UU_VCH_GRANT, TSBK_UU_ANS_REQ):
		# channel(16) target(24) source(24)
		tsbk.channel = (args >> 48) & 0xFFFF
		tsbk.target_id = (args >> 24) & 0xFFFFFF
		tsbk.radio_id = args & 0xFFFFFF


def decode_tsbk(dibits) -> Tsbk | None:
	"""Decode one 98 dibit trellis coded block into a TSBK.

	Returns None if there are not enough dibits. A block that decodes but fails
	its CRC is still returned, with crc_ok False, because the pass rate over
	many blocks is the diagnostic that matters.
	"""
	dibits = np.asarray(dibits, dtype = np.uint8)

	if dibits.size < TSBK_ENCODED_DIBITS:
		return None

	pairs = dibits[: TSBK_ENCODED_DIBITS].reshape(-1, 2)
	symbols = (pairs[:, 0].astype(np.int64) << 2) | pairs[:, 1]

	decoded, metric = trellis.decode(deinterleave_symbols(symbols))

	bits = dibits_to_bits(np.array(decoded[: TSBK_DIBITS], dtype = np.uint8))

	payload = bits[: TSBK_PAYLOAD_BITS]
	received_crc = bits_to_int(bits[TSBK_PAYLOAD_BITS: TSBK_BITS])

	tsbk = Tsbk(
		last_block = bool(bits[0]),
		protected = bool(bits[1]),
		opcode = bits_to_int(bits[2: 8]),
		mfid = bits_to_int(bits[8: 16]),
		arguments = bits_to_int(bits[16: TSBK_PAYLOAD_BITS]),
		crc_ok = crc16_ccitt(payload) == received_crc,
		path_metric = metric)

	_parse_arguments(tsbk)

	return tsbk


def encode_tsbk(opcode: int, mfid: int, arguments: int, last_block: bool = True,
				protected: bool = False) -> np.ndarray:
	"""Build the 98 dibits a transmitter would send. Used by the tests."""
	from .fec import int_to_bits

	header = [int(last_block), int(protected)] + int_to_bits(opcode, 6) + int_to_bits(mfid, 8)
	payload = header + int_to_bits(arguments, TSBK_PAYLOAD_BITS - 16)
	bits = payload + int_to_bits(crc16_ccitt(payload), TSBK_CRC_BITS)

	dibits = list(bits_to_dibits(np.array(bits, dtype = np.uint8))) + [0] * FLUSH_DIBITS
	symbols = interleave_symbols(trellis.encode(dibits))

	out = np.empty(TSBK_ENCODED_DIBITS, dtype = np.uint8)
	out[0::2] = (np.asarray(symbols) >> 2) & 0b11
	out[1::2] = np.asarray(symbols) & 0b11

	return out
