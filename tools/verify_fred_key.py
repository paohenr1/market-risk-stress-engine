"""Verify locally configured FRED authentication without logging the secret.

Read the inherited environment first, then the Windows user environment when
Codex has not yet inherited a newly saved setting. Never print request URLs or
exception messages: FRED authenticates using a query parameter in the URL.
"""

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import warnings


def read_key() -> tuple[str | None, str]:
    """Return a local secret and its source; do not modify environment settings."""
    key = os.environ.get("FRED_API_KEY")
    if key:
        return key, "process_environment"
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                value, _ = winreg.QueryValueEx(handle, "FRED_API_KEY")
                if isinstance(value, str) and value:
                    return value, "windows_user_environment"
        except FileNotFoundError:
            pass
    return None, "not_found"


def main(argv: list[str] | None = None) -> int:
    """Test official series metadata for GDP and unemployment; emit safe evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", action="store_true", help="Enter the key through a hidden local prompt")
    parser.add_argument("--save", action="store_true", help="Save a prompted key to the Windows user environment after verification")
    args = parser.parse_args(argv)
    if args.save and (not args.prompt or os.name != "nt"):
        parser.error("--save requires --prompt on Windows")
    try:
        if args.prompt:
            # Reject getpass's echoing fallback when no interactive terminal exists.
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                key = getpass.getpass("Paste your actual FRED key here (hidden), then press Enter: ")
            source = "hidden_prompt"
        else:
            key, source = read_key()
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        print(json.dumps({"status": "blocked", "reason": "hidden_prompt_unavailable_or_cancelled"}))
        return 1
    except OSError:
        print(json.dumps({"status": "blocked", "reason": "local_key_access_denied"}))
        return 1
    if not key:
        print(json.dumps({"status": "blocked", "reason": "key_not_configured"}))
        return 1
    if not re.fullmatch(r"[a-z0-9]{32}", key) or key == "abcdefghijklmnopqrstuvwxyz123456":
        print(json.dumps({"status": "blocked", "reason": "invalid_key_format"}))
        return 1
    results = []
    for series_id in ("GDPC1", "UNRATE"):
        query = urllib.parse.urlencode({"series_id": series_id, "file_type": "json", "api_key": key})
        request = urllib.request.Request(
            "https://api.stlouisfed.org/fred/series?" + query,
            headers={"User-Agent": "market-risk-authentication-check/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                metadata = json.load(response)["seriess"][0]
            if metadata["id"] != series_id:
                raise ValueError("Unexpected series")
            results.append({
                "series_id": metadata["id"], "frequency": metadata["frequency"],
                "units": metadata["units"], "observation_start": metadata["observation_start"],
                "observation_end": metadata["observation_end"],
            })
        except urllib.error.HTTPError as exc:
            exc.close()
            print(json.dumps({"status": "failed", "series_id": series_id, "http_status": exc.code,
                              "reason": "official_api_rejected_request"}))
            return 1
        except (OSError, ValueError, KeyError, IndexError):
            print(json.dumps({"status": "blocked", "series_id": series_id,
                              "reason": "network_or_response_failure"}))
            return 1
    saved = False
    if args.save:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as handle:
                winreg.SetValueEx(handle, "FRED_API_KEY", 0, winreg.REG_SZ, key)
                saved = winreg.QueryValueEx(handle, "FRED_API_KEY")[0] == key
            if not saved:
                raise OSError("Read-back mismatch")
        except OSError:
            print(json.dumps({"status": "blocked", "reason": "key_storage_failed", "api_authenticated": True}))
            return 1
    evidence = {"status": "verified", "key_source": source, "saved_to_windows_user_environment": saved,
                "verified_at_utc": datetime.now(timezone.utc).isoformat(),
                "endpoint": "https://api.stlouisfed.org/fred/series", "series": results}
    path = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "fred-authentication.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
