"""Unmodified owner pins replayed through API v1 and host-owned state/timers."""
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

from tests import harness, canonical
from omaphones.api import Event, Report, Protocol, Send
from omaphones.platform import GLibClock
from omaphones.registry import ROOT, descriptors, get_adapter, load_protocol, model_parameters, select, transport_for
from omaphones.session import Session
from omaphones.state import State, legacy_command
from omaphones.transports import GattTransport


class Clock:
    def __init__(self):
        self.now = 0
        self.serial = 0
        self.pending = {}

    def schedule(self, ms, callback):
        self.serial += 1
        self.pending[self.serial] = (self.now + ms, ms, callback)
        return self.serial

    def cancel(self, token):
        self.pending.pop(token, None)

    def advance(self, ms):
        end = self.now + ms
        while self.pending:
            token, (at, _, callback) = min(self.pending.items(), key=lambda item: (item[1][0], item[0]))
            if at > end:
                break
            self.pending.pop(token)
            self.now = at
            callback()
        self.now = end

    def fire(self):
        pending, self.pending = self.pending, {}
        for token, (_at, _ms, callback) in pending.items():
            callback()


class MemoryTransport:
    def __init__(self):
        self.frames = []
        self.closed = False

    def write(self, data):
        if self.closed:
            raise AssertionError("write after close")
        self.frames.append(bytes(data))

    def close(self):
        self.closed = True


class SonyReplay:
    def __init__(self, uuid="v2", name=""):
        self.row = get_adapter("sony")
        self.bridge = load_protocol(self.row, {"name": name})
        self.bridge.linked = True  # Old owner pins begin after transport open.
        self.clock = Clock()
        self.transport = MemoryTransport()
        self.lines = []
        self.session = Session(self.bridge, self.transport, self.clock,
                               lambda state: self.lines.append(state.legacy()), self.ended)
        self.frames = self.transport.frames

    def ended(self, code, message):
        if message:
            self.lines.append({"modes": False, "error": message})

    def receive(self, data):
        self.session.dispatch(Event("received", data))

    def device(self, spec):
        from tests.sony_bridge_test import bridge_module as old
        self.receive(bytes.fromhex(spec["wire"]) if isinstance(spec, dict) else old.encode(old.DATA_MDR, 0, bytes.fromhex(spec)))

    def ack(self):
        self.receive(bytes.fromhex("3e010100000000023c"))

    def command(self, line):
        command = legacy_command(line)
        if command:
            key, value = command
            self.session.dispatch(Event("command", value, key))

    def fire(self):
        self.clock.fire()

    @property
    def sent(self):
        from tests.sony_bridge_test import bridge_module as old
        return [harness.hexstr(decoded[2]) for frame in self.frames if (decoded := old.decode(frame)) and decoded[0] == old.DATA_MDR]

    @property
    def timers(self):
        return [ms for _at, ms, _callback in self.clock.pending.values()]

    @property
    def exit_code(self):
        return self.session.exit_code

    def state(self, name):
        return getattr(self.bridge, name)


harness.pin_tests(globals(), "sony-bridge", SonyReplay)


class GattGLib:
    PRIORITY_DEFAULT = 0
    IO_IN, IO_OUT, IO_HUP, IO_ERR = 1, 4, 16, 8

    def __init__(self, clock):
        self.clock = clock
        self.watchers = {}
        self.serial = -1

    def timeout_add(self, ms, callback):
        return self.clock.schedule(ms, callback)

    def source_remove(self, token):
        self.clock.cancel(token)
        self.watchers.pop(token, None)

    def io_add_watch(self, fd, priority, conditions, callback):
        self.serial -= 1
        token = self.serial
        def run(*args):
            keep = callback(*args)
            if not keep:
                self.watchers.pop(token, None)
            return keep
        self.watchers[token] = (fd, conditions, run)
        return token


class FakePipe:
    def __init__(self, lines):
        self.lines = lines
    def write(self, value):
        self.lines.append(value.decode().rstrip('\n'))
    def flush(self):
        pass
    def fileno(self):
        return -1
    def close(self):
        pass


