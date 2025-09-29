import requests
from typing import Optional, Tuple

HEADERS = {
    "User-Agent": "TelegramWeatherBot/1.0 (contact: user)"
}


def reverse_geocode(lat: float, lon: float, language: str = "ru") -> Tuple[str, str]:
    """
    Returns (display_name, short_name)
    short_name tries to pick settlement/municipality/region
    """
    url = (
        "https://nominatim.openstreetmap.org/reverse"
        f"?format=jsonv2&lat={lat:.6f}&lon={lon:.6f}&accept-language={language}"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        display = data.get("display_name") or ""
        addr = data.get("address", {})
        # Prefer village/town/city -> municipality -> district -> state
        short = (
            addr.get("village")
            or addr.get("town")
            or addr.get("city")
            or addr.get("municipality")
            or addr.get("district")
            or addr.get("state")
            or display
        )
        return display, short
    except Exception:
        return "", ""
