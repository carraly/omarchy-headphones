"""New pure codecs against frozen legacy fixtures; synthetic faults labelled here.

This is automated migration coverage, not new hardware evidence. Original owner
pins still run through their original bridges. Soundcore command state and
Nothing's unobserved default strength intentionally differ on the new path.
"""
import importlib
import json
import unittest
from copy import deepcopy

from omaphones.registry import ROOT, get_adapter, load_protocol
from omaphones.testing import Replay
from omaphones.api import Event
from omaphones.state import legacy_command

PINS = {'samsung': 'galaxy-buds2', 'xiaomi': 'buds-5-pro', 'oppo': 'enco-air3-pro', 'bose': 'qc45',
        'nothing': 'headphone-pro', 'soundcore': 'space-2'}


def pin_for(brand):
    return json.loads((ROOT/'tests/pins'/brand/(PINS[brand]+'.json')).read_text())


def wire(brand, value):
    if isinstance(value, str): return bytes.fromhex(value)
    old = importlib.import_module('tests.' + brand + '_bridge_test').bridge_module
    if brand == 'nothing':
        return old.frame(int(value['cmd'], 16), int(value['dir'], 16), bytes.fromhex(value.get('payload', '')))
    body = bytes.fromhex(value.get('body', ''))
    cmd = bytes.fromhex(value['cmd'])
    packet = bytes.fromhex('09 ff 00 00 00') + cmd + (10+len(body)).to_bytes(2, 'little') + body
    return packet + bytes([sum(packet) & 255])


def new(brand):
    context = {'name': 'Migration fixture'}
    row = get_adapter(brand)
    if brand == 'soundcore':
        row = {**row, 'unknownModel': {'offset': 71, 'query': False}}
    return Replay(load_protocol(row, context))


