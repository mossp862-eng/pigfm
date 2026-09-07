"""P25 Phase 1 (TIA-102) constants.

Every number here is checkable against the standard. The frame sync being
entirely +/-3 symbols is load bearing: it sits at maximum distance from the
+/-1 data symbols, which is what makes correlation robust at low SNR.
"""

# C4FM
SYMBOL_RATE = 4800
BIT_RATE = SYMBOL_RATE * 2
UNIT_DEVIATION_HZ = 600          # +/-600 Hz is +/-1, +/-1800 Hz is +/-3

# Dibit to symbol level. Note this is not binary order: 01 is the *most*
# positive symbol.
DIBIT_TO_SYMBOL = {0b01: 3, 0b00: 1, 0b10: -1, 0b11: -3}
SYMBOL_TO_DIBIT = {v: k for k, v in DIBIT_TO_SYMBOL.items()}

# Frame sync, 48 bits / 24 dibits, prefixed to every data unit.
FRAME_SYNC = 0x5575F5FF77FF
FRAME_SYNC_BITS = 48
FRAME_SYNC_DIBITS = FRAME_SYNC_BITS // 2

FRAME_SYNC_DIBIT_SEQUENCE = tuple(
	(FRAME_SYNC >> shift) & 0b11
	for shift in range(FRAME_SYNC_BITS - 2, -2, -2))

FRAME_SYNC_SYMBOLS = tuple(DIBIT_TO_SYMBOL[d] for d in FRAME_SYNC_DIBIT_SEQUENCE)

# Network Identifier: 64 bits following the sync. A BCH(63,16) codeword whose
# 16 data bits are NAC(12) then DUID(4), plus one trailing parity bit.
NID_BITS = 64
NID_DIBITS = NID_BITS // 2
NAC_BITS = 12
DUID_BITS = 4

# Data unit types.
DUID_HDU = 0x0
DUID_TDU = 0x3
DUID_LDU1 = 0x5
DUID_TSDU = 0x7
DUID_LDU2 = 0xA
DUID_PDU = 0xC
DUID_TDULC = 0xF

DUID_NAMES = {
	DUID_HDU: 'HDU',
	DUID_TDU: 'TDU',
	DUID_LDU1: 'LDU1',
	DUID_TSDU: 'TSDU',
	DUID_LDU2: 'LDU2',
	DUID_PDU: 'PDU',
	DUID_TDULC: 'TDULC',
}

# Which data units carry identities. TSDU is the control channel's trunking
# traffic; LDU1 carries link control during a voice call.
DUID_CARRIES_IDS = (DUID_TSDU, DUID_LDU1)

# A TSBK is 96 bits: 80 of payload then a 16 bit CRC. Trellis encoded 1/2 rate
# with one flush dibit, it occupies 196 bits on air.
TSBK_BITS = 96
TSBK_PAYLOAD_BITS = 80
TSBK_CRC_BITS = 16
TSBK_ENCODED_BITS = 196
TSBK_ENCODED_DIBITS = TSBK_ENCODED_BITS // 2

# TSBK opcodes that name a talkgroup and a radio.
TSBK_GRP_VCH_GRANT = 0x00
TSBK_GRP_VCH_GRANT_UPDATE = 0x02
TSBK_UU_VCH_GRANT = 0x04
TSBK_UU_ANS_REQ = 0x05
TSBK_GRP_AFF_RSP = 0x28
TSBK_UNIT_REG_RSP = 0x2C

TSBK_OPCODE_NAMES = {
	TSBK_GRP_VCH_GRANT: 'Group voice grant',
	TSBK_GRP_VCH_GRANT_UPDATE: 'Group voice grant update',
	TSBK_UU_VCH_GRANT: 'Unit to unit voice grant',
	TSBK_UU_ANS_REQ: 'Unit to unit answer request',
	TSBK_GRP_AFF_RSP: 'Group affiliation response',
	TSBK_UNIT_REG_RSP: 'Unit registration response',
}
