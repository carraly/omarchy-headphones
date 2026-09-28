"""Owner-invoked smoke check of the exact checkout's adapter, with restoration.

This tests the adapter, not the running shell. Battery from Fast Pair/BlueZ,
wear edges, reconnect, acoustics and peer isolation need separate owner checks.
"""
import copy
import datetime
import json
import queue
import signal
import subprocess
import threading
import time

from . import devices as profiles
from .evidence import writable_cases
from .state import valid_value


class Client:
    def __init__(self, command):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        text=True, bufsize=1)
        self.events = queue.Queue()
        self.state = {}
        self.serial = 0
        self.pending = set()

        def reader():
            for line in self.process.stdout:
                try:
                    self.events.put(json.loads(line))
                except ValueError:
                    self.events.put({"modes": False, "error": "invalid adapter JSON"})
            self.events.put(None)

        self.thread = threading.Thread(target=reader, daemon=True)
        self.thread.start()

    def wait(self, predicate, timeout=20, after=-1):
        deadline = time.monotonic() + timeout
        while not (self.serial > after and predicate(self.state)):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("device did not report the expected state")
            try:
                state = self.events.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("device did not report the expected state") from None
            if state is None:
                raise RuntimeError("adapter exited before reporting the expected state")
            if state.get("apiVersion") == 1:
                self.state = state
            self.serial += 1
            if state.get("modes") is False:
                raise RuntimeError(state.get("error", "adapter is unavailable"))
        return copy.deepcopy(self.state)

    def refresh(self):
        # Consume queued reports before presenting a baseline or sending a command.
        while True:
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                break
            if item is None:
                raise RuntimeError("adapter has exited")
            if item.get("apiVersion") == 1:
                self.state = item
            self.serial += 1
        return copy.deepcopy(self.state)

    def set(self, command, field, value):
        self.refresh()
        if self.state.get("values", {}).get(field) == value and field not in self.pending:
            # A control already in its original state needs no restoration write.
            return copy.deepcopy(self.state)
        serial = self.serial
        self.pending.add(field)
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        state = self.wait(lambda state: field in state.get("observed", []) and state.get("values", {}).get(field) == value, after=serial)
        self.pending.discard(field)
        return state

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.thread.join(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()


def controls(profile):
    return [(case, json.dumps({"apiVersion": 1, "control": key, "value": value}), key, value)
            for case, (key, value) in writable_cases(profile).items()]


def ambient_prerequisite(profile, client, field):
    """The Ambient write to send before an ambient.* case, or None.

    A Sony headset stores the ambient level and Focus on voice with its ambient
    settings and applies them only in Ambient: a Focus on voice write sent in
    ANC is acknowledged and ignored (WH-CH720N, 2026-09-28), so the case would
    wait for a report that never comes. The panel only offers these controls
    in Ambient; the check tests them where they are offered.
    """
    modes = profile["capabilities"].get("noise.mode", {}).get("values", [])
    if not field.startswith("ambient.") or "ambient" not in modes:
        return None
    if hasattr(client, "refresh"):
        client.refresh()
    if client.state.get("values", {}).get("noise.mode") == "ambient":
        return None
    return json.dumps({"apiVersion": 1, "control": "noise.mode", "value": "ambient"})


def initial_ready(profile, state):
    caps, values = state.get("capabilities", {}), state.get("values", {})
    return all(key in caps and key in values and valid_value(key, values[key], spec)
               for key, spec in profile["capabilities"].items() if not spec.get("readOnly"))


def restore_actions(profile, initial):
    keys = [key for key, spec in profile["capabilities"].items() if not spec.get("readOnly") and key != "noise.mode"] + ["noise.mode"]
    return [(json.dumps({"apiVersion": 1, "control": key, "value": initial["values"][key]}), key, initial["values"][key]) for key in keys]


def run(profile, directory, client, output, root=profiles.ROOT, prompt=None, implementation=None, interview=None):
    report = {"apiVersion": 1, "device": profile["id"], "owner": profile["owner"],
              "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "implementation": implementation or profiles.implementation_hash(profile, directory, root),
              "scope": "adapter", "checks": [], "restoration": [], "passed": False,
              "untested": ["shell-integration", "reconnect", "peer-isolation", "charging", "acoustics"]}
    if prompt is None and interview is None:
        report['untested'].extend(('external-change', 'repeated', 'unsupported-command'))
    initial = None
    try:
        initial = client.wait(lambda state: initial_ready(profile, state))
        report["initial"] = initial
        report["checks"].append({"case": "initial", "reported": initial, "passed": True})
        ordered = sorted(controls(profile), key=lambda item: item[3] == initial["values"].get(item[2]))
        if interview is not None:
            extra = [('external-change', 'Change mode with the headphones or vendor app')]
            if profile.get('batterySource') == 'bridge':
                extra.extend(('battery:' + part, 'Read battery: ' + part)
                             for part in profile['capabilities'].get('battery', {}).get('parts', []))
            interview.configure_plan(ordered, extra)
        for case, command, field, value in ordered:
            prerequisite = ambient_prerequisite(profile, client, field)
            if prerequisite is not None:
                if interview is not None:
                    interview.event('prerequisite', 'Switching to Ambient first: this setting only applies in Ambient.',
                                    stepId=case)
                state = client.set(prerequisite, "noise.mode", "ambient")
                report["checks"].append({"case": "prerequisite:" + case, "command": prerequisite,
                                         "reported": state, "passed": True})
            if interview is not None:
                state = interview.control(case, field, value, client, command, profile)
                if state is None:
                    report['untested'].append(case)
                    interview.complete(case, 'skipped')
                    continue
                interview.complete(case)
            else:
                state = client.set(command, field, value)
            report["checks"].append({"case": case, "command": command, "reported": state, "passed": True})
        if prompt is not None or interview is not None:
            before = copy.deepcopy(client.state.get('values', {}))
            serial = client.serial
            instruction = 'Change the listening mode using the headphones or vendor app.'
            if interview is not None:
                performed = interview.manual('external-change', instruction)
            else:
                prompt(instruction)
                performed = True
            if performed:
                state = client.wait(lambda s: s.get('values', {}).get('noise.mode') != before.get('noise.mode'), after=serial)
                report['checks'].append({'case': 'external-change', 'reported': state, 'passed': True})
                if interview is not None:
                    interview.complete('external-change')
            else:
                report['untested'].append('external-change')
                if interview is not None:
                    interview.complete('external-change', 'skipped')
            if interview is None:
                client.process.stdin.write(json.dumps({'apiVersion': 1, 'control': 'unavailable.control', 'value': True}) + '\n')
                client.process.stdin.flush()
                prompt('Allow repeated reports to be captured (or trigger the same status again), then press Enter: ')
            else:
                # These need dedicated assertions; collecting an answer alone is not verification.
                report['untested'].extend(('repeated', 'unsupported-command'))
        battery = profile["capabilities"].get("battery", {})
        for part in battery.get("parts", []):
            if profile.get("batterySource") != "bridge":
                report["untested"].append("battery:" + part)
                continue
            if interview is not None:
                interview.activate('battery:' + part)
                interview.event('action', 'Reading the battery report.')
            state = client.wait(lambda s: type(s.get("values", {}).get("battery", {}).get(part)) is int
                                and 0 <= s["values"]["battery"][part] <= 100)
            report["checks"].append({"case": "battery:" + part, "reported": state, "passed": True})
            if interview is not None:
                interview.complete("battery:" + part)
        if "wear.detected" in profile["capabilities"]:
            report["untested"].extend(("wear.detected:true", "wear.detected:false"))
        report["passed"] = not (interview is not None and interview.skipped)
        if interview is not None and interview.skipped:
            report["incompleteReason"] = "owner skipped one or more checks"
    except BaseException as error:
        if interview is not None and interview.active_step:
            from .interview import Stopped
            interview.complete(interview.active_step, 'interrupted' if isinstance(error, (Stopped, KeyboardInterrupt)) else 'failed')
        report["error"] = type(error).__name__ + ": " + str(error)
    finally:
        if interview is not None:
            # UI/log errors must never suppress device restoration.
            try:
                interview.event('restoration', 'Restoring the original device settings. Ambient may briefly turn on before the original mode returns.')
            except BaseException as error:
                report['interviewError'] = str(error)
                report['passed'] = False
            report['ownerObservations'] = interview.observations
            report['skippedOwnerSteps'] = interview.skipped
            report['ownerObservationScope'] = 'Per-attempt owner answers; not automatic acoustic approval.'
        if hasattr(client, 'begin_restoration'):
            client.begin_restoration()
        if initial is not None:
            for command, field, value in restore_actions(profile, initial):
                try:
                    state = client.set(command, field, value)
                    report["restoration"].append({"command": command, "reported": state, "passed": True})
                except BaseException as error:
                    report["restoration"].append({"command": command, "passed": False, "error": str(error)})
                    report["passed"] = False
        else:
            report["restoration"] = [{"passed": False, "error": "initial state unknown; no controls sent"}]
        if interview is not None:
            restored = bool(report['restoration']) and all(item['passed'] for item in report['restoration'])
            interview.complete('restoration', 'completed' if restored else 'failed')
        if getattr(client, 'log_errors', []):
            report['passed'] = False
            report['timelineErrors'] = client.log_errors
        try:
            client.close()
        finally:
            output.write(json.dumps(report, indent=2) + "\n")
            output.flush()
    return report


def interrupt(_signum, _frame):
    raise KeyboardInterrupt("owner interrupted the live check")
