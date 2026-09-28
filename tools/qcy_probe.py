#!/usr/bin/env python3
"""Discover what a QCY headset exposes over LE GATT and capture its replies.

    tools/qcy_probe.py ADDRESS [--listen SECONDS]
    tools/qcy_probe.py ADDRESS --requests   # send candidate read/request probes
    tools/qcy_probe.py ADDRESS --cycle      # cycle and restore noise-cancel modes

Enumerates the whole GATT tree the device exposes to BlueZ, subscribes to
every notify-capable service, and logs raw TX/RX bytes with timestamps. It
writes nothing to the device except the requests explicitly asked for, and
restores the observed starting mode after a --cycle run.

On the QCY H3 (Jieli JL7018F6) no GATT tree ever appears: BlueZ opens only
the BR/EDR bearer, the device never advertises its BLE control service, and
the probe reports no write channel. See docs/captures/qcy-h3-live.txt.
"""
import argparse
import datetime
import json
import signal
import sys
import time

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

# Candidate frame sources, from documented QCY/Jieli behaviour. Kept as
# *probe candidates* only: nothing is enabled that the device did not answer.
WRITE_CANDIDATES = [
    # QCY standard protocol channel (HT07, T13 and similar).
    "00001001-0000-1000-8000-00805f9b34fb",
    # JL-service branded reuse of the same handles.
    "0000a001-0000-1000-8000-00805f9b34fb",
]
NOTIFY_CANDIDATES = [
    "00001002-0000-1000-8000-00805f9b34fb",
    "0000000e-0000-1000-8000-00805f9b34fb",
]
# V1-style direct reads: battery and version on their own characteristics.
V1_BATTERY = "00000008-0000-1000-8000-00805f9b34fb"
V1_VERSION = "00000007-0000-1000-8000-00805f9b34fb"
V1_BATTERY_STD = "00002a19-0000-1000-8000-00805f9b34fb"

REPLY_TIMEOUT = 8.0
CONNECT_TIMEOUT = 45.0


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def log(text):
    print(now(), text, flush=True)


def qcy(cmd, plen, *params):
    """FF <body_len> <cmd> <plen> <params...>; body_len = total length - 2."""
    body = bytes([cmd, plen] + list(params))
    return b"\xff" + bytes([len(body) + 1]) + body


# The read-mostly probes a QCY app sends on connect: current value per command.
REQUESTS = {
    "battery_req": b"\xfe\x01\x2f",
    "mode_req": b"\xfe\x01\x0c",
    "anc_setting_req": b"\xfe\x01\x17",
    "version_req": b"\xfe\x01\x30",
    "lowlvl_req": b"\xfe\x01\x09",
    "wear_req": b"\xfe\x01\x29",
    "inesense_req": b"\xfe\x01\x48",
    "ancwear_req": b"\xfe\x01\x2c",
    "gen_req": b"\xfe\x01\x01",
}

MODE_SETS = {  # command 0x0C simple modes; 0x17 advanced
    "off": [qcy(0x0c, 1, 0x00)],
    "anc": [qcy(0x0c, 1, 0x01)],
    "ambient": [qcy(0x0c, 1, 0x03)],
}


