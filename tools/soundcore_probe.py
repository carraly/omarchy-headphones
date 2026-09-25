#!/usr/bin/env python3
"""Probe Soundcore listening mode over RFCOMM.

Registers an org.bluez.Profile1 for the Soundcore vendor UUID (0cf12d31-fac3-4553-bd80-d6832e7...),
connects the socket, and sends a state query (0x01, 0x01) and a sound-mode query
(0x06, 0x01), optionally setting a mode.

A set writes the device's own sound-mode bytes back with only the mode replaced:
the 06 01 reply where there was one. The Space 2, whose answer to that question
is untested, falls back to the six bytes at offset 71 of its state, where its
block is known to sit. Any other device that did not answer is not written to:
offset 71 is somewhere else's bytes on the Space One Pro and past the end on
the Life Q30. The probe used to send a fixed
`1f ff 00 00 01` after the mode; on the Life Q30 that filled two fields with
values nobody chose (PROTOCOL.md, Life Q30).

Usage: soundcore_probe.py <address> [seconds] [set:off|anc|ambient]
"""
import datetime
import os
import re
import sys

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

SOUNDCORE_UUID_PREFIX = "0cf12d31-fac3-4553-bd80-d6832e7"
DEFAULT_UUID = "0cf12d31-fac3-4553-bd80-d6832e700000"
PROFILE_PATH = "/io/github/ncr/omaphones/soundcore_probe"

OUTBOUND_HDR = bytes([0x08, 0xEE, 0x00, 0x00, 0x00])

CMD_STATE_UPDATE = (0x01, 0x01)
CMD_SOUND_MODES_NOTIFY = (0x06, 0x01)
CMD_SOUND_MODES_SET = (0x06, 0x81)

# The one model whose block is known to sit at offset 71 of the state.
SPACE_2_SUFFIX = "d1402"

SET_BYTE = {"anc": 0x00, "ambient": 0x01, "off": 0x02}
MODE_NAME = {0x00: "anc", 0x01: "ambient", 0x02: "off"}


def stamp():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def calc_checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def make_packet(cmd: tuple[int, int], body: bytes = b"") -> bytes:
    total_len = 5 + 2 + 2 + len(body) + 1
    raw = OUTBOUND_HDR + bytes([cmd[0], cmd[1], total_len & 0xFF, (total_len >> 8) & 0xFF]) + body
    return raw + bytes([calc_checksum(raw)])


def mode_write_body(wanted, reply, state_block):
    """The body of a 06 81 write, or None when the device showed no block.

    `reply` is the body of the device's 06 01 answer, `state_block` the six
    bytes at offset 71 of the state, passed only for the Space 2. At most six
    bytes go out, which is what soundcore-bridge writes; a shorter reply is
    written back as short as it came.
    """
    block = reply if reply else state_block
    if not block:
        return None
    return bytes([SET_BYTE[wanted]]) + bytes(block[1:6])


def find_device_path_and_uuid(bus, address):
    wanted = address.upper()
    objects = dbus.Interface(bus.get_object("org.bluez", "/"),
                             "org.freedesktop.DBus.ObjectManager").GetManagedObjects()
    for path, interfaces in objects.items():
        device = interfaces.get("org.bluez.Device1")
        if device and str(device.get("Address", "")).upper() == wanted:
            uuids = [str(u).lower() for u in device.get("UUIDs", [])]
            for u in uuids:
                if u.startswith(SOUNDCORE_UUID_PREFIX):
                    return str(path), u
            return str(path), DEFAULT_UUID
    return None, None


