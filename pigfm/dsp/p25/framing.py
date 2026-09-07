"""Frame assembly: symbols in, identified data units out.

Every P25 data unit is a frame sync, then a 64 bit Network Identifier saying
what kind of unit it is and which system it belongs to, then a payload whose
shape depends on that type.
"""

from dataclasses import dataclass, field

import numpy as np

from .constants import (DUID_NAMES, NID_BITS, NID_DIBITS, FRAME_SYNC_DIBITS,
						TSBK_ENCODED_DIBITS)
from .fec import bch_decode, bch_encode, bits_to_int
from .symbols import (DEFAULT_SYNC_THRESHOLD, SymbolNormaliser, dibits_to_bits,
					  find_sync_positions, slice_dibits)

# A TSDU carries at most three TSBKs; that bounds how much a frame ever needs.
MAX_PAYLOAD_DIBITS = TSBK_ENCODED_DIBITS * 3

# Beyond this many corrected bit errors the NID is not trusted. The code can
# correct 11, but a word needing that many is usually noise landing on a valid
# codeword rather than a real frame.
MAX_NID_BIT_ERRORS = 8


def encode_nid(nac: int, duid: int) -> int:
	"""Build the 64 bit NID: a BCH(63,16) codeword plus an even parity bit."""
	codeword = bch_encode(((nac & 0xFFF) << 4) | (duid & 0xF))

	return (codeword << 1) | (bin(codeword).count('1') & 1)


def decode_nid(nid: int) -> tuple[int, int, int]:
	"""Recover (nac, duid, corrected bit errors) from a 64 bit NID."""
	data, errors = bch_decode(nid >> 1)

	return (data >> 4) & 0xFFF, data & 0xF, errors


@dataclass
class DataUnit:
	nac: int
	duid: int
	payload: np.ndarray            # dibits following the NID
	nid_bit_errors: int
	sync_correlation: float = 0.0

	@property
	def duid_name(self) -> str:
		return DUID_NAMES.get(self.duid, f'unknown(0x{self.duid:X})')

	def __str__(self) -> str:
		return (f'NAC 0x{self.nac:03X} {self.duid_name} '
				f'({len(self.payload)} dibits, {self.nid_bit_errors} NID errors)')


@dataclass
class FramerStats:
	syncs: int = 0
	units: int = 0
	nid_rejected: int = 0
	duid_counts: dict = field(default_factory = dict)

	def note(self, unit: DataUnit) -> None:
		self.units += 1
		self.duid_counts[unit.duid] = self.duid_counts.get(unit.duid, 0) + 1


class P25Framer:
	"""Streaming framer. Feed it symbol blocks, it yields complete data units."""

	def __init__(self, sync_threshold: float = DEFAULT_SYNC_THRESHOLD,
				 max_nid_bit_errors: int = MAX_NID_BIT_ERRORS,
				 normalise: bool = True):
		self.sync_threshold = sync_threshold
		self.max_nid_bit_errors = max_nid_bit_errors
		self.normaliser = SymbolNormaliser() if normalise else None
		self.stats = FramerStats()

		self._buffer = np.empty(0, dtype = np.float32)

	# A unit can be emitted once its sync and NID are present. The payload runs
	# to the next sync, or to the maximum a TSDU can hold if no next sync is
	# known yet.
	_HEADER_DIBITS = FRAME_SYNC_DIBITS + NID_DIBITS

	# Cap on retained symbols, so a stream that never syncs cannot grow without
	# bound.
	_MAX_BUFFER = (FRAME_SYNC_DIBITS + NID_DIBITS + MAX_PAYLOAD_DIBITS) * 4

	def feed(self, symbols: np.ndarray) -> list[DataUnit]:
		"""Consume a block of symbols and return whatever units completed."""
		if self.normaliser is not None:
			symbols = self.normaliser.normalise(np.asarray(symbols, dtype = np.float32))

		self._buffer = np.concatenate([self._buffer, np.asarray(symbols, dtype = np.float32)])

		units = self._drain(flush = False)

		if self._buffer.size > self._MAX_BUFFER:
			self._buffer = self._buffer[-self._MAX_BUFFER:]

		return units

	def flush(self) -> list[DataUnit]:
		"""Emit any unit still pending at the end of a stream.

		Without this the final unit is held forever waiting for a payload that
		will never arrive, which costs one unit on every finite capture.
		"""
		return self._drain(flush = True)

	def _drain(self, flush: bool) -> list[DataUnit]:
		units = []

		while True:
			positions = find_sync_positions(self._buffer, self.sync_threshold)

			if positions.size == 0:
				break

			position = int(positions[0])
			body_start = position + self._HEADER_DIBITS

			if body_start > self._buffer.size:
				break                                    # not even a full NID yet

			limit = body_start + MAX_PAYLOAD_DIBITS

			if positions.size > 1:
				payload_end = min(int(positions[1]), limit)
			elif limit <= self._buffer.size or flush:
				payload_end = min(limit, self._buffer.size)
			else:
				break                                    # wait for the rest

			self.stats.syncs += 1
			unit = self._decode_at(position, payload_end)

			if unit is None:
				self.stats.nid_rejected += 1
			else:
				units.append(unit)
				self.stats.note(unit)

			# Never advance by zero, or a rejected sync at offset 0 would loop.
			self._buffer = self._buffer[max(payload_end, position + 1):]

		return units

	def _decode_at(self, position: int, payload_end: int) -> DataUnit | None:
		nid_start = position + FRAME_SYNC_DIBITS
		nid_dibits = slice_dibits(self._buffer[nid_start: nid_start + NID_DIBITS])
		nid = bits_to_int(dibits_to_bits(nid_dibits))

		nac, duid, errors = decode_nid(nid)

		if errors > self.max_nid_bit_errors:
			return None

		payload = slice_dibits(self._buffer[nid_start + NID_DIBITS: payload_end])

		return DataUnit(nac = nac, duid = duid, payload = payload,
						nid_bit_errors = errors)
