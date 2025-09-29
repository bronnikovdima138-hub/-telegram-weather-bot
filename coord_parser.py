import re
from typing import Optional, Tuple

# Utilities to parse coordinates like:
# "Широта: 47°41'с. ш. / Долгота: 36°49'в. д. / Высота: 119 m\nЧасовой пояс: Europe/Kiev (UTC+3)"
# and also accept variations, including spaces and punctuation.

_DEG_MIN_SEC_RE = re.compile(
    r"(?P<deg>[-+]?\d{1,3})\s*[°º]?\s*(?P<min>\d{1,2})?\s*['’′]?\s*(?P<sec>\d{1,2}(?:\.\d+)?)?\s*\"?",
    re.IGNORECASE,
)

# Accept direction letters in Cyrillic and Latin
_LAT_DIR_RE = re.compile(r"([NSСЮ]|с\.?\s*ш\.|ю\.?\s*ш\.)", re.IGNORECASE)
_LON_DIR_RE = re.compile(r"([EWВЗ]|в\.?\s*д\.|з\.?\s*д\.)", re.IGNORECASE)

_TZ_RE = re.compile(r"Часов[ао]й\s+пояс\s*:\s*([^\n]+)", re.IGNORECASE)
_ALT_RE = re.compile(r"Высот[ае]:?\s*([+-]?\d+(?:\.\d+)?)\s*(?:m|м)?", re.IGNORECASE)


def dms_to_decimal(deg: float, minute: float = 0.0, sec: float = 0.0, sign: int = 1) -> float:
    return sign * (abs(deg) + minute / 60.0 + sec / 3600.0)


def _extract_dms(fragment: str) -> Optional[Tuple[float, float, float, Optional[str]]]:
    m = _DEG_MIN_SEC_RE.search(fragment)
    if not m:
        return None
    deg = float(m.group('deg'))
    minute = float(m.group('min') or 0.0)
    sec = float(m.group('sec') or 0.0)
    return deg, minute, sec, fragment[m.end():]


def _direction_sign(text_after: Optional[str], is_lat: bool) -> int:
    if not text_after:
        return 1
    # Determine sign by direction letters if present
    if is_lat:
        if _LAT_DIR_RE.search(text_after or ''):
            dir_match = _LAT_DIR_RE.search(text_after)
            dir_str = dir_match.group(1).lower() if dir_match else ''
            if dir_str.startswith('s') or 'ю' in dir_str:
                return -1
            return 1
    else:
        if _LON_DIR_RE.search(text_after or ''):
            dir_match = _LON_DIR_RE.search(text_after)
            dir_str = dir_match.group(1).lower() if dir_match else ''
            # Восток (E) положительный, Запад (W) отрицательный
            if dir_str.startswith('w') or 'з' in dir_str:
                return -1
            return 1
    return 1


def parse_coordinates(text: str) -> Tuple[float, float, Optional[float], Optional[str]]:
    """
    Returns (lat, lon, altitude_m, timezone_str)
    Raises ValueError if cannot parse lat/lon.
    """
    # Split by markers for latitude/longitude
    # Try to locate latitude and longitude fragments
    lat_frag = None
    lon_frag = None

    # Common Russian labels
    for part in re.split(r"[\n/|]", text):
        pt = part.strip()
        if re.search(r"широт|lat", pt, re.IGNORECASE):
            lat_frag = pt
        elif re.search(r"долгот|lon|lng", pt, re.IGNORECASE):
            lon_frag = pt

    # If labels absent, try to find two DMS numbers in order
    if lat_frag is None or lon_frag is None:
        nums = _DEG_MIN_SEC_RE.findall(text)
        if len(nums) >= 2:
            # Build fragments manually around matches
            lat_frag = text
            lon_frag = text

    lat_tuple = _extract_dms(lat_frag or text)
    lon_tuple = _extract_dms(lon_frag or text)

    if not lat_tuple or not lon_tuple:
        raise ValueError("Не удалось разобрать координаты. Пришлите, пожалуйста, строку в формате DMS: 47°41'с. ш. / 36°49'в. д.")

    lat_deg, lat_min, lat_sec, lat_tail = lat_tuple
    lon_deg, lon_min, lon_sec, lon_tail = lon_tuple

    lat_sign = _direction_sign(lat_tail, is_lat=True)
    lon_sign = _direction_sign(lon_tail, is_lat=False)

    lat = dms_to_decimal(lat_deg, lat_min, lat_sec, lat_sign)
    lon = dms_to_decimal(lon_deg, lon_min, lon_sec, lon_sign)

    # Altitude
    alt = None
    m_alt = _ALT_RE.search(text)
    if m_alt:
        try:
            alt = float(m_alt.group(1))
        except Exception:
            alt = None

    # Timezone (optional)
    tz = None
    mtz = _TZ_RE.search(text)
    if mtz:
        tz = mtz.group(1).strip()

    return lat, lon, alt, tz
