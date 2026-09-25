"""What tools/soundcore_probe.py writes when asked to set a mode.

    python -m unittest tests.soundcore_probe_test

The probe runs on headphones nobody here has held, so its write must carry the
device's own bytes. The replies below are the ones in docs/captures/ and in
the Space 2 and Space One Pro pins.
"""
import unittest

from tests import harness

probe = harness.load_bridge("tools/soundcore_probe.py")


class ModeWriteBody(unittest.TestCase):
    def test_life_q30_reply_goes_back_four_bytes_wide(self):
        # docs/captures/soundcore-life-q30.txt: NOTIFY body=00020000
        body = probe.mode_write_body("ambient", bytes.fromhex("00020000"), None)
        self.assertEqual(body, bytes.fromhex("01020000"))

    def test_r60i_reply_goes_back_six_of_its_eight(self):
        # docs/captures/soundcore-r60i-nc.txt is not on main yet; its 06 01
        # body is 00 51 00 00 00 00 00 00 and the owner's write was 01 51 00 00 00 00.
        body = probe.mode_write_body("ambient", bytes.fromhex("0051000000000000"), None)
        self.assertEqual(body, bytes.fromhex("015100000000"))

    def test_reply_stands_over_the_state(self):
        # The Space One Pro's reply, as in its pin; the state bytes are the
        # Space 2's, standing in for a read at the wrong offset.
        body = probe.mode_write_body("off", bytes.fromhex("015001010005"),
                                     bytes.fromhex("011fff000003"))
        self.assertEqual(body, bytes.fromhex("025001010005"))

    def test_space_2_state_block_when_nothing_answered(self):
        body = probe.mode_write_body("anc", None, bytes.fromhex("011fff000003"))
        self.assertEqual(body, bytes.fromhex("001fff000003"))

    def test_nothing_seen_nothing_written(self):
        self.assertIsNone(probe.mode_write_body("anc", None, None))
        self.assertIsNone(probe.mode_write_body("anc", b"", None))


class SetStep(unittest.TestCase):
    def link(self, trust):
        link = probe.Link.__new__(probe.Link)
        link.fd, link.trust_offset_71 = 7, trust
        link.state_block, link.reply = bytes.fromhex("313131313131"), None
        link.sent = []
        link.send = lambda name, data: link.sent.append((name, data))
        return link

    def test_unanswered_unknown_model_is_not_written_to(self):
        link = self.link(trust=False)
        link.step("set-ambient", "ambient")
        self.assertEqual(link.sent, [])

    def test_the_old_fixed_bytes_are_gone(self):
        link = self.link(trust=False)
        link.reply = bytes.fromhex("00020000")
        link.step("set-ambient", "ambient")
        self.assertEqual(link.sent, [("set-ambient", probe.make_packet(
            probe.CMD_SOUND_MODES_SET, bytes.fromhex("01020000")))])
        self.assertNotIn(bytes.fromhex("1fff"), link.sent[0][1])

    def test_space_2_falls_back_to_its_state(self):
        link = self.link(trust=True)
        link.state_block = bytes.fromhex("011fff000003")
        link.step("set-off", "off")
        self.assertEqual(link.sent[0][1], probe.make_packet(
            probe.CMD_SOUND_MODES_SET, bytes.fromhex("021fff000003")))


if __name__ == "__main__":
    unittest.main()