class Probe:
    def __init__(self, address, wire=log):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.manager = dbus.Interface(self.bus.get_object("org.bluez", "/"),
                                      "org.freedesktop.DBus.ObjectManager")
        self.loop = GLib.MainLoop()
        self.address = address.upper()
        self.wire = wire
        self.path = None
        self.link = None
        self.write_char = None
        self.notify_char = None
        self.extra_notify = []
        self.stop_at = None
        self.deadline = None
        self.pending_read = None
        self.closed = False
        self.signal_match = self.bus.add_signal_receiver(
            self.changed, signal_name="PropertiesChanged",
            dbus_interface="org.freedesktop.DBus.Properties", path_keyword="path")

    def objects(self):
        return self.manager.GetManagedObjects(timeout=3)

    def interface(self, path, interface):
        return dbus.Interface(self.bus.get_object("org.bluez", path), interface)

    def find_listed(self):
        """Locate the device path and its current GATT tree in BlueZ."""
        objects = self.objects()
        path = next((str(p) for p, v in objects.items()
                     if str(v.get("org.bluez.Device1", {}).get("Address", "")).upper()
                     == self.address), None)
        if not path:
            return None, {}
        chars = {}
        for p, v in objects.items():
            if str(p).startswith(path + "/") and "org.bluez.GattCharacteristic1" in v:
                chars[str(v["org.bluez.GattCharacteristic1"]["UUID"])] = str(p)
        return path, chars

    def connect(self):
        path, _ = self.find_listed()
        if not path:
            raise RuntimeError("device not present in BlueZ")
        self.path = path
        self.link = {"deadline": time.monotonic() + CONNECT_TIMEOUT, "connected": False}
        device = self.interface(path, "org.bluez.Device1")
        props = dbus.Interface(self.bus.get_object("org.bluez", path),
                               "org.freedesktop.DBus.Properties")
        if not bool(props.Get("org.bluez.Device1", "Connected")):
            device.Connect(reply_handler=self.connected, error_handler=self.connect_failed,
                           timeout=20)
        else:
            # Already over Classic; ask BlueZ to bring up its GATT bearer too.
            device.Connect(reply_handler=self.connected, error_handler=self.connect_failed,
                           timeout=20)

    def connected(self):
        self.link["connected"] = True

    def connect_failed(self, error):
        log("CONNECT FAILED " + str(error))
        self.deadline = time.monotonic()

    def changed(self, interface, values, invalidated, path=None):
        if self.closed:
            return
        if interface == "org.bluez.Device1" and path == self.path and "Connected" in values:
            if not bool(values["Connected"]):
                log("DEVICE DISCONNECTED")
                self.deadline = time.monotonic()
                return
        if interface != "org.bluez.GattCharacteristic1" or "Value" not in values:
            return
        self.wire_handle(path, bytes(values["Value"]))

    def wire_handle(self, path, raw):
        role = "write" if path == self.notify_char else "notify"
        self.wire("RX " + role + " " + raw.hex(" "))

    def discover(self):
        """Wait for, then print and classify, the device's GATT characteristics."""
        start = time.monotonic()
        end = start + CONNECT_TIMEOUT
        services = []
        chars = []
        while time.monotonic() < end:
            objects = self.objects()
            # Show the whole subtree path shape so a Bearer.LE1 or leased
            # GATT tree that uses a different path is visible in the capture.
            heads = sorted({str(p).split("/")[-1][:3] for p in objects
                            if str(p).startswith(self.path)})
            services = [(str(p), str(v["org.bluez.GattService1"]["UUID"]))
                        for p, v in objects.items()
                        if str(p).startswith(self.path + "/")
                        and "org.bluez.GattService1" in v]
            if services or self.deadline is not None:
                break
            if int((time.monotonic() - start)) % 3 == 0:
                log("WAITING for GATT tree; subtree heads: " + ",".join(heads)
                    if heads else "WAITING for GATT tree")
            self.wait_ms(100)
        objects = self.objects()
        services = [(str(p), str(v["org.bluez.GattService1"]["UUID"]))
                    for p, v in objects.items()
                    if str(p).startswith(self.path + "/")
                    and "org.bluez.GattService1" in v]
        chars = [(str(p), str(v["org.bluez.GattCharacteristic1"]["UUID"]),
                  list(v["org.bluez.GattCharacteristic1"].get("Flags", ())),
                  list(v["org.bluez.GattCharacteristic1"].get("Value", ())))
                 for p, v in objects.items()
                 if str(p).startswith(self.path + "/")
                 and "org.bluez.GattCharacteristic1" in v]
        services.sort(); chars.sort()
        for p, uuid in services:
            log("SERVICE " + uuid + " @" + p)
        for p, uuid, flags, value in chars:
            log("CHAR " + uuid + " flags=" + ",".join(flags) + " value=" + bytes(value).hex(" "))
        by_uuid = {u: p for p, u, _, _ in chars}
        self.note_writes(by_uuid)
        self.subscribe_all(by_uuid, chars)

    def note_writes(self, by_uuid):
        for uuid in WRITE_CANDIDATES:
            if uuid in by_uuid:
                self.write_char = by_uuid[uuid]
                log("WRITE CHANNEL = " + uuid)
                return
        # Fall back: any write/command-capable characteristic.
        for uuid in ("0000ff01-0000-1000-8000-00805f9b34fb", "0000000a-0000-1000-8000-00805f9b34fb"):
            if uuid in by_uuid:
                self.write_char = by_uuid[uuid]
                log("WRITE CHANNEL (V1) = " + uuid)
                return
        log("NO OBVIOUS WRITE CHANNEL")
        # Try classic AVRCP-free V1 reads for battery/version directly.
        for uuid in (V1_BATTERY, V1_BATTERY_STD):
            if uuid in by_uuid:
                self.read_value(by_uuid[uuid], "battery?")

    def subscribe_all(self, by_uuid, chars):
        for uuid in NOTIFY_CANDIDATES:
            if uuid in by_uuid:
                self.notify_char = by_uuid[uuid]
                log("SUBSCRIBE " + uuid)
                self.notify(self.notify_char)
                return

    def read_value(self, path, label):
        try:
            value = self.interface(path, "org.bluez.GattCharacteristic1").ReadValue(
                {}, timeout=5)
            self.wire("RX read(" + label + ") " + bytes(value).hex(" "))
        except dbus.DBusException as error:
            self.wire("RX read(" + label + ") FAILED " + str(error))

    def notify(self, path):
        self.interface(path, "org.bluez.GattCharacteristic1").StartNotify(
            reply_handler=lambda: self.wire("RX subscribed " + path),
            error_handler=lambda e: self.wire("RX subscribe failed " + str(e)), timeout=5)

    def send(self, data):
        if not self.write_char:
            self.wire("TX no write channel")
            return False
        self.wire("TX " + data.hex(" "))
        self.deadline = time.monotonic() + REPLY_TIMEOUT
        self.interface(self.write_char, "org.bluez.GattCharacteristic1").WriteValue(
            dbus.Array(data, signature="y"),
            dbus.Dictionary({"type": "command"}, signature="sv"),
            reply_handler=lambda: None,
            error_handler=lambda e: self.wire("TX failed " + str(e)), timeout=3)
        return True

    def wait(self, predicate, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.deadline is not None and time.monotonic() >= self.deadline:
                self.deadline = None
                return False
            self.wait_ms(100)
            if predicate():
                return True
        return False

    def wait_ms(self, ms):
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            context = GLib.MainContext.default()
            while context.pending():
                context.iteration(False)
            time.sleep(0.01)

    def listen(self, seconds):
        self.wait_ms(seconds * 1000)

    def requests(self):
        for label, data in REQUESTS.items():
            self.send(data)
            if not self.wait(lambda: False, REPLY_TIMEOUT):
                pass

    def cycle(self):
        # Read initial mode on the QCY channel, then restore whatever it was.
        self.send(b"\xfe\x01\x0c")
        if not self.wait(lambda: False, 3):
            return 0
        # Without a parsed response we cannot prove the start state; the
        # instructions and bytes we accepted are recorded verbatim.
        initial = None
        for mode, frames in MODE_SETS.items():
            for frame in frames:
                self.send(frame)
                self.wait(lambda: False, 0.5)
            log("RESULT mode=" + mode + " (reported by device later")
        if initial is not None:
            for frame in MODE_SETS.get(initial, ()):
                self.send(frame)
                self.wait(lambda: False, 0.5)
            log("RESTORED " + initial)
        return 1

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.signal_match.remove()
        if self.notify_char:
            try:
                self.interface(self.notify_char, "org.bluez.GattCharacteristic1").StopNotify(timeout=2)
            except dbus.DBusException:
                pass
        for extra in self.extra_notify:
            try:
                self.interface(extra, "org.bluez.GattCharacteristic1").StopNotify(timeout=2)
            except dbus.DBusException:
                pass
        if self.path and self.link and self.link.get("connected"):
            try:
                self.interface(self.path, "org.bluez.Device1").Disconnect(timeout=2)
            except dbus.DBusException:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address")
    parser.add_argument("--listen", type=float, default=5)
    parser.add_argument("--requests", action="store_true")
    parser.add_argument("--cycle", action="store_true")
    args = parser.parse_args()
    if len(args.address.replace(":", "")) != 12 or not 0 <= args.listen <= 600:
        parser.error("valid MAC address and listen seconds 0..600 required")
    probe = None
    state = {"stopping": False}
    def stop(*_):
        state["stopping"] = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        probe = Probe(args.address)
        probe.connect()
        probe.discover()
        if state["stopping"]:
            return 0
        if args.requests:
            probe.requests()
        if args.cycle:
            probe.cycle()
        probe.listen(args.listen)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        log("FAILED " + repr(error))
        return 1
    finally:
        if probe:
            probe.close()


if __name__ == "__main__":
    sys.exit(main())