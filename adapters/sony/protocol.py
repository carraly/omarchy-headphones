"""Sony MDR wire protocol. Transport and process ownership live in Omaphones.

Ported without changing the observed layouts or model query policy. See
PROTOCOL.md and tests/pins/sony; captures remain at their original paths.
"""
import struct
from omaphones.api import Protocol

HDR, TRL, ESC, MASK = 0x3E, 0x3C, 0x3D, 0xEF


DATA_MDR, DATA_MDR_NO2, ACK = 0x0C, 0x0E, 0x01


CONNECT_RET_PROTOCOL_INFO = 0x01


NCASM_GET, NCASM_RET, NCASM_SET, NCASM_NTFY = 0x66, 0x67, 0x68, 0x69


SYSTEM_GET_STATUS, SYSTEM_RET_STATUS, SYSTEM_NTFY_STATUS = 0xF2, 0xF3, 0xF5


WEARING_STATUS_TYPE = 0x10


CANDIDATES_V2 = (0x17, 0x15, 0x22)


CANDIDATES_V1 = (0x02,)


CANDIDATES = CANDIDATES_V2 + CANDIDATES_V1


AVAILABLE = {
    0x17: ["off", "anc", "ambient"],
    0x15: ["off", "anc", "ambient"],
    0x22: ["off", "ambient"],
    0x19: ["off", "anc", "ambient"],
    0x02: ["off", "anc", "ambient"],
}


NC_VALUE_DEFAULT = 0x02


MAX_LEVEL = 20


EXIT_TRANSIENT = 1


EXIT_UNSUPPORTED = 3


INIT_TIMEOUT = 2000


INIT_ATTEMPTS = 3


CANDIDATE_TIMEOUT = 3000


ACK_TIMEOUT = 3000


def escape(data):
    out = bytearray()
    for byte in data:
        if byte in (HDR, TRL, ESC):
            out += bytes([ESC, byte & MASK])
        else:
            out.append(byte)
    return bytes(out)


def unescape(data):
    out, index = bytearray(), 0
    while index < len(data):
        if data[index] == ESC:
            index += 1
            if index >= len(data):
                break
            out.append(data[index] | 0x10)
        else:
            out.append(data[index])
        index += 1
    return bytes(out)


def encode(dtype, seq, payload=b""):
    """One frame, checksummed over the unescaped bytes and then stuffed."""
    body = bytes([dtype, seq]) + struct.pack(">I", len(payload)) + payload
    return bytes([HDR]) + escape(body + bytes([sum(body) & 0xFF])) + bytes([TRL])


def decode(frame):
    """(dtype, seq, payload) for a frame including its markers, or None.

    A frame that does not add up is dropped rather than guessed at: the only way
    to get one on this link is a bug on one side or the other, and acting on
    half a payload would move the headset to a mode nobody asked for.
    """
    if len(frame) < 9 or frame[0] != HDR or frame[-1] != TRL:
        return None
    message = unescape(frame[1:-1])
    if len(message) < 7:
        return None
    dtype, seq = message[0], message[1]
    length = struct.unpack(">I", message[2:6])[0]
    if len(message) != 6 + length + 1:
        return None
    payload, checksum = message[6:6 + length], message[6 + length]
    if checksum != sum(message[:6 + length]) & 0xFF:
        return None
    return dtype, seq, payload


def parse_ncasm(payload, expect=None):
    """The listening-mode state carried by a RET (0x67) or NTFY (0x69).

    `expect` is the inquired type this headset has already answered with, once
    it has answered: after that, a block of any other type is dropped. Before
    it, any candidate is allowed, because the probe is asking them in turn and
    the answer is what settles it. Both generations' numbers are on the table at
    that point, so a block is only ever read the way its own type says — never
    the way the UUID or the handshake suggested.
    """
    if len(payload) < 2 or payload[0] not in (NCASM_RET, NCASM_NTFY):
        return None
    inquired = payload[1]
    # The XM6 settles on 0x17, then volunteers compatible wider 0x19 mode
    # notifications. Other mismatched types still belong to another layout and
    # must be ignored once a model has answered.
    if expect is not None and inquired != expect \
            and not (expect == 0x17 and inquired == 0x19):
        return None
    if inquired in CANDIDATES_V1:
        return parse_ncasm_v1(payload)
    # 0x19 is notification-only on the WH-1000XM6: the device answers GET on
    # 0x17 but reports subsequent mode changes on this compatible wider block.
    if inquired in CANDIDATES_V2 or inquired == 0x19:
        return parse_ncasm_v2(payload)
    return None


