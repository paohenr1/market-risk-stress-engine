"""Check authentication-helper behavior with fake credentials and mocked HTTP.

No live FRED call or real credential is used in these tests.
"""

from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from tools import verify_fred_key

FAKE_KEY = "a" * 32


def metadata_response(series_id):
    stream = io.StringIO(json.dumps({"seriess": [{
        "id": series_id, "frequency": "Quarterly", "units": "test units",
        "observation_start": "2000-01-01", "observation_end": "2026-04-01",
    }]}))
    response = MagicMock()
    response.__enter__.return_value = stream
    return response


class AuthenticationTests(unittest.TestCase):
    def test_prompt_authenticates_both_series_without_disclosing_secret(self):
        output = io.StringIO()
        with patch.object(verify_fred_key.getpass, "getpass", return_value=FAKE_KEY), \
             patch.object(verify_fred_key.urllib.request, "urlopen", side_effect=[metadata_response("GDPC1"), metadata_response("UNRATE")]) as http, \
             patch.object(verify_fred_key.Path, "write_text") as save_evidence, \
             redirect_stdout(output):
            code = verify_fred_key.main(["--prompt"])
        self.assertEqual(code, 0)
        self.assertEqual(http.call_count, 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "verified")
        self.assertFalse(result["saved_to_windows_user_environment"])
        self.assertNotIn(FAKE_KEY, output.getvalue())
        self.assertNotIn(FAKE_KEY, save_evidence.call_args.args[0])

    def test_invalid_prompt_does_not_make_a_request(self):
        output = io.StringIO()
        with patch.object(verify_fred_key.getpass, "getpass", return_value="not-a-key"), \
             patch.object(verify_fred_key.urllib.request, "urlopen") as http, redirect_stdout(output):
            code = verify_fred_key.main(["--prompt"])
        self.assertEqual(code, 1)
        http.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["reason"], "invalid_key_format")

    def test_api_rejection_does_not_log_a_credential_bearing_url(self):
        output = io.StringIO()
        failure = urllib.error.HTTPError("https://example.invalid/?api_key=" + FAKE_KEY, 400, "test", {}, None)
        with patch.object(verify_fred_key.getpass, "getpass", return_value=FAKE_KEY), \
             patch.object(verify_fred_key.urllib.request, "urlopen", side_effect=failure), redirect_stdout(output):
            code = verify_fred_key.main(["--prompt"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["http_status"], 400)
        self.assertNotIn(FAKE_KEY, output.getvalue())


if __name__ == "__main__":
    unittest.main()