class JblReplay:
    def __init__(self, model_id=""):
        self.clock = Clock()
        self.glib = GattGLib(self.clock)
        self.lines = []
        self.frames = []
        self.row = get_adapter("jbl")
        # The pin's Fast Pair model id picks the handles, as it does for jbl-bridge.
        context = {"modelId": model_id} if model_id else {}
        self.bridge = load_protocol(self.row, context)
        self.transport = GattTransport(transport_for(self.row, context), {"bleAddress": "48:B4:41:00:00:01"}, self.glib)
        pipe = FakePipe(self.frames)
        self.client = types.SimpleNamespace(stdin=pipe, stdout=pipe, wait=lambda **kwargs: 0)
        self.session = Session(self.bridge, self.transport, self.clock, lambda state: self.lines.append(state.legacy()), self.ended)
        self.transport.deliver = self.session.dispatch
        with patch('omaphones.transports.subprocess.Popen', return_value=self.client), patch('omaphones.transports.os.set_blocking'):
            self.transport.start()

    def ended(self, code, message):
        if message:
            self.lines.append({"modes": False, "error": message})

    def device(self, line):
        self.transport.line(line)

    def command(self, line):
        command = legacy_command(line)
        if command:
            key, value = command
            self.session.dispatch(Event("command", value, key))

    def call(self, name, *args):
        if name == 'start':
            return  # v1 starts on discovery, instead of a separate thread.
        if name == 'wait_start':
            return
        raise AssertionError(name)

    def wait_sent(self, count):
        for _ in range(100):
            if len(self.frames) >= count:
                return
            if not self.clock.pending:
                break
            at = min(v[0] for v in self.clock.pending.values())
            self.clock.advance(at - self.clock.now)
        raise AssertionError('not enough writes')

    @property
    def sent(self):
        return list(self.frames)

    @property
    def exit_code(self):
        return self.session.exit_code

    def state(self, name):
        if name == 'start_code':
            return self.session.exit_code
        return getattr(self.bridge, name)

    def close(self):
        self.session.finish(0)


harness.pin_tests(globals(), "jbl-bridge", JblReplay)


class RuntimeBehavior(unittest.TestCase):
    def sony(self):
        s = SonyReplay(name='WH-CH720N')
        s.receive(canonical.frame('sony', 'rx', 'handshake'))
        s.ack()
        s.receive(canonical.frame('sony', 'rx', 'initial'))
        return s

    def test_every_split_damage_glued_frames_and_real_ack(self):
        wire = canonical.frame('sony', 'rx', 'ambient')
        for split in range(1, len(wire)):
            s = self.sony()
            before = list(s.frames)
            s.receive(wire[:split])
            self.assertEqual(s.frames, before)
            self.assertEqual(s.lines[-1]['mode'], 'anc')
            s.receive(wire[split:])
            self.assertEqual(s.frames[-1].hex(), '3e010100000000023c')
            self.assertEqual(s.lines[-1]['mode'], 'ambient')
        s = self.sony()
        bad = bytearray(wire); bad[-2] ^= 1  # Synthetic checksum damage.
        before = list(s.frames)
        s.receive(bad)
        self.assertEqual(s.frames, before)
        s.receive(b'noise' + wire + wire + canonical.frame('sony', 'rx', 'off'))
        self.assertEqual([line['mode'] for line in s.lines], ['anc', 'ambient', 'off'])

    def test_no_optimistic_state_or_unsupported_writes_and_session_isolation(self):
        a, b = self.sony(), self.sony()
        before = list(b.frames)
        a.command('level 5')
        self.assertEqual(a.lines[-1]['level'], 14)
        a.receive(canonical.frame('sony', 'rx', 'level5'))
        self.assertEqual(a.lines[-1]['level'], 5)
        self.assertEqual(b.lines[-1]['level'], 14)
        self.assertEqual(b.frames, before)
        for command in ('set talkthru', 'level nan', 'voice maybe', 'latency on'):
            b.command(command)
        self.assertEqual(b.frames, before)
        a.session.dispatch(Event('disconnected', 'lost'))
        a.clock.advance(100000)
        a.ack()
        self.assertEqual(a.exit_code, 1)
        self.assertEqual(a.clock.pending, {})
        self.assertIsNone(b.exit_code)

    def test_handshake_deadlines_preserve_exact_frames_and_transient_exit(self):
        s = SonyReplay(name='WH-CH720N')
        s.session.dispatch(Event('connected'))
        s.clock.advance(6000)
        self.assertEqual(s.sent, ['00 00'] * 3)
        self.assertEqual(s.exit_code, 1)
        self.assertEqual(s.clock.pending, {})

    def test_ack_timeout_and_stale_timer_do_not_free_new_command(self):
        s = self.sony()
        s.command('set off')
        serial = s.bridge.sent
        s.command('set ambient')
        self.assertEqual(s.sent[-1], '68 17 01 00 00 00 0e')
        s.clock.advance(3000)
        self.assertEqual(s.sent[-1], '68 17 01 01 01 00 0e')
        s.bridge.on_ack_timeout(serial)
        self.assertTrue(s.bridge.waiting)

    def test_jbl_timing_silence_and_first_exit(self):
        s = JblReplay(); self.addCleanup(s.close)
        s.device('GATT discovery procedures complete')
        s.device('Registered notify handler')
        self.assertEqual(s.sent, ['register-notify 0x000c'])
        s.clock.advance(1999)
        self.assertEqual(len(s.sent), 1)
        s.clock.advance(1)
        self.assertEqual(len(s.sent), 2)
        s.clock.advance(2000)
        self.assertEqual(len(s.sent), 3)
        s.clock.advance(11999)
        self.assertIsNone(s.exit_code)
        s.clock.advance(1)
        self.assertEqual(s.exit_code, 3)
        s.session.finish(0)
        self.assertEqual(s.exit_code, 3)
        self.assertEqual(s.clock.pending, {})

    def test_jbl_stop_at_each_stage_cancels_later_writes(self):
        for stage in (0, 1, 2, 3):
            s = JblReplay()
            if stage >= 1:
                s.device('GATT discovery procedures complete')
            if stage >= 2:
                s.device('Registered notify handler')
            if stage >= 3:
                s.clock.advance(2000)
            s.close()
            before = s.sent
            s.clock.advance(100000)
            s.device('Registered notify handler')
            self.assertEqual(s.sent, before)
            self.assertEqual(s.exit_code, 0)

    def test_jbl_subscription_fallback_transport_failure_and_report_timeout_race(self):
        s = JblReplay(); self.addCleanup(s.close)
        s.device('GATT discovery procedures complete')
        s.clock.advance(5000)
        self.assertEqual(len(s.sent), 1)
        s.clock.advance(4000)
        self.assertEqual(len(s.sent), 3)
        s.device('Handle Value Not/Ind: 0x000c - (10 data bytes): aa 91 07 12 01 01 02 00 03 00')
        s.clock.advance(12000)
        self.assertIsNone(s.exit_code)
        for line in ('GATT discovery procedures failed', 'Failed to register notify handler', 'Device disconnected'):
            failed = JblReplay(); self.addCleanup(failed.close)
            failed.device(line)
            self.assertEqual(failed.exit_code, 1)
            failed.clock.advance(100000)
            self.assertEqual(failed.exit_code, 1)

    def test_invalid_report_is_atomic_and_stops_only_its_session(self):
        state = State()
        state.apply(Report({'noise.mode': 'anc'}, {'noise.mode': {'values': ['off', 'anc']}}))
        with self.assertRaises(ValueError):
            state.apply(Report({'noise.mode': 'ambient'}))
        self.assertEqual(state.values, {'noise.mode': 'anc'})
        self.assertFalse(state.accepts('noise.mode', 'ambient'))
        self.assertFalse(state.accepts('noise.mode', True))

    def test_sensor_is_read_only_and_battery_shape_is_validated(self):
        state = State()
        state.apply(Report({'wear.detected': True, 'battery': {'headset': 50}}, {'wear.detected': {'type': 'boolean', 'readOnly': True}, 'battery': {'readOnly': True}}))
        self.assertFalse(state.accepts('wear.detected', False))
        with self.assertRaises(ValueError):
            state.apply(Report({'battery': {'headset': 150}}))


class RegistryBehavior(unittest.TestCase):
    def test_known_model_parameters_equal_frozen_legacy_rows(self):
        from tests.sony_bridge_test import bridge_module as old
        row = get_adapter('sony')
        for name, params in old.MODELS.items():
            self.assertEqual(model_parameters(row, {'name': name}), params)
        self.assertEqual(model_parameters(row, {}), old.NAMELESS)
        self.assertEqual(model_parameters(row, {'name': 'unseen'}), old.UNKNOWN)

    def test_routing_preserves_uuid_precedence_and_ble_fallback(self):
        rows = descriptors()
        self.assertEqual([r['id'] for r in rows], ['sony','samsung','nothing','xiaomi','soundcore','oppo','bose','tozo','jbl'])
        for row in rows:
            match = row['match']
            uuids = match.get('uuids', [match['uuidPrefix'] + '042'] if match.get('uuidPrefix') else [])
            if uuids:
                self.assertEqual(select(uuids, 'AA:BB:CC:DD:EE:FF'), row['id'])
        self.assertEqual(select([], 'AA:BB:CC:DD:EE:FF'), 'jbl')
        self.assertEqual(select([]), '')