class Link:
    def __init__(self, fd, plan, trust_offset_71=False):
        self.fd = fd
        self.trust_offset_71 = trust_offset_71
        self.buffer = bytearray()
        self.state_block = None
        self.reply = None
        GLib.io_add_watch(fd, GLib.PRIORITY_DEFAULT,
                          GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_io)
        delay = 400
        for name, data in plan:
            GLib.timeout_add(delay, lambda n=name, d=data: (self.step(n, d), False)[1])
            delay += 800

    def on_io(self, _fd, condition):
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            print("%s !! closed" % stamp(), flush=True)
            self.fd = -1
            return False
        try:
            data = os.read(self.fd, 4096)
        except OSError as error:
            print("%s !! read err: %s" % (stamp(), error), flush=True)
            self.fd = -1
            return False
        if not data:
            print("%s !! EOF" % stamp(), flush=True)
            self.fd = -1
            return False
        self.buffer += data
        self.parse_buffer()
        return True

    def parse_buffer(self):
        while len(self.buffer) >= 9:
            if not (self.buffer[0] == 0x09 and self.buffer[1] == 0xFF):
                idx = self.buffer.find(b"\x09\xff")
                if idx == -1:
                    self.buffer = bytearray()
                    break
                self.buffer = self.buffer[idx:]
                if len(self.buffer) < 9:
                    break
            cmd = (self.buffer[5], self.buffer[6])
            total_len = self.buffer[7] | (self.buffer[8] << 8)
            if len(self.buffer) < total_len:
                break
            packet = bytes(self.buffer[:total_len])
            self.buffer = self.buffer[total_len:]

            cksum = packet[-1]
            if calc_checksum(packet[:-1]) != cksum:
                print("%s !! checksum mismatch in packet" % stamp(), flush=True)
                continue

            body = packet[9:-1]
            if cmd == CMD_STATE_UPDATE:
                if len(body) >= 77:
                    self.state_block = bytes(body[71:77])
                    mode_name = MODE_NAME.get(body[71], "unknown(%d)" % body[71])
                    print("%s <<< STATE: mode=%s params=%s" % (stamp(), mode_name, bytes(body[71:77]).hex()), flush=True)
                else:
                    # Short for the offset this tool reads, not short of
                    # meaning: a model whose block sits earlier answers here,
                    # and hiding its payload hides the only evidence of where
                    # the block is. The Life Q30's state is 70 bytes.
                    print("%s <<< STATE (%d bytes, shorter than offset 71) %s"
                          % (stamp(), len(body), body.hex()), flush=True)
            elif cmd == CMD_SOUND_MODES_NOTIFY:
                if body:
                    self.reply = bytes(body)
                mode_name = MODE_NAME.get(body[0] if body else -1, "unknown")
                print("%s <<< NOTIFY: mode=%s body=%s" % (stamp(), mode_name, body.hex()), flush=True)
            elif cmd == CMD_SOUND_MODES_SET:
                print("%s <<< ACK [06 81]" % stamp(), flush=True)
            else:
                print("%s <<< PKT cmd=(0x%02x, 0x%02x) len=%d body=%s" % (
                    stamp(), cmd[0], cmd[1], total_len, body.hex()), flush=True)

    def step(self, name, data):
        """One step of the plan. A set is built here, not ahead of time: what
        it writes depends on what the device has said by now."""
        if name.startswith("set-"):
            body = mode_write_body(data, self.reply,
                                   self.state_block if self.trust_offset_71 else None)
            if body is None:
                print("%s !! %s not sent: the device showed no sound-mode bytes to "
                      "write back, and guessed ones would overwrite its settings"
                      % (stamp(), name), flush=True)
                return
            data = make_packet(CMD_SOUND_MODES_SET, body)
        self.send(name, data)

    def send(self, name, data):
        if self.fd < 0:
            return
        print("%s >>> %s (%d bytes) %s" % (stamp(), name, len(data), data.hex()), flush=True)
        try:
            os.write(self.fd, data)
        except OSError as error:
            print("%s !! write: %s" % (stamp(), error), flush=True)


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: soundcore_probe.py <address> [seconds] [set:off|anc|ambient]")
        return 0
    address = argv[0]
    seconds = 10
    wanted = None
    for arg in argv[1:]:
        if arg.startswith("set:"):
            wanted = arg.split(":", 1)[1].strip().lower()
            if wanted not in SET_BYTE:
                sys.exit("unknown mode %r (off, anc, ambient)" % wanted)
        else:
            seconds = int(arg)

    plan = [
        ("req_state", make_packet(CMD_STATE_UPDATE)),
        # Ask for the sound modes as well: a device that answers 06 01 says
        # where its block is without anyone guessing an offset.
        ("req_sound_modes", make_packet(CMD_SOUND_MODES_NOTIFY)),
    ]
    if wanted is not None:
        # The mode name, not a packet: Link.step builds the write from what
        # the device answered to the two questions above.
        plan.append(("set-%s" % wanted, wanted))
        plan.append(("req_state2", make_packet(CMD_STATE_UPDATE)))
        plan.append(("req_sound_modes2", make_packet(CMD_SOUND_MODES_NOTIFY)))

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    loop = GLib.MainLoop()
    link = {"l": None}

    device_path, target_uuid = find_device_path_and_uuid(bus, address)
    if not device_path:
        sys.exit("no paired device with address %s" % address)

    class Profile(dbus.service.Object):
        @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
        def Release(self):
            pass

        @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}",
                             out_signature="")
        def NewConnection(self, _path, fd, _properties):
            print("%s == connected" % stamp(), flush=True)
            link["l"] = Link(fd.take(), plan,
                             trust_offset_71=target_uuid.endswith(SPACE_2_SUFFIX))

        @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
        def RequestDisconnection(self, _path):
            print("%s == BlueZ asked to disconnect" % stamp(), flush=True)

    Profile(bus, PROFILE_PATH)
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                             "org.bluez.ProfileManager1")
    manager.RegisterProfile(PROFILE_PATH, target_uuid, {
        "Name": "Soundcore probe",
        "Role": "client",
        "Channel": dbus.UInt16(0),
        "RequireAuthentication": dbus.Boolean(False),
        "RequireAuthorization": dbus.Boolean(False),
        "AutoConnect": dbus.Boolean(False),
    })

    dev = dbus.Interface(bus.get_object("org.bluez", device_path), "org.bluez.Device1")

    def connect(attempt=0):
        def failed(error):
            print("%s ConnectProfile: %s" % (stamp(), error.get_dbus_message()), flush=True)
            if attempt < 10:
                GLib.timeout_add(1500, lambda: connect(attempt + 1))

        dev.ConnectProfile(target_uuid, reply_handler=lambda: None,
                           error_handler=failed, timeout=30)
        return False

    GLib.timeout_add(500, connect)
    GLib.timeout_add_seconds(seconds, lambda: (loop.quit(), False)[1])
    loop.run()
    try:
        manager.UnregisterProfile(PROFILE_PATH)
    except dbus.DBusException:
        pass
    print("%s == done" % stamp(), flush=True)


if __name__ == "__main__":
    main()
