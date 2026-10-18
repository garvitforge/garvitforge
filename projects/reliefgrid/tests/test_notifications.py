import os
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs

from notifications import TwilioNotifier


class TwilioNotifierTests(unittest.TestCase):
    def setUp(self):
        self.notifier = TwilioNotifier(
            "AC" + "a" * 32,
            "test-auth-token",
            "+14155550100",
            ("+14155550101",),
        )
        self.incident = {
            "code": "RG-ABC123",
            "kind": "Flooding & rescue",
            "district": "North District",
        }
        self.response = MagicMock()
        self.response.__enter__.return_value.status = 201

    def test_alerts_are_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(TwilioNotifier.from_environment())

    @patch("notifications.urlopen")
    def test_sms_uses_twilio_messages_resource(self, urlopen):
        urlopen.return_value = self.response
        self.notifier.send_sms(self.incident)

        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/Messages.json"))
        self.assertTrue(request.get_header("Authorization").startswith("Basic "))
        fields = parse_qs(request.data.decode("utf-8"))
        self.assertEqual(fields["To"], ["+14155550101"])
        self.assertEqual(fields["From"], ["+14155550100"])
        self.assertIn("RG-ABC123", fields["Body"][0])

    @patch("notifications.urlopen")
    def test_call_uses_twilio_calls_resource_and_escaped_twiml(self, urlopen):
        urlopen.return_value = self.response
        self.notifier.place_call(self.incident)

        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/Calls.json"))
        fields = parse_qs(request.data.decode("utf-8"))
        self.assertEqual(fields["To"], ["+14155550101"])
        self.assertIn("&amp;", fields["Twiml"][0])
        self.assertIn("<Response><Say>", fields["Twiml"][0])


if __name__ == "__main__":
    unittest.main()