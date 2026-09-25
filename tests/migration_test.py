"""Owner migration workflow and legacy endpoint regression checks; no radio."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from omaphones import migration, owner_recording as recording
from omaphones.registry import get_adapter, model_parameters, transport_for
from tests.owner_recording_test import INFO, fixture


class MigrationTests(unittest.TestCase):
    def test_nothing_keeps_each_known_channel_and_unknown_widening(self):
        from tests.nothing_bridge_test import bridge_module as old
        row = get_adapter('nothing')
        for name in ['', 'unknown', *old.MODELS, ' Nothing Ear (a) ']:
            with self.subTest(name=name):
                self.assertEqual(transport_for(row, {'name': name})['channels'], list(old.model_for(name)['channels']))
                self.assertEqual(model_parameters(row, {'name': name})['defaultAncLevel'], 'adaptive')

    def test_soundcore_selects_observed_vendor_uuid_and_owner_parameters(self):
        from tests.soundcore_bridge_test import bridge_module as old
        row = get_adapter('soundcore')
        for suffix in [*old.MODELS, 'fffff']:
            uuid = old.SOUNDCORE_UUID_PREFIX + suffix
            context = {'name': 'reported name', 'uuids': [uuid.upper()]}
            expected = old.model_for(uuid)
            self.assertEqual(model_parameters(row, context), {key: expected[key] for key in ('offset', 'width', 'query') if key in expected})
            self.assertEqual(transport_for(row, context)['uuidPreference'], [uuid])

    def test_migration_refuses_disconnected_or_wrong_brand(self):
        with self.assertRaisesRegex(ValueError, 'connect'):
            migration.prepare('sony', INFO)
        with self.assertRaisesRegex(ValueError, 'identity'):
            migration.prepare('sony', INFO + '\tConnected: yes\n')

    def test_sony_battery_only_policy_is_explicit(self):
        params = model_parameters(get_adapter('sony'), {'name': 'WH-CH520'})
        self.assertEqual(params, {'wear': False, 'noModes': True})
        self.assertEqual(model_parameters(get_adapter('sony'), {'name': 'WH-CH720N'}), {'wear': False})

    def test_failed_write_is_logged_as_failure_not_owner_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'source'; fixture(directory)
            (directory / 'manifest.json').unlink()
            class Client:
                def set(self, *args): raise TimeoutError('no reply')
            client = migration.TimelineClient(Client(), directory)
            with self.assertRaises(TimeoutError): client.set('command', 'noise.mode', 'anc')
            import csv
            with (directory / 'actions.csv').open(newline='') as source: rows = list(csv.DictReader(source))
            self.assertEqual([row['kind'] for row in rows[-2:]], ['action', 'failure'])
            self.assertEqual(rows[-1]['description'], 'no reply')

    def exercise(self, failure=None, incomplete=False):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        directory = Path(temp.name) / 'source'; fixture(directory)
        (directory / 'manifest.json').unlink()
        context = recording.read_json(directory / 'session.json')['device']
        caps = {'noise.mode': {'values': ['off', 'anc']}}
        if incomplete:
            caps['ambient.level'] = {'min': 0, 'max': 20, 'step': 1}
        class Client:
            def __init__(self):
                self.state = {'apiVersion': 1, 'values': {'noise.mode': 'off'}, 'capabilities': caps}
                self.sent = []; self.closed = False
            def wait(self, predicate):
                if not predicate(self.state): raise TimeoutError('missing initial')
                return json.loads(json.dumps(self.state))
            def set(self, command, field, value):
                self.sent.append((field, value))
                if value == 'anc' and failure: raise failure
                self.state['values'][field] = value
                return json.loads(json.dumps(self.state))
            def close(self): self.closed = True
        client = Client()
        with patch('omaphones.migration.subprocess.check_output', return_value=INFO), \
             patch('omaphones.migration.prepare', return_value=(context, {}, {})), \
             patch('omaphones.migration.recording.revision', return_value={'commit': 'synthetic', 'codeSha256': 'test', 'dirty': False}), \
             patch('omaphones.migration.live.Client', return_value=client):
            report = migration.run('synthetic', directory, interactive=False)
        self.assertEqual(json.loads((directory / 'adapter-result.json').read_text()), report)
        self.assertTrue(client.closed)
        return report, client

    def test_control_failure_and_interruption_restore_initial_state(self):
        for error in (TimeoutError('lost reply'), KeyboardInterrupt('interrupted')):
            with self.subTest(error=error):
                report, client = self.exercise(error)
                self.assertFalse(report['passed'])
                self.assertEqual(client.sent, [('noise.mode', 'anc'), ('noise.mode', 'off')])
                self.assertTrue(report['restoration'][0]['passed'])

    def test_unknown_initial_prevents_all_writes(self):
        report, client = self.exercise(incomplete=True)
        self.assertFalse(report['passed'])
        self.assertEqual(client.sent, [])

    def test_automatic_success_keeps_manual_checks_explicitly_untested(self):
        report, client = self.exercise()
        self.assertTrue(report['passed'])
        self.assertIn('external-change', report['untested'])
        self.assertIn('shell-integration', report['untested'])
        self.assertEqual(client.state['values']['noise.mode'], 'off')
