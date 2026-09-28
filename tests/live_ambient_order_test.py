"""Owner check order: ambient.* controls are exercised in Ambient.

Synthetic device, not captured evidence. It behaves as the WH-CH720N did on
2026-09-28 through the widget's own IPC: in ANC a Focus on voice write was
acknowledged and ignored (voice stayed on, also after returning to Ambient),
and a level write is an ambient SET, so it switches the headset to Ambient.
"""
import io
import json
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from omaphones import live

PROFILE = {"id": "synthetic-sony", "owner": "synthetic", "capabilities": {
    "noise.mode": {"values": ["off", "anc", "ambient"]},
    "ambient.level": {"min": 0, "max": 20, "step": 1},
    "ambient.focus_on_voice": {"type": "boolean"},
}}


class AmbientOnlySony:
    def __init__(self, mode, level, voice):
        self.state = {"apiVersion": 1, "capabilities": PROFILE["capabilities"],
                      "values": {"noise.mode": mode, "ambient.level": level,
                                 "ambient.focus_on_voice": voice}}
        self.sent = []
        self.closed = False

    def snapshot(self):
        return json.loads(json.dumps(self.state))

    def wait(self, predicate, **_):
        if not predicate(self.state):
            raise TimeoutError("device did not report the expected state")
        return self.snapshot()

    def set(self, command, field, value):
        values = self.state["values"]
        self.sent.append((values["noise.mode"], field, value))
        if field == "ambient.level":
            values["noise.mode"] = "ambient"
            values[field] = value
        elif field == "ambient.focus_on_voice":
            if values["noise.mode"] == "ambient":
                values[field] = value
        else:
            values[field] = value
        return self.wait(lambda state: state["values"][field] == value)

    def close(self):
        self.closed = True


class AmbientOrder(unittest.TestCase):
    def run_check(self, client):
        return live.run(PROFILE, Path("."), client, io.StringIO(), implementation="synthetic")

    def test_voice_off_after_anc_is_sent_in_ambient(self):
        # The 2026-09-15 failure: initial ANC with voice off put voice:true
        # before mode:anc and voice:false after it, so voice:false went in ANC.
        client = AmbientOnlySony("anc", 7, False)
        report = self.run_check(client)
        self.assertNotIn("error", report)
        self.assertTrue(report["passed"])
        for mode, field, _ in client.sent:
            if field.startswith("ambient."):
                self.assertEqual(mode, "ambient", client.sent)
        self.assertEqual(client.state["values"],
                         {"noise.mode": "anc", "ambient.level": 7, "ambient.focus_on_voice": False})

    def test_prerequisite_is_reported_separately(self):
        client = AmbientOnlySony("off", 7, False)
        report = self.run_check(client)
        cases = [check["case"] for check in report["checks"]]
        self.assertTrue(any(case.startswith("prerequisite:ambient.") for case in cases), cases)

    def test_devices_without_ambient_get_no_prerequisite(self):
        class Client:
            state = {"values": {"noise.mode": "anc"}}
        profile = {"capabilities": {"noise.mode": {"values": ["off", "anc"]}}}
        self.assertIsNone(live.ambient_prerequisite(profile, Client(), "ambient.focus_on_voice"))
        self.assertIsNone(live.ambient_prerequisite(PROFILE, Client(), "noise.mode"))


if __name__ == "__main__":
    unittest.main()
