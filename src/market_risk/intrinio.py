"""Read-only Intrinio requests; credential and account fields stay out of evidence."""

import json
import urllib.error
import urllib.parse
import urllib.request

from market_risk.credentials import read_key
from market_risk.data import DataError


def request_intrinio(endpoint: str, params: dict) -> dict:
    key = read_key("INTRINIO_API_KEY")
    if not key:
        raise DataError("INTRINIO_API_KEY is not configured locally")
    request = urllib.request.Request(
        "https://api-v2.intrinio.com/" + endpoint + "?" + urllib.parse.urlencode(params),
        headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
        if key.encode() in content:
            raise DataError("Intrinio response contains unexpected authentication material")
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise DataError("Unexpected Intrinio response schema")
        return payload
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise DataError(f"Intrinio request rejected (HTTP {status})") from None
    except (OSError, ValueError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError("Intrinio network or JSON response failure") from None
