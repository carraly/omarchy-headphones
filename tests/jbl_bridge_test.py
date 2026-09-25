"""What jbl-bridge tells btgatt-client for the TUNE230NC TWS.

    python -m unittest tests.jbl_bridge_test

The frozen session is tests/pins/jbl/tune230nc-tws.json. This bridge speaks
to the earbuds through btgatt-client, so what it "sends" is the lines it
writes to that child's stdin — register-notify and write-value commands, the
frames spelled out byte by byte — and what it "receives" is the child's
output lines. The child is a fake with a queue for its stdout; the bridge's
own reader thread runs as it does live, and start() runs on a thread of its
own so the pin can answer it mid-way, in the order the hardware would.

No hardware and no child process: the bridge's effects on the world are the
lines it tells the child, emit(), and the two support-file writes, and all
of them are captured.
"""
import queue
import os
import subprocess
import threading
import time
import types
import unittest

from tests import harness

bridge_module = harness.load_bridge("jbl-bridge")


class FakeClient:
    """btgatt-client as the bridge sees it: a stdin to write, a stdout to read."""

    def __init__(self, told):
        self.told = told
        self.queue = queue.Queue()
        self.returncode = None
        self.stdin = self
        self.stdout = self

    def write(self, text):
        self.told.append(text.rstrip("\n"))

    def flush(self):
        pass

    def readline(self):
        return self.queue.get()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        self.queue.put("")
        return 0

    def kill(self):
        self.wait()


class Session(harness.Session):
    """A JBL session. "device" in a pin is one output line of btgatt-client.
    "sent" is every line the bridge wrote to btgatt-client. "start" runs the
    bridge's opening conversation on a thread; "wait_start" joins it, after
    which state "start_code" is what it returned (null to carry on)."""

    def __init__(self, model_id="0x1234"):
        super().__init__(bridge_module)
        self.support = []
        self.client = None
        self.thread = None
        self.start_code = "not started"
        self.processed = threading.Semaphore(0)

        def popen(*_args, **_kwargs):
            self.client = FakeClient(self.frames)
            return self.client

        bridge_module.subprocess = types.SimpleNamespace(
            Popen=popen, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
            TimeoutExpired=subprocess.TimeoutExpired)
        # The gaps between frames are the earbuds' business, not the test's.
        bridge_module.time = types.SimpleNamespace(sleep=lambda _s: None)
        bridge_module.remember_answered = lambda model: self.support.append(("answered", model))
        bridge_module.record_miss = lambda model: self.support.append(("miss", model))

        self.bridge = bridge_module.Bridge("48:B4:41:00:00:01", model_id)
        original = self.bridge.on_client_line

        def on_client_line(line):
            try:
                original(line)
            finally:
                self.processed.release()

        self.bridge.on_client_line = on_client_line

    def device(self, line):
        self.client.queue.put(line + "\n")
        if not self.processed.acquire(timeout=2):
            raise AssertionError("the bridge did not read %r" % line)

    def command(self, line):
        self.bridge.run_command(line)

    def do_start(self):
        def run():
            self.start_code = self.bridge.start()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def do_wait_start(self):
        self.thread.join(5)
        if self.thread.is_alive():
            raise AssertionError("start() did not return")

    def wait_sent(self, count):
        deadline = time.monotonic() + 2
        while len(self.frames) < count and time.monotonic() < deadline:
            time.sleep(0.005)
        super().wait_sent(count)

    @property
    def sent(self):
        return list(self.frames)

    def state(self, name):
        if name == "start_code":
            return self.start_code
        return super().state(name)

    def close(self):
        self.bridge.finish(0)
        self.bridge.stop()
        if self.thread is not None:
            self.thread.join(2)
        os.close(self.bridge.wake_read)
        os.close(self.bridge.wake_write)


harness.pin_tests(globals(), "jbl-bridge", Session)

DISCOVERED = "GATT discovery procedures complete"
REGISTERED = "Registered notify handler"


class Silent(unittest.TestCase):
    def test_no_report_records_a_miss_and_parks_the_model(self):
        bridge_module.ANSWER_TIMEOUT = 0.05
        try:
            s = Session()
            self.addCleanup(s.close)
            s.device("Connecting to device... Done")
            s.device(DISCOVERED)
            s.do_start()
            s.wait_sent(1)
            s.device(REGISTERED)
            s.do_wait_start()
        finally:
            bridge_module.ANSWER_TIMEOUT = 12
        self.assertEqual(s.start_code, bridge_module.EXIT_UNSUPPORTED)
        self.assertEqual(s.support, [("miss", "0x1234")])
        self.assertEqual(s.lines[-1]["modes"], False)

    def test_a_refused_link_is_transient_and_records_nothing(self):
        s = Session()
        self.addCleanup(s.close)
        s.device("Failed to connect: Connection refused (111)")
        self.assertEqual(s.bridge.exit_code, bridge_module.EXIT_TRANSIENT)
        self.assertEqual(s.support, [])


if __name__ == "__main__":
    unittest.main()