def parse_ncasm_v2(payload):
    """The v2 block: 0x17 and 0x22 are 6 bytes, 0x15 is 8.

    Focus-on-voice and the ambient level are the last two bytes of every one of
    the three GET-able layouts, which is why they are read from the tail rather
    than from an index that depends on the variant.
    """
    if len(payload) < 6:
        return None
    inquired = payload[1]
    if inquired == 0x19:
        # Never a candidate — nothing GETs 0x19 — but a WH-1000XM6 sends it
        # unprompted for every mode change, answering a SET built for 0x17
        # (the type its own GET replies on). Confirmed against three states
        # here — off, anc, and ambient at a non-default level: effect and
        # ambientMode sit in the same slots as 0x17, followed by voice and
        # level one slot later, then two trailing 0x00 bytes. As with 0x17,
        # level/voice are only current while the mode is ambient.
        if len(payload) < 9:
            return None
        return {
            "inquired": inquired,
            "mode": "off" if payload[3] != 0x01 else ("ambient" if payload[4] == 0x01 else "anc"),
            "voice": payload[5] == 0x01,
            "level": payload[6],
            "ncValue": None,
        }
    on = payload[3] == 0x01
    # On 0x22 the master switch *is* the ambient switch; the other two layouts
    # carry the NC-versus-ambient choice in the byte after it.
    ambient = True if inquired == 0x22 else (len(payload) > 4 and payload[4] == 0x01)
    return {
        "inquired": inquired,
        "mode": "off" if not on else ("ambient" if ambient else "anc"),
        "voice": payload[-2] == 0x01,
        "level": payload[-1],
        # Only the 0x15 layout has it, at the index the ambient fields follow.
        "ncValue": payload[5] if inquired == 0x15 and len(payload) > 7 else None,
    }


def parse_ncasm_v1(payload):
    """The v1 block: NcAsmParam, 7 bytes after the command byte.

        67 02  ncAsmEffect ncType ncValue  asmType asmId asmValue

    A different shape from v2, not a different order: the master switch and the
    NC-versus-ambient choice sit one byte earlier, ncType is an enum of its own,
    and the level is at a fixed index rather than at the tail. Read by index,
    because a 7-byte block gives the tail no meaning it does not already have.
    """
    if len(payload) < 8:
        return None
    on = payload[2] == 0x01
    nc_value = payload[4]
    return {
        "inquired": payload[1],
        # ncValue: 0 OFF, 1 SINGLE (wind reduction), 2 DUAL (noise cancelling).
        # With the effect on and NC off, what is left is ambient sound.
        "mode": "off" if not on else ("ambient" if nc_value == 0x00 else "anc"),
        "voice": payload[6] == 0x01,      # asmId, 1 is voice
        "level": payload[7],              # asmValue, 0-20
        # ncType, the value a SET must echo back, the way 0x15's ncValue is.
        "ncValue": payload[3],
    }


