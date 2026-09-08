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

# Direction. The wire format is identical both ways, so nothing in a frame says
# which it is: the frequency does. Opcode 0x2C is a registration *response* from
# the base station and a registration *request* from a radio, and reading one as
# the other pulls the identity out of the wrong bits.
OUTBOUND = 'outbound'      # base station to radio, heard on the downlink
INBOUND = 'inbound'        # radio to base station, heard on the uplink

# Outbound (OSP) opcodes that name a talkgroup and a radio.
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


# Inbound (ISP) opcodes: what a radio transmits. These are the ones that matter
# for noticing which radios are nearby, because a radio has to announce itself
# to register, affiliate or ask for a channel.
ISP_GRP_V_REQ = 0x00           # group voice service request
ISP_UU_V_REQ = 0x04            # unit to unit voice service request
ISP_UU_ANS_RSP = 0x05          # unit to unit answer response
ISP_CALL_ALRT_REQ = 0x1E       # call alert request
ISP_ACK_RSP_U = 0x1F           # acknowledge response
ISP_CAN_SRV_REQ = 0x20         # cancel service request
ISP_EMRG_ALRM_REQ = 0x23       # emergency alarm
ISP_GRP_AFF_REQ = 0x24         # group affiliation request
ISP_U_DEREG_REQ = 0x28         # unit de-registration request
ISP_U_REG_REQ = 0x2C           # unit registration request
ISP_LOC_REG_REQ = 0x2D         # location registration request

ISP_OPCODE_NAMES = {
	ISP_GRP_V_REQ: 'Group voice request',
	ISP_UU_V_REQ: 'Unit to unit voice request',
	ISP_UU_ANS_RSP: 'Unit to unit answer',
	ISP_CALL_ALRT_REQ: 'Call alert request',
	ISP_ACK_RSP_U: 'Acknowledge',
	ISP_CAN_SRV_REQ: 'Cancel service request',
	ISP_EMRG_ALRM_REQ: 'EMERGENCY ALARM',
	ISP_GRP_AFF_REQ: 'Group affiliation request',
	ISP_U_DEREG_REQ: 'Unit de-registration',
	ISP_U_REG_REQ: 'Unit registration request',
	ISP_LOC_REG_REQ: 'Location registration request',
}

# Inbound messages that also carry the talkgroup the radio is working with, in
# the 16 bits immediately above the source address.
ISP_CARRIES_GROUP = (ISP_GRP_V_REQ, ISP_EMRG_ALRM_REQ, ISP_GRP_AFF_REQ,
					 ISP_LOC_REG_REQ)

# Inbound messages addressed at another radio rather than a talkgroup.
ISP_CARRIES_TARGET = (ISP_UU_V_REQ, ISP_UU_ANS_RSP, ISP_CALL_ALRT_REQ,
					  ISP_ACK_RSP_U, ISP_CAN_SRV_REQ)

# A radio announcing itself unprompted. These are the ones worth alerting on:
# the radio is nearby and has just told the network so.
ISP_ANNOUNCES_PRESENCE = (ISP_U_REG_REQ, ISP_LOC_REG_REQ, ISP_GRP_AFF_REQ,
						  ISP_EMRG_ALRM_REQ)
