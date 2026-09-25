"""CMF capture replay and lifecycle regression, without touching owner pins.

Incoming bytes come from the named capture, never the bridge's encoder.
Only the Headphone Pro redacted device-info frame is excluded; the complete
Buds 2 device-info frame is replayed with its original CRC. The real loop's info timeout unlocks queries in lifecycle tests.
Socket errors, clock progression and CRC damage are synthetic fault injection.
"""
import collections
import pathlib
import re
import unittest
from unittest.mock import patch

from tests import harness

bridge = harness.load_bridge("nothing-bridge")
CAPTURE = pathlib.Path(harness.ROOT) / "docs/captures/nothing-headphone-pro.txt"


def captured(path=CAPTURE, *, skip_device_info=True):
    rows = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        found = re.search(r"\b55(?: [0-9a-fA-F]{2})+", line)
        if found and not (skip_device_info and "device-info    answer" in line):
            rows.append((number, "<-" if "<-" in line else "->",
                         bytes.fromhex(found.group())))
    return rows


ROWS = captured()
BUDS2_CAPTURE = pathlib.Path(harness.ROOT) / "docs/captures/nothing-cmf-buds-2.txt"
BUDS2_ROWS = captured(BUDS2_CAPTURE, skip_device_info=False)


def rx_buds2(command, direction, payload):
    for _, flow, raw in BUDS2_ROWS:
        if (flow == "<-" and raw[3:5] == bytes((command, direction))
                and raw[8:8 + int.from_bytes(raw[5:7], "little")] == bytes.fromhex(payload)):
            return raw
    raise AssertionError("response absent from CMF Buds 2 capture: %02x %02x %s" % (command, direction, payload))


def rx(command, direction, payload):
    """Find a full captured response, including its own header and CRC."""
    for _, flow, raw in ROWS:
        if (flow == "<-" and raw[3:5] == bytes((command, direction))
                and raw[8:8 + int.from_bytes(raw[5:7], "little")] == bytes.fromhex(payload)):
            return raw
    raise AssertionError("response absent from capture")


class Socket:
    def __init__(self, incoming=()):
        self.incoming = collections.deque(incoming)
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(bytes(data))

    def recv(self, _):
        return self.incoming.popleft()

    def close(self):
        self.closed = True


