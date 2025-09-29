"""
Compatibility shim for Python 3.13 where the stdlib module `imghdr` was removed.
This provides a minimal `what()` function used by python-telegram-bot 13.x.
It returns None when type cannot be determined, which PTB handles gracefully.
"""
from typing import Optional, Union

def what(file: Union[str, bytes, "os.PathLike[str]"], h: Optional[bytes] = None) -> Optional[str]:
    # We intentionally do not try to detect real image types to avoid heavy deps.
    # PTB treats None as unknown type and proceeds without raising.
    return None
