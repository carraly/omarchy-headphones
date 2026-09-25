"""Native codec extracted from soundcore-bridge; existing owners still use that bridge.
New models must supply their own evidence before activation.
"""
import struct
import re
from omaphones.api import Protocol

OUTBOUND_HDR = bytes([0x08, 0xEE, 0x00, 0x00, 0x00])

CMD_STATE_UPDATE = (0x01, 0x01)

CMD_SOUND_MODES_NOTIFY = (0x06, 0x01)

CMD_SOUND_MODES_SET = (0x06, 0x81)

MODE_FROM_BYTE = {0x00: "anc", 0x01: "ambient", 0x02: "off"}

BYTE_FROM_MODE = {name: byte for byte, name in MODE_FROM_BYTE.items()}

AVAILABLE = ["off", "anc", "ambient"]

def calc_checksum(data: bytes) -> int:
    return sum(data) & 0xFF

def make_packet(cmd: tuple[int, int], body: bytes = b"") -> bytes:
    total_len = 5 + 2 + 2 + len(body) + 1
    raw = OUTBOUND_HDR + bytes([cmd[0], cmd[1], total_len & 0xFF, (total_len >> 8) & 0xFF]) + body
    return raw + bytes([calc_checksum(raw)])

class Adapter(Protocol):
    def __init__(self, model=None):
        super().__init__(model)
        self.buffer = bytearray()
        self.handshake_seen = False
        self.query_answered = False
        self.params = None

    def connected(self):
        self.handshake(1)

    def handshake(self, attempt):
        if self.handshake_seen:
            return
        if attempt > 3:
            self.finish(1, 'the device did not answer the handshake')
            return
        self.write(make_packet(CMD_STATE_UPDATE))
        self.schedule(2000, 'handshake', attempt + 1)

    def query(self):
        self.write(make_packet(CMD_SOUND_MODES_NOTIFY))

    def deadline(self):
        if self.params is None:
            self.finish(3, 'the device did not answer the listening-mode query')

    def received(self, data):
        self.buffer.extend(data)
        while self.buffer:
            at = self.buffer.find(b'\x09\xff')
            if at < 0:
                self.buffer = self.buffer[-1:] if self.buffer[-1] == 9 else bytearray()
                return
            del self.buffer[:at]
            if len(self.buffer) < 9:
                return
            size = self.buffer[7] | self.buffer[8] << 8
            if size < 10:
                del self.buffer[0]
                continue
            if len(self.buffer) < size:
                return
            packet = bytes(self.buffer[:size])
            del self.buffer[:size]
            if calc_checksum(packet[:-1]) != packet[-1]:
                continue
            cmd, body = (packet[5], packet[6]), packet[9:-1]
            if cmd == CMD_STATE_UPDATE:
                self.handshake_seen = True
                offset = self.model['offset']
                if len(body) >= offset + self.width():
                    self.observed(body[offset:offset + self.width()])
                if self.model['query']:
                    self.query()
                if self.params is None:
                    self.schedule(3000, 'deadline')
            elif cmd == CMD_SOUND_MODES_NOTIFY and len(body) >= self.width():
                self.query_answered = True
                self.observed(body[:self.width()])

    def width(self):
        # Six bytes unless the model says otherwise: mode, four fields, then the
        # ambient level. The Life Q30's block is four wide and has neither the
        # level nor the wind-noise switch, so it reports the mode alone.
        return self.model.get('width', 6)

    def observed(self, params):
        if params[0] not in MODE_FROM_BYTE:
            return
        if len(params) < 6:
            self.params = list(params)
            self.report({'noise.mode': MODE_FROM_BYTE[params[0]]}, {'noise.mode': {'values': AVAILABLE}})
            return
        if not 1 <= params[5] <= 5:
            return
        self.params = list(params)
        self.report({'noise.mode': MODE_FROM_BYTE[params[0]], 'ambient.level': params[5], 'noise.wind_reduction': bool(params[4])},
                    {'noise.mode': {'values': AVAILABLE}, 'ambient.level': {'min': 1, 'max': 5, 'step': 1}, 'noise.wind_reduction': {'type': 'boolean'}})

    def command(self, control, value):
        if self.params is None:
            return
        body = list(self.params)
        if control == 'noise.mode' and value in BYTE_FROM_MODE:
            body[0] = BYTE_FROM_MODE[value]
        elif len(body) < 6:
            return
        elif control == 'ambient.level' and type(value) is int and 1 <= value <= 5:
            body[5] = value
        elif control == 'noise.wind_reduction' and type(value) is bool:
            body[4] = int(value)
        else:
            return
        self.write(make_packet(CMD_SOUND_MODES_SET, bytes(body)))
        if self.model['query'] and self.query_answered:
            self.schedule(400, 'query')
        # params and reported values remain device observations until actual RX.