class CaptureReplay(unittest.TestCase):
    def setUp(self):
        self.lines = []
        self.emit = patch.object(bridge, "emit", self.lines.append)
        self.emit.start()
        self.addCleanup(self.emit.stop)
        with patch.object(bridge, "read_case_cache", return_value=None):
            self.device = bridge.Bridge("2C:BE:EE:3C:6F:FE", "CMF Headphone Pro")
        self.device.sock = Socket()

    def feed(self, raw):
        self.device.buffer += raw
        for frame in bridge.take_frames(self.device.buffer):
            self.device.on_frame(*frame)

    def test_every_unredacted_frame_has_its_own_valid_framing(self):
        self.assertEqual(len(ROWS), 64)
        for number, _, raw in ROWS:
            with self.subTest(line=number):
                buffer = bytearray(raw)
                self.assertEqual(len(bridge.take_frames(buffer)), 1)
                self.assertEqual(buffer, b"")

    def test_all_captured_replies_survive_every_split_without_using_encoder(self):
        for number, flow, raw in ROWS:
            if flow != "<-":
                continue
            expected = bridge.take_frames(bytearray(raw))
            for cut in range(1, len(raw)):
                with self.subTest(line=number, cut=cut):
                    buffer = bytearray(raw[:cut])
                    self.assertEqual(bridge.take_frames(buffer), [])
                    buffer += raw[cut:]
                    self.assertEqual(bridge.take_frames(buffer), expected)
                    self.assertEqual(buffer, b"")

    def test_all_controls_ack_readback_and_exact_captured_writes(self):
        self.feed(rx(0x1e, 0x40, "01 04 00 02 04 00"))
        self.feed(rx(0x07, 0x40, "01 06 0f"))
        self.feed(rx(0x41, 0x40, "02"))
        cases = [
            ("set off", "01 05 00", "01 05 00 02 04 00", "off", "adaptive"),
            ("set ambient", "01 07 00", "01 07 00 02 04 00", "ambient", "adaptive"),
            ("set anc", "01 04 00", "01 04 00 02 04 00", "anc", "adaptive"),
            ("level low", "01 03 00", "01 03 00 02 03 00", "anc", "low"),
            ("level mid", "01 02 00", "01 02 00 02 02 00", "anc", "mid"),
            ("level high", "01 01 00", "01 01 00 02 01 00", "anc", "high"),
            ("level adaptive", "01 04 00", "01 04 00 02 04 00", "anc", "adaptive"),
            ("latency on", "01", "01", "anc", "adaptive"),
            ("latency off", "02", "02", "anc", "adaptive"),
        ]
        for command, outgoing, answer, mode, level in cases:
            with self.subTest(command=command):
                latency = command.startswith("latency")
                cmd = 0x40 if latency else 0x0f
                before = len(self.lines)
                self.device.command(command)
                sent = self.device.sock.sent[-1]
                self.assertIn(sent, [raw for _, flow, raw in ROWS if flow == "->"])
                self.assertEqual(sent[3:5], bytes((cmd, 0xf0)))
                self.assertEqual(sent[8:-2], bytes.fromhex(outgoing))
                self.feed(rx(cmd, 0x70, ""))
                self.assertEqual(len(self.lines), before, "command/ACK must not publish state")
                self.assertIsNotNone(self.device.readback_at)
                self.device.readback()
                self.assertEqual([raw.hex(" ") for raw in self.device.sock.sent[-2:]], [
                    "55 60 01 1e c0 00 00 01 b1 1d",
                    "55 60 01 41 c0 00 00 01 25 10"])
                self.feed(rx(0x41 if latency else 0x1e, 0x40, answer))
                self.assertEqual(self.lines[-1]["mode"], mode)
                self.assertEqual(self.lines[-1]["ancLevel"], level)
                if latency:
                    self.assertEqual(self.lines[-1]["latency"], command.endswith("on"))

    def test_unsolicited_reports_deduplicate_and_damage_does_not_hide_next_frame(self):
        mode = rx(0x03, 0xe0, "01 04 00 02 04 00")
        battery = rx(0x01, 0xe0, "01 06 0f")
        self.feed(mode + battery)
        self.assertEqual(self.lines[-1]["battery"]["headset"], 15)
        count = len(self.lines)
        self.feed(mode + battery)
        self.assertEqual(len(self.lines), count)
        damaged = bytearray(rx(0x1e, 0x40, "01 01 00 02 01 00"))
        damaged[-1] ^= 0xff  # synthetic corruption of a captured frame
        self.feed(damaged + rx(0x03, 0xe0, "01 02 00 02 02 00"))
        self.assertEqual(self.lines[-1]["ancLevel"], "mid")
        self.assertEqual(len(self.lines), count + 1)