class NativeCodecs(unittest.TestCase):
    def test_four_codecs_preserve_frozen_wire_and_observed_state(self):
        for brand in ('samsung', 'xiaomi', 'oppo', 'bose'):
            with self.subTest(brand=brand):
                replay = new(brand)
                for step in pin_for(brand)['steps']:
                    if 'call' in step:
                        method = {'open': 'opened' if brand == 'bose' else 'connected', 'ask_mode': 'ask_mode'}[step['call']]
                        # Original Bose pin begins after verified endpoint setup.
                        adapter = replay.session.adapter
                        adapter.connected = getattr(adapter, method)
                        replay.event('connected')
                    elif 'device' in step: replay.receive(wire(brand, step['device']))
                    elif 'command' in step:
                        key, value = legacy_command(step['command'])
                        previous = deepcopy(replay.session.state.values)
                        replay.command(key, value)
                        self.assertEqual(replay.session.state.values, previous)
                    elif 'fire' in step:
                        # Owner pin fires one poll, not every pending callback.
                        adapter = replay.session.adapter
                        token = next(t for t, (method, _) in adapter._timers.items() if method == step['fire'])
                        replay.fire(token)
                    elif 'sent' in step: self.assertEqual(replay.sent, step['sent'])
                    elif 'sent_last' in step: self.assertEqual(replay.sent[-1], step['sent_last'])
                    elif 'line' in step: self.assertEqual(replay.session.state.legacy(), step['line'])
                    elif 'line_has' in step:
                        for k, v in step['line_has'].items(): self.assertEqual(replay.session.state.legacy()[k], v)
                    elif 'exit' in step: self.assertEqual(replay.session.exit_code, step['exit'])
                self.assertIsNone(replay.session.exit_code, replay.session.message)

    def test_all_six_stream_codecs_accept_every_split_and_coalesced_fixture(self):
        for brand in PINS:
            frames = [wire(brand, s['device']) for s in pin_for(brand)['steps'] if 'device' in s]
            data = b''.join(frames)
            expected = new(brand)
            if brand == 'bose': expected.session.adapter.probing = False
            expected.receive(data)
            self.assertIsNone(expected.session.exit_code, expected.session.message)
            self.assertIn('noise.mode', expected.session.state.values)
            for boundary in range(1, len(data)):
                replay = new(brand)
                if brand == 'bose': replay.session.adapter.probing = False
                replay.receive(data[:boundary]); replay.receive(data[boundary:])
                self.assertEqual(replay.session.state.values, expected.session.state.values, (brand, boundary, replay.session.message))
                self.assertEqual(replay.sent, expected.sent, (brand, boundary))

    def test_silence_disconnect_and_unsupported_commands(self):
        for brand in PINS:
            replay = new(brand); replay.event('connected')
            sent = list(replay.sent)
            replay.command('noise.mode', 'anc')
            self.assertEqual(replay.sent, sent)
            replay.advance(12000)
            if brand == 'bose': self.assertEqual(replay.endpoint_changes, 1)
            else: self.assertIn(replay.session.exit_code, (1, 3), (brand, replay.session.message))
            replay = new(brand); replay.event('connected'); replay.event('disconnected', 'synthetic link loss')
            sent = list(replay.sent); replay.advance(12000); replay.receive(b'late')
            self.assertEqual(replay.sent, sent)
            self.assertEqual(replay.session.exit_code, 1)
            self.assertEqual(replay.clock.pending, {})

    def test_soundcore_commands_leave_observed_parameters_unchanged(self):
        replay = new('soundcore')
        first = next(s['device'] for s in pin_for('soundcore')['steps'] if 'device' in s)
        replay.receive(wire('soundcore', first))
        previous = deepcopy(replay.session.state.values)
        params = list(replay.session.adapter.params)
        replay.command('ambient.level', 5); replay.command('noise.wind_reduction', True)
        self.assertEqual(replay.session.adapter.params, params)
        self.assertEqual(replay.session.state.values, previous)
        replay.receive(wire('soundcore', {'cmd': '06 01', 'body': '01 1f ff 00 01 05'}))
        self.assertEqual(replay.session.state.values['ambient.level'], 5)
        self.assertIs(replay.session.state.values['noise.wind_reduction'], True)

    def test_soundcore_life_q30_four_byte_block_matches_its_pin(self):
        """The owner's frozen Life Q30 frames through the native codec: the mode
        from the four-byte block, no level or wind switch, and a set that posts
        the four observed bytes back with only the mode changed."""
        pin = json.loads((ROOT/'tests/pins/soundcore/life-q30.json').read_text())
        devices = [s['device'] for s in pin['steps'] if 'device' in s]
        row = get_adapter('soundcore')
        context = {'name': 'reported name', 'uuids': ['0cf12d31-fac3-4553-bd80-d6832e7b302a']}
        replay = Replay(load_protocol(row, context))
        replay.receive(wire('soundcore', devices[0]))
        self.assertEqual(replay.session.state.values, {'noise.mode': 'anc'})
        self.assertEqual(replay.sent, ['08 ee 00 00 00 06 01 0a 00 07'])
        replay.receive(wire('soundcore', devices[1]))
        replay.command('ambient.level', 3); replay.command('noise.wind_reduction', True)
        self.assertEqual(len(replay.sent), 1)
        replay.command('noise.mode', 'ambient')
        self.assertEqual(replay.sent[-1], '08 ee 00 00 00 06 81 0e 00 01 01 00 00 8d')
        self.assertEqual(replay.session.state.values, {'noise.mode': 'anc'})
        replay.receive(wire('soundcore', devices[3]))
        self.assertEqual(replay.session.state.values, {'noise.mode': 'ambient'})

    def test_soundcore_bad_checksum_zero_length_and_partial_record_are_not_state(self):
        replay = new('soundcore')
        first = wire('soundcore', next(s['device'] for s in pin_for('soundcore')['steps'] if 'device' in s))
        broken = bytearray(first); broken[-1] ^= 255
        replay.receive(broken)
        replay.receive(bytes.fromhex('09 ff 00 00 00 06 01 00 00'))
        replay.receive(wire('soundcore', {'cmd': '06 01', 'body': '01'}))
        self.assertEqual(replay.session.state.values, {})
        replay.receive(first)
        self.assertEqual(replay.session.state.values['noise.mode'], 'ambient')

    def test_nothing_reports_strength_only_when_observed(self):
        replay = new('nothing')
        replay.receive(wire('nothing', {'cmd': '1e', 'dir': '40', 'payload': '01 07 00'}))
        self.assertNotIn('anc.strength', replay.session.state.values)
        replay.command('anc.strength', 'low')
        self.assertEqual(replay.sent, [])
        replay.receive(wire('nothing', {'cmd': '1e', 'dir': '40', 'payload': '01 01 00 02 01 00'}))
        replay.command('anc.strength', 'low')
        self.assertEqual(replay.session.state.values['anc.strength'], 'high')
        self.assertEqual(replay.session.adapter.level, 'high')

    def test_bose_endpoint_requires_version_reply_before_mode_queries(self):
        replay = new('bose'); replay.event('connected')
        self.assertEqual(replay.sent, ['00 01 01 00'])
        replay.receive('00 01 03 05 31 2e 31 2e 30')
        self.assertEqual(replay.sent, ['00 01 01 00', '02 02 01 00', '1f 03 01 00'])
        replay.advance(1000)
        self.assertEqual(replay.endpoint_changes, 0)
