"""Safe Intrinio authentication and observable entitlement-probe behavior."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error

from market_risk.data import DataError
from market_risk.intrinio import request_intrinio

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IntrinioTests(unittest.TestCase):
    def test_authentication_reports_entitlements_without_account_or_key(self):
        module = load_tool("verify_intrinio_key")
        secret = "synthetic-private-key"
        response = io.StringIO(json.dumps({"access_codes": ["api", "us_index_prices"], "account": {"email": "private@example.test"}}))
        output = io.StringIO()
        with patch.object(module, "read_key", return_value=secret), patch("urllib.request.urlopen", return_value=response) as call, patch.object(Path, "write_text"), contextlib.redirect_stdout(output):
            result = module.main([])
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["access_codes"], ["api", "us_index_prices"])
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn("private@example.test", output.getvalue())
        request = call.call_args.args[0]
        self.assertNotIn(secret, request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + secret)

    def test_missing_key_never_contacts_api(self):
        with patch("market_risk.intrinio.read_key", return_value=None), patch("urllib.request.urlopen") as call:
            with self.assertRaisesRegex(DataError, "not configured"):
                request_intrinio("indices", {})
        call.assert_not_called()

    def test_http_rejection_does_not_disclose_secret(self):
        secret = "synthetic-private-key"
        error = urllib.error.HTTPError("https://example.test", 403, secret, {}, None)
        with patch("market_risk.intrinio.read_key", return_value=secret), patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(DataError) as raised:
                request_intrinio("indices", {})
        self.assertNotIn(secret, str(raised.exception))
        self.assertIn("403", str(raised.exception))

    def test_probe_preserves_missing_and_does_not_emit_prices(self):
        module = load_tool("probe_intrinio")
        def response(endpoint, params):
            return {"index": {"symbol": "$SPX", "name": "S&P 500"},
                    "historical_data": [{"date": params["start_date"], "value": None},
                                        {"date": params["end_date"], "value": 12345.678}], "next_page": None}
        output = io.StringIO()
        with patch.object(module, "request_intrinio", side_effect=response), patch.object(Path, "write_text"), contextlib.redirect_stdout(output):
            result = module.main()
        self.assertEqual(result, 0)
        self.assertNotIn("12345.678", output.getvalue())
        report = json.loads(output.getvalue())
        self.assertTrue(all(row["source_missing_count"] == 1 for row in report["windows"]))
        self.assertFalse(report["full_continuous_history_verified"])


if __name__ == "__main__":
    unittest.main()