class CmfBuds2CaptureReplay(unittest.TestCase):
    def setUp(self):
        self.lines = []
        self.emit = patch.object(bridge, "emit", self.lines.append)
        self.emit.start()
        self.addCleanup(self.emit.stop)
        with patch.object(bridge, "read_case_cache", return_value=None):
            self.device = bridge.Bridge("3C:B0:ED:D0:AC:0B", "CMF Buds 2")
        self.device.sock = Socket()

    def feed(self, raw):
        self.device.buffer += raw
        for frame in bridge.take_frames(self.device.buffer):
            self.device.on_frame(*frame)

    def test_every_unredacted_buds2_frame_has_valid_framing(self):
        self.assertEqual(len(BUDS2_ROWS), 63)
        for number, _, raw in BUDS2_ROWS:
            with self.subTest(line=number):
                buffer = bytearray(raw)
                self.assertEqual(len(bridge.take_frames(buffer)), 1)
                self.assertEqual(buffer, b"")

    def test_full_info_reply_unlocks_exact_captured_queries(self):
        self.device.send(bridge.CMD_DEVICE_INFO)
        outgoing = [raw for _, flow, raw in BUDS2_ROWS if flow == "->"]
        self.assertEqual(self.device.sock.sent, outgoing[:1])
        info = next(raw for _, flow, raw in BUDS2_ROWS
                    if flow == "<-" and raw[3:5] == bytes((0x06, 0x40)))
        self.feed(info)
        self.assertTrue(self.device.info_seen)
        self.assertEqual(self.device.sock.sent, outgoing[:4])
        self.assertEqual(self.lines, [])

    def test_every_reply_survives_every_split_and_joined_stream(self):
        replies = [raw for _, flow, raw in BUDS2_ROWS if flow == "<-"]
        expected_stream = []
        for raw in replies:
            expected = bridge.take_frames(bytearray(raw))
            expected_stream.extend(expected)
            for cut in range(1, len(raw)):
                with self.subTest(frame=raw.hex(), cut=cut):
                    buffer = bytearray(raw[:cut])
                    self.assertEqual(bridge.take_frames(buffer), [])
                    buffer += raw[cut:]
                    self.assertEqual(bridge.take_frames(buffer), expected)
                    self.assertEqual(buffer, b"")
        buffer = bytearray(b"".join(replies))
        self.assertEqual(bridge.take_frames(buffer), expected_stream)
        self.assertEqual(buffer, b"")

    def test_unsolicited_state_deduplication_and_crc_recovery(self):
        mode = rx_buds2(0x03, 0xe0, "01 07 00 02 01 00")
        latency = next(raw for _, flow, raw in BUDS2_ROWS
                       if flow == "<-" and raw[:5] == bytes.fromhex("55 20 01 41 40")
                       and raw[8] == 1)
        self.feed(mode + latency)
        self.assertEqual(self.lines[-1]["mode"], "ambient")
        self.assertTrue(self.lines[-1]["latency"])
        count = len(self.lines)
        self.feed(mode + latency)
        self.assertEqual(len(self.lines), count)
        damaged = bytearray(rx_buds2(0x03, 0xe0, "01 03 00 02 03 00"))
        damaged[-1] ^= 0xff  # Synthetic CRC damage, not owner evidence.
        self.feed(damaged)
        self.assertEqual(len(self.lines), count)
        self.feed(rx_buds2(0x03, 0xe0, "01 02 00 02 02 00"))
        self.assertEqual(self.lines[-1]["ancLevel"], "mid")
        self.assertEqual(len(self.lines), count + 1)

    def test_buds2_controls_and_case_battery(self):
        self.feed(rx_buds2(0x1e, 0x40, "01 05 00 02 01 00"))
        self.feed(rx_buds2(0x07, 0x40, "02 02 64 03 64"))
        self.feed(rx_buds2(0x41, 0x40, "02"))
        self.assertEqual(self.lines[-1]["mode"], "off")
        self.assertEqual(self.lines[-1]["ancLevel"], "high")
        self.assertEqual(self.lines[-1]["battery"]["left"], 100)
        self.assertEqual(self.lines[-1]["battery"]["right"], 100)
        self.assertNotIn("case", self.lines[-1]["battery"])

        # Case battery event when case is opened
        self.feed(rx_buds2(0x01, 0xe0, "03 02 64 03 64 04 55"))
        self.assertEqual(self.lines[-1]["battery"]["case"], 85)
        self.assertFalse(self.lines[-1]["battery"]["caseStale"])

        # Case closed subsequent query preserves case level with caseStale
        self.feed(rx_buds2(0x07, 0x40, "02 02 64 03 64"))
        self.assertEqual(self.lines[-1]["battery"]["case"], 85)
        self.assertTrue(self.lines[-1]["battery"]["caseStale"])

        cases = [
            ("set ambient", "01 07 00", "01 07 00 02 01 00", "ambient", "high"),
            ("set anc", "01 01 00", "01 01 00 02 01 00", "anc", "high"),
            ("level adaptive", "01 04 00", "01 04 00 02 04 00", "anc", "adaptive"),
            ("level low", "01 03 00", "01 03 00 02 03 00", "anc", "low"),
            ("level mid", "01 02 00", "01 02 00 02 02 00", "anc", "mid"),
            ("level high", "01 01 00", "01 01 00 02 01 00", "anc", "high"),
            ("set off", "01 05 00", "01 05 00 02 01 00", "off", "high"),
            ("latency on", "01", "01", "off", "high"),
            ("latency off", "02", "02", "off", "high"),
        ]
        for command, outgoing, answer, mode, level in cases:
            with self.subTest(command=command):
                latency = command.startswith("latency")
                cmd = 0x40 if latency else 0x0f
                before = len(self.lines)
                self.device.command(command)
                sent = self.device.sock.sent[-1]
                self.assertIn(sent, [raw for _, flow, raw in BUDS2_ROWS if flow == "->"])
                self.assertEqual(sent[3:5], bytes((cmd, 0xf0)))
                self.assertEqual(sent[8:-2], bytes.fromhex(outgoing))
                self.feed(rx_buds2(cmd, 0x70, "00" if not latency else "01" if command.endswith("on") else "02"))
                self.assertEqual(len(self.lines), before)
                self.assertIsNotNone(self.device.readback_at)
                self.device.readback()
                self.feed(rx_buds2(0x41 if latency else 0x1e, 0x40, answer))
                self.assertEqual(self.lines[-1]["mode"], mode)
                self.assertEqual(self.lines[-1]["ancLevel"], level)
                if latency:
                    self.assertEqual(self.lines[-1]["latency"], command.endswith("on"))


