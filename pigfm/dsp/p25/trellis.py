"""P25 rate 1/2 trellis code.

A four state code where the next state is simply the input dibit, and each
(state, input) pair emits one of the sixteen 4 bit symbols. The table below is a
bijection: all sixteen symbols appear exactly once across it, which is a useful
structural check that it has been transcribed correctly.

This table comes from TIA-102.BAAA. The round trip tests below encode and decode
with the same table, so they prove the Viterbi implementation but cannot prove
the table itself. Only real traffic can do that, via the TSBK CRC pass rate.
"""

import numpy as np

# Indexed by state * 4 + input dibit, giving the transmitted 4 bit symbol.
CONSTELLATION = (2, 12, 1, 15,
				 14, 0, 13, 3,
				 9, 7, 10, 4,
				 5, 11, 6, 8)

N_STATES = 4
N_INPUTS = 4

assert sorted(CONSTELLATION) == list(range(16)), 'constellation must use each symbol once'

# Hamming distance between every pair of 4 bit values, precomputed.
_POPCOUNT = np.array([bin(i).count('1') for i in range(16)], dtype = np.int16)
_DISTANCE = _POPCOUNT[np.arange(16)[:, None] ^ np.arange(16)[None, :]]

_TABLE = np.array(CONSTELLATION, dtype = np.int64).reshape(N_STATES, N_INPUTS)


def encode(dibits) -> list[int]:
	"""Dibits to 4 bit trellis symbols. One symbol per input dibit."""
	state = 0
	symbols = []

	for dibit in dibits:
		dibit = int(dibit) & 0b11
		symbols.append(int(_TABLE[state, dibit]))
		state = dibit

	return symbols


def decode(symbols) -> tuple[list[int], int]:
	"""Viterbi decode 4 bit symbols back to dibits.

	Returns the dibits and the total path metric, which is the number of symbol
	bits that had to be corrected. A large metric means the block was badly
	received and its CRC will almost certainly fail.
	"""
	symbols = [int(s) & 0xF for s in symbols]

	if not symbols:
		return [], 0

	# Start in state 0, as the encoder does.
	INFINITY = 1 << 20
	metrics = np.full(N_STATES, INFINITY, dtype = np.int64)
	metrics[0] = 0

	back = np.zeros((len(symbols), N_STATES), dtype = np.int8)

	for step, symbol in enumerate(symbols):
		# cost[state, input] for emitting this symbol from each transition.
		cost = _DISTANCE[_TABLE, symbol]
		candidates = metrics[:, None] + cost

		# Every transition with input d lands in state d, so the survivor for
		# state d is the best source state over that column.
		best_source = np.argmin(candidates, axis = 0)
		metrics = candidates[best_source, np.arange(N_INPUTS)]
		back[step] = best_source

	state = int(np.argmin(metrics))
	total = int(metrics[state])

	dibits = [0] * len(symbols)

	for step in range(len(symbols) - 1, -1, -1):
		dibits[step] = state                 # the input equals the state entered
		state = int(back[step, state])

	return dibits, total
