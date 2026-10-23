"""Verify Intrinio access through a hidden prompt; never print the credential."""

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
import warnings

from market_risk.credentials import read_key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", action="store_true")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    if args.save and (not args.prompt or os.name != "nt"):
        parser.error("--save requires --prompt on Windows")
    try:
        if args.prompt:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                key = getpass.getpass("Paste your Intrinio API key (hidden), then press Enter: ")
        else:
            key = read_key("INTRINIO_API_KEY")
        if not key or key != key.strip() or any(c.isspace() for c in key):
            print(json.dumps({"status": "blocked", "reason": "key_missing_or_invalid"}))
            return 1
        request = urllib.request.Request(
            "https://api-v2.intrinio.com/account/access_codes",
            headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
        codes = payload["access_codes"]
        if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
            raise ValueError("Unexpected access-code schema")
        # Select only entitlement identifiers; do not persist account or email fields.
        if any(key in code for code in codes):
            raise ValueError("Unexpected response")
        if args.save:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as handle:
                winreg.SetValueEx(handle, "INTRINIO_API_KEY", 0, winreg.REG_SZ, key)
                if winreg.QueryValueEx(handle, "INTRINIO_API_KEY")[0] != key:
                    raise OSError("Storage read-back failed")
        evidence = {"status": "verified", "verified_at_utc": datetime.now(timezone.utc).isoformat(),
                    "endpoint": "https://api-v2.intrinio.com/account/access_codes",
                    "access_codes": codes, "saved_to_windows_user_environment": args.save,
                    "historical_coverage_verified": False}
        path = Path(__file__).resolve().parents[1] / "data" / "raw" / "intrinio-access.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, indent=2))
        return 0
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        print(json.dumps({"status": "failed", "http_status": code,
                          "reason": "intrinio_rejected_request"}))
    except (OSError, ValueError, KeyError, TypeError, getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        print(json.dumps({"status": "blocked", "reason": "prompt_network_response_or_storage_failure"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