class ChannelRegression(unittest.TestCase):
    def setUp(self):
        # CI Python may omit Bluetooth constants. All sockets here are fake;
        # supply only the constants needed to exercise connection/CLI logic.
        constants = patch.multiple(bridge.socket, AF_BLUETOOTH=31,
                                   BTPROTO_RFCOMM=3, create=True)
        constants.start()
        self.addCleanup(constants.stop)

    def connect(self, name, successes):
        attempts, sockets = [], []
        class Candidate(Socket):
            def __init__(self, *_):
                super().__init__()
                sockets.append(self)
            def settimeout(self, _): pass
            def setblocking(self, _): pass
            def connect(self, target):
                channel = target[1]
                attempts.append(channel)
                if (channel, attempts.count(channel)) not in successes:
                    raise OSError(111, "Connection refused")
        with patch.object(bridge.socket, "socket", Candidate), \
             patch.object(bridge.time, "sleep") as sleep, \
             patch.object(bridge, "read_case_cache", return_value=None), \
             patch.object(bridge, "emit"):
            device = bridge.Bridge("2C:BE:EE:3C:6F:FE", name)
            result = device.connect()
        return device, result, attempts, sockets, sleep.call_count

    def test_known_earbuds_never_contact_28_even_when_15_initially_refuses(self):
        for name in ("Nothing Ear (a)", "Ear (a)", " NOTHING EAR (A) ", ""):
            with self.subTest(name=name):
                device, ok, tried, sockets, sleeps = self.connect(name, {(15, 2), (28, 1)})
                self.assertTrue(ok)
                self.assertEqual(tried, [15, 15])
                self.assertEqual(device.channel, 15)
                self.assertTrue(sockets[0].closed)
                self.assertFalse(sockets[1].closed)
                self.assertEqual(sleeps, 1)

    def test_cmf_uses_only_28_including_retry(self):
        device, ok, tried, _, _ = self.connect("CMF Headphone Pro", {(15, 1), (28, 2)})
        self.assertTrue(ok)
        self.assertEqual(tried, [28, 28])
        self.assertEqual(device.channel, 28)

    def test_cmf_buds_2_uses_only_16_including_retry(self):
        for name in ("CMF Buds 2", "Nothing CMF Buds 2", "buds 2", " CMF BUDS 2 "):
            with self.subTest(name=name):
                device, ok, tried, sockets, sleeps = self.connect(name, {(15, 1), (16, 2)})
                self.assertTrue(ok)
                self.assertEqual(tried, [16, 16])
                self.assertEqual(device.channel, 16)
                self.assertTrue(sockets[0].closed)
                self.assertFalse(sockets[1].closed)
                self.assertEqual(sleeps, 1)

    def test_unknown_model_keeps_wider_discovery(self):
        device, ok, tried, _, _ = self.connect("Future NT Link", {(28, 1)})
        self.assertTrue(ok)
        self.assertEqual(tried, [15, 28])
        self.assertEqual(device.channel, 28)

    def test_failure_exhausts_original_retry_count_and_closes_every_socket(self):
        device, ok, tried, sockets, sleeps = self.connect("Nothing Ear (a)", set())
        self.assertFalse(ok)
        self.assertEqual(tried, [15] * 6)
        self.assertTrue(all(sock.closed for sock in sockets))
        self.assertEqual(sleeps, 5)
        self.assertEqual(device.exit_code, bridge.EXIT_TRANSIENT)


