"""Read local API credentials without including them in logs or files."""

import os


def read_key(name: str) -> str | None:
    """Read an inherited setting, then the Windows user environment."""
    value = os.environ.get(name)
    if value:
        return value
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                value, _ = winreg.QueryValueEx(handle, name)
                if isinstance(value, str) and value:
                    return value
        except FileNotFoundError:
            pass
    return None