class Adapter(Protocol):
    def __init__(self, model=None):
        super().__init__(model)
        self.buffer = bytearray()
        self.seq = 0
        self.waiting = False
        self.sent = 0
        self.queue = []
        self.inquired = None
        self.mode = ""
        self.level = 0
        # v1 only: the last ambient level the headset reported above zero. It
        # reports 0 in noise cancelling, so going back to ambient with the
        # reported level would always start at the bottom of the dial.
        self.ambient_level = 0
        self.voice = False
        self.nc_value = NC_VALUE_DEFAULT
        self.worn = None
        self.protocol_seen = False
        self.candidate = 0
        self.candidates = CANDIDATES
        self.linked = False

    def connected(self):
        self.linked = True
        self.send_init(1)

    def received(self, data):
        self.buffer += data
        self.parse_buffer()

    def push_line(self, first, observed=("noise.mode", "ambient.level", "ambient.focus_on_voice")):
        values = {"noise.mode": self.mode, "ambient.level": self.level,
                  "ambient.focus_on_voice": self.voice}
        capabilities = {
            "noise.mode": {"values": AVAILABLE.get(self.inquired, ["off", "anc", "ambient"])},
            "ambient.level": {"min": 0, "max": MAX_LEVEL, "step": 1},
            "ambient.focus_on_voice": {"type": "boolean"},
        }
        if self.worn is not None:
            values["wear.detected"] = self.worn
            capabilities["wear.detected"] = {"type": "boolean", "readOnly": True}
        self.report(values, capabilities, observed=observed)

    def command(self, control, value):
        if self.inquired is None:
            return
        available = AVAILABLE.get(self.inquired, ["off", "anc", "ambient"])
        if control == "noise.mode" and value in available:
            level = self.level
            if self.inquired == 0x02 and value == "ambient" and not level:
                # Back to the level this headset last said it had in ambient.
                # Nothing remembered yet is the bottom of the dial, as before.
                level = self.ambient_level
            self.push(self.set_frame(value, level, self.voice))
        elif control == "ambient.level" and "ambient" in available:
            self.push(self.set_frame("ambient", value, self.voice))
        elif control == "ambient.focus_on_voice":
            self.push(self.set_frame(self.mode or "ambient", self.level, value))

    def send_init(self, attempt):
        """CONNECT_GET_PROTOCOL_INFO, retried while the headset ignores it."""
        if self.exit_code is not None or self.protocol_seen:
            return False
        if attempt > INIT_ATTEMPTS:
            return self.finish(EXIT_TRANSIENT,
                               "the Sony headset did not answer the handshake")
        # Straight out rather than through the queue: this is the first frame on
        # the channel, and a retry has to go out whether or not the last attempt
        # was ever acknowledged.
        self.waiting = False
        self.queue = []
        self.write(encode(DATA_MDR, self.seq, b"\x00\x00"))
        self.schedule(INIT_TIMEOUT, "send_init", attempt + 1)
        return False


    def probe(self, candidate):
        """Ask the next candidate inquired type, or give up on this headset."""
        if self.exit_code is not None or self.inquired is not None:
            return False
        if candidate >= len(self.candidates):
            return self.finish(EXIT_UNSUPPORTED,
                               "the headset did not answer the listening-mode query")
        self.candidate = candidate
        self.push(bytes([NCASM_GET, self.candidates[candidate]]))
        self.schedule(CANDIDATE_TIMEOUT, "next_candidate", candidate)
        return False


    def next_candidate(self, candidate):
        # Only if nothing answered in the meantime and we are still on the same
        # question: a late ACK or an unsolicited notification may have settled
        # it already.
        if self.inquired is None and self.candidate == candidate:
            self.probe(candidate + 1)
        return False


    def push(self, payload):
        """Queue one command; it goes out when the link is free."""
        self.queue.append(payload)
        self.pump()


    def pump(self):
        if self.waiting or not self.queue or not self.linked:
            return False
        payload = self.queue.pop(0)
        self.waiting = True
        self.sent += 1
        self.write(encode(DATA_MDR, self.seq, payload))
        self.schedule(ACK_TIMEOUT, "on_ack_timeout", self.sent)
        return False


    def on_ack_timeout(self, serial):
        # The ACK for that command never came. Nothing can be concluded from
        # that on its own — the headset drops the odd frame — so the queue moves
        # on rather than the run ending.
        if self.waiting and self.sent == serial:
            self.waiting = False
            self.pump()
        return False


    def parse_buffer(self):
        """Every whole frame the buffer holds, in order.

        One read is not one frame: several can arrive together and one can be
        split across two reads. 0x3C cannot occur inside a stuffed body, so
        "from the next 0x3E to the first 0x3C after it" is exact.
        """
        while True:
            start = self.buffer.find(HDR)
            if start < 0:
                self.buffer.clear()
                return
            end = self.buffer.find(TRL, start + 1)
            if end < 0:
                del self.buffer[:start]
                return
            frame = bytes(self.buffer[start:end + 1])
            del self.buffer[:end + 1]
            self.on_frame(frame)
            if self.exit_code is not None:
                return


    def on_frame(self, frame):
        decoded = decode(frame)
        if decoded is None:
            return
        dtype, seq, payload = decoded
        if dtype == ACK:
            # The ACK carries the sequence number our next command must use, and
            # it is the only thing that advances ours: a device-initiated frame
            # does not move it.
            self.seq = seq
            self.waiting = False
            self.pump()
            return
        if dtype in (DATA_MDR, DATA_MDR_NO2):
            # First thing, before acting on it, and for unsolicited frames too.
            # An unacknowledged frame is retransmitted and then the headset goes
            # quiet.
            self.write(encode(ACK, 1 - seq))
        if not payload:
            return
        if payload[0] == CONNECT_RET_PROTOCOL_INFO:
            if not self.protocol_seen:
                self.protocol_seen = True
                if self.model.get("noModes", False):
                    return self.finish(EXIT_UNSUPPORTED, "no ANC/Ambient on this model (confirmed on hardware; see PROTOCOL.md)")
                self.order_candidates(len(payload))
                self.probe(0)
                # Wearing status is never volunteered at connect the way NCASM's
                # RET is answered here for free — only a later change speaks up
                # on its own — so a model in MODELS that answers it is asked
                # once, up front, rather than leaving `worn` unset until the
                # first time you take the headset off. The pinned models are
                # not asked; see MODELS.
                if self.model["wear"]:
                    self.push(bytes([SYSTEM_GET_STATUS, WEARING_STATUS_TYPE]))
            return
        state = parse_ncasm(payload, self.inquired)
        if state is not None:
            self.on_state(state)
        elif len(payload) >= 3 and payload[0] in (SYSTEM_RET_STATUS, SYSTEM_NTFY_STATUS) \
                and payload[1] == WEARING_STATUS_TYPE:
            self.on_wear(payload[-1] == 0x00)


    def order_candidates(self, length):
        """Ask the generation the handshake named first — and the other after.

        The reply's length is the protocol generation: 8 bytes v2, 4 bytes v1.
        That is a good hint and a bad rule. Two models answer 0x17 today, and a
        headset that replies short — or replies with something nobody has seen —
        must still be asked 0x17, 0x15 and 0x22 before it is given up on, or one
        handshake byte is all it takes to lose a headset that works. So the
        length moves the likely questions to the front and the rest stay behind
        them: a headset that answers something pays nothing for the ones behind
        it, and a headset that answers nothing sends three more frames before
        the same verdict.
        """
        first = CANDIDATES_V1 if length < 8 else CANDIDATES_V2
        rest = tuple(c for c in CANDIDATES if c not in first)
        self.candidates = first + rest


    def on_state(self, state):
        """A RET or an NTFY: the headset's own account of its listening mode."""
        first = self.inquired is None
        self.inquired = state["inquired"]
        self.mode = state["mode"]
        self.level = state["level"]
        if state["inquired"] == 0x02 and state["level"] > 0:
            self.ambient_level = state["level"]
        self.voice = state["voice"]
        if state["ncValue"] is not None:
            # Whatever the headset reports here is what a SET must echo back;
            # inventing a value is how the 0x15 layout gets rejected.
            self.nc_value = state["ncValue"]
        self.push_line(first)


    def on_wear(self, worn):
        """SYSTEM status, type 0x10: on-head or not, from a headset that says so.

        Arrives independently of NCASM and on its own schedule — the widget
        wants to know the moment it changes, not only the next time the
        listening mode does — so it gets its own push rather than waiting to
        ride along on an NCASM line. Before the mode is known there is nothing
        to build a line for; push_line() is called the moment on_state() does
        settle it, and self.worn is read then like every other field.
        """
        self.worn = worn
        if self.inquired is not None:
            self.push_line(False, observed=("wear.detected",))


    def set_frame(self, mode, level, voice):
        """The SET block for the variant this headset answered with.

        The ambient level and the focus-on-voice flag are carried in every mode,
        and the headset stores them and only applies them in ambient — so a
        change of mode has to repeat the stored values rather than send zeroes.
        """
        on = 1 if mode != "off" else 0
        ambient = 1 if mode == "ambient" else 0
        level = max(0, min(MAX_LEVEL, int(level)))
        voice = 1 if voice else 0
        if self.inquired == 0x02:
            # v1: the block the headset answered with, written back. ncValue
            # carries the three-way choice here rather than a separate ambient
            # flag, and asmType (0x01) is the only value the WH-1000XM4 was
            # seen to take.
            # Frame sony-device-center sends and a WH-1000XM4 obeys: effect
            # 0x11 (adjustment completion), level-adjustment type, dual/single
            # 2 for noise cancelling and 0 for ambient, level only in ambient.
            if mode == "ambient":
                return bytes([NCASM_SET, 0x02, 0x11, 0x01, 0x00, 0x01, voice,
                              max(1, level)])
            return bytes([NCASM_SET, 0x02, 0x11 if on else 0x00, 0x01,
                          0x02 if on else 0x00, 0x01, voice, 0x00])
        if self.inquired == 0x15:
            return bytes([NCASM_SET, 0x15, 0x01, on, ambient,
                          self.nc_value, voice, level])
        if self.inquired == 0x22:
            # Ambient-only: the master switch is the ambient switch, so "anc"
            # never reaches here (it is not in this variant's available list).
            return bytes([NCASM_SET, 0x22, 0x01, on, voice, level])
        return bytes([NCASM_SET, 0x17, 0x01, on, ambient, voice, level])