class Lifecycle(unittest.TestCase):
    def run_device(self, incoming=()):
        sock, lines, clock = Socket(incoming), [], [0.0]
        with patch.object(bridge, "read_case_cache", return_value=None):
            device = bridge.Bridge("2C:BE:EE:3C:6F:FE", "CMF Headphone Pro")
        def connect():
            device.sock = sock
            return True
        def select(readers, *_):
            clock[0] += 1
            if clock[0] > 20:
                raise AssertionError("loop failed to terminate")
            return ([sock] if sock.incoming else [], [], [])
        with patch.object(device, "connect", connect), \
             patch.object(bridge, "emit", lines.append), \
             patch.object(bridge.time, "monotonic", lambda: clock[0]), \
             patch.object(bridge.select, "select", select):
            result = device.run()
            device.close()
        return device, sock, lines, result

    def test_silence_really_expires_in_run_and_sends_queries_after_info_timeout(self):
        device, sock, lines, result = self.run_device()
        self.assertEqual(result, bridge.EXIT_UNSUPPORTED)
        self.assertEqual([raw[3] for raw in sock.sent[:4]], [0x06, 0x07, 0x1e, 0x41])
        self.assertFalse(device.info_seen)
        self.assertEqual(len(lines), 1)
        self.assertFalse(lines[0]["modes"])
        self.assertTrue(sock.closed)
        self.assertIsNone(device.sock)

    def test_eof_is_transient_and_fresh_bridge_recovers_from_its_own_reports(self):
        first, old_sock, old_lines, result = self.run_device([
            rx(0x1e, 0x40, "01 01 00 02 01 00"), b""])
        self.assertEqual(result, bridge.EXIT_TRANSIENT)
        self.assertTrue(old_sock.closed)
        self.assertIsNone(first.sock)
        snapshot = list(old_lines)
        second, _, lines, _ = self.run_device([
            rx(0x07, 0x40, "01 06 0f"),
            rx(0x1e, 0x40, "01 07 00 02 04 00"), b""])
        self.assertEqual(lines[0]["mode"], "ambient")
        self.assertEqual(lines[0]["battery"]["headset"], 15)
        self.assertEqual(second.level, "adaptive")
        self.assertEqual(first.level, "high")
        self.assertEqual(old_lines, snapshot)


class CommandLine(unittest.TestCase):
    def setUp(self):
        # CI Python may omit Bluetooth constants. All sockets here are fake;
        # supply only the constants needed to exercise connection/CLI logic.
        constants = patch.multiple(bridge.socket, AF_BLUETOOTH=31,
                                   BTPROTO_RFCOMM=3, create=True)
        constants.start()
        self.addCleanup(constants.stop)

    def test_optional_model_name_reaches_bridge_and_old_call_is_nameless(self):
        address = "2C:BE:EE:3C:6F:FE"
        for tail in ([], ["CMF Headphone Pro"], ["Nothing Ear (a)"], ["CMF Buds 2"]):
            with self.subTest(args=tail), \
                 patch.object(bridge.sys, "argv", ["nothing-bridge", address] + tail), \
                 patch.object(bridge.signal, "signal"), \
                 patch.object(bridge, "Bridge") as factory:
                factory.return_value.run.return_value = 0
                self.assertEqual(bridge.main(), 0)
                factory.assert_called_once_with(address, tail[0] if tail else "")
                factory.return_value.close.assert_called_once_with()

    def test_probe_defaults_to_15_and_cmf_explicitly_selects_channel(self):
        probe = harness.load_bridge("tools/nothing_probe.py")
        address = "2C:BE:EE:3C:6F:FE"
        for args, channel in (([address], 15), (["--channel", "16", address], 16), (["--channel", "28", address], 28)):
            with self.subTest(args=args), \
                 patch.object(probe.sys, "argv", ["nothing_probe.py"] + args), \
                 patch.object(probe.socket, "socket") as factory, \
                 patch.object(probe, "listen"), patch.object(probe, "send"), \
                 patch("builtins.print"):
                self.assertEqual(probe.main(), 0)
                factory.return_value.connect.assert_called_once_with((address, channel))
                factory.return_value.close.assert_called_once_with()
