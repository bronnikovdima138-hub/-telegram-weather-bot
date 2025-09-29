import datetime as dt
from typing import Dict, List, Tuple, Optional
import requests
import math

HEADERS = {
    "User-Agent": "TelegramWeatherBot/1.0 (contact: user)"
}

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

# Intervals requested by the user, local time
INTERVALS = [
    (dt.time(0, 0), dt.time(3, 0)),
    (dt.time(3, 0), dt.time(6, 0)),
    (dt.time(6, 0), dt.time(12, 0)),
    (dt.time(12, 0), dt.time(15, 0)),
    (dt.time(15, 0), dt.time(18, 0)),
    (dt.time(18, 0), dt.time(20, 0)),
    (dt.time(20, 0), dt.time(23, 0)),
]


def _date_str(target_date: Optional[dt.date]) -> str:
    d = target_date or dt.date.today()
    return d.isoformat()


def fetch_surface(lat: float, lon: float, timezone: str, target_date: Optional[dt.date]) -> Dict:
    params = {
        "latitude": f"{lat:.6f}",
        "longitude": f"{lon:.6f}",
        "hourly": ",".join([
            "temperature_2m",
            "dew_point_2m",
            "precipitation",
            "precipitation_probability",
            "rain",
            "showers",
            "snowfall",
            "cloud_cover",
            "cloud_cover_low",
            "cloud_cover_mid",
            "cloud_cover_high",
            "wind_speed_10m",
            "wind_gusts_10m",
            "cape",
        ]),
        "timezone": timezone,
        "start_date": _date_str(target_date),
        "end_date": _date_str(target_date),
        "models": "best_match",
    }
    r = requests.get(OPEN_METEO_BASE, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_winds_aloft(lat: float, lon: float, timezone: str, target_date: Optional[dt.date]) -> Dict[str, Dict]:
    """Fetch winds for multiple models at 850/700/600 hPa."""
    fields = [
        "wind_speed_850hPa","wind_direction_850hPa",
        "wind_speed_700hPa","wind_direction_700hPa",
        "wind_speed_600hPa","wind_direction_600hPa",
    ]
    models = ["gfs_seamless", "icon_seamless"]
    out: Dict[str, Dict] = {}
    for model in models:
        params = {
            "latitude": f"{lat:.6f}",
            "longitude": f"{lon:.6f}",
            "hourly": ",".join(fields),
            "timezone": timezone,
            "start_date": _date_str(target_date),
            "end_date": _date_str(target_date),
            "models": model,
        }
        r = requests.get(OPEN_METEO_BASE, params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        out[model] = r.json()
    return out


def _to_ms(kmh: float) -> float:
    return kmh / 3.6


def _interp(value_low: float, z_low: float, value_high: float, z_high: float, z: float) -> float:
    if z_high == z_low:
        return value_low
    t = (z - z_low) / (z_high - z_low)
    return value_low + t * (value_high - value_low)


# Rough mapping from pressure levels to altitudes (m) for mid-latitudes
Z_850 = 1500.0
Z_700 = 3000.0
Z_600 = 4200.0


def derive_winds_profile(times: List[str], gfs: Dict, icon: Dict) -> Dict[str, Dict[str, float]]:
    """Return dict[iso_time] -> {w1500, w2500, w3500} in m/s by model consensus (average of close values)."""
    # Build dicts by time
    out: Dict[str, Dict[str, float]] = {}

    def extract(model_json: Dict, key: str) -> List[Optional[float]]:
        return model_json.get("hourly", {}).get(key, [])

    for i, t in enumerate(times):
        # Pull values from models (km/h), convert to m/s
        vals = {}
        for name, data in [("gfs", gfs), ("icon", icon)]:
            h = data.get("hourly", {})
            try:
                s850 = h["wind_speed_850hPa"][i]
                s700 = h["wind_speed_700hPa"][i]
                s600 = h["wind_speed_600hPa"][i]
            except Exception:
                s850 = s700 = s600 = None
            if s850 is None or s700 is None or s600 is None:
                continue
            vals[name] = (
                _to_ms(s850),
                _to_ms(s700),
                _to_ms(s600),
            )
        if not vals:
            continue
        # Average by model if both present
        def avg(idx: int) -> float:
            arr = [v[idx] for v in vals.values()]
            return sum(arr) / len(arr)

        s850 = avg(0)
        s700 = avg(1)
        s600 = avg(2)

        # Interpolate to requested heights
        w1500 = s850  # ~same level
        w2500 = _interp(s850, Z_850, s700, Z_700, 2500.0)
        w3500 = _interp(s700, Z_700, s600, Z_600, 3500.0)

        out[t] = {
            "w1500": w1500,
            "w2500": w2500,
            "w3500": w3500,
        }
    return out


def compute_lcl_m(temp_c: float, dewpoint_c: float) -> Optional[float]:
    if temp_c is None or dewpoint_c is None:
        return None
    try:
        return max(0.0, 125.0 * (temp_c - dewpoint_c))
    except Exception:
        return None


def slice_intervals(surface: Dict, winds_profile: Dict[str, Dict[str, float]]) -> List[Dict]:
    """
    Build per-interval aggregates in local time for the requested date.
    Returns list of dicts: {label, weather_text, wind_ground_ms, w1500, w2500, w3500, cloud_base_m}
    """
    hourly = surface.get("hourly", {})
    times = hourly.get("time", [])
    t2m = hourly.get("temperature_2m", [])
    td2m = hourly.get("dew_point_2m", [])
    precip = hourly.get("precipitation", [])
    cc_total = hourly.get("cloud_cover", [])
    wind10 = hourly.get("wind_speed_10m", [])  # km/h
    gust10 = hourly.get("wind_gusts_10m", [])  # km/h

    def parse_time(s: str) -> dt.time:
        # Assume format: YYYY-MM-DDTHH:MM
        return dt.datetime.fromisoformat(s).time()

    results: List[Dict] = []

    for start_t, end_t in INTERVALS:
        # Collect indices that fall in [start_t, end_t)
        idxs = [i for i, ts in enumerate(times) if start_t <= parse_time(ts) < end_t]
        if not idxs:
            label = f"Погода с {start_t.strftime('%H:%M')} по {end_t.strftime('%H:%M')}"
            results.append({
                "label": label,
                "desc": "нет данных",
                "wind_ground_ms": None,
                "w1500": None,
                "w2500": None,
                "w3500": None,
                "cloud_base_m": None,
            })
            continue

        p_sum = sum(precip[i] or 0.0 for i in idxs)
        p_any = p_sum > 0.05
        cloud_mean = sum(cc_total[i] or 0 for i in idxs) / len(idxs)

        # Simple description
        if p_any:
            desc = "пасмурно, дождь"
        else:
            desc = "пасмурно" if cloud_mean >= 70 else ("переменная облачность" if cloud_mean >= 30 else "малоблачно")

        # Ground wind mean and max gust
        mean_wind_ms = _to_ms(sum(wind10[i] or 0.0 for i in idxs) / len(idxs))
        max_gust_ms = _to_ms(max((gust10[i] or 0.0) for i in idxs)) if gust10 else None

        # Cloud base from first hour in window (representative)
        lcl_vals = [compute_lcl_m(t2m[i], td2m[i]) for i in idxs]
        lcl_vals = [v for v in lcl_vals if v is not None]
        cloud_base_m = sum(lcl_vals) / len(lcl_vals) if lcl_vals else None

        # Winds aloft: take center hour if available
        mid_i = idxs[len(idxs)//2]
        time_key = times[mid_i]
        wp = winds_profile.get(time_key, {})

        label = f"Погода с {start_t.strftime('%H:%M')} по {end_t.strftime('%H:%M')}"
        results.append({
            "label": label,
            "desc": desc,
            "wind_ground_ms": mean_wind_ms,
            "wind_gust_ms": max_gust_ms,
            "w1500": wp.get("w1500"),
            "w2500": wp.get("w2500"),
            "w3500": wp.get("w3500"),
            "cloud_base_m": cloud_base_m,
        })
    return results


def format_report(date_local: dt.date, coords_text: str, place_text: str, intervals: List[Dict]) -> str:
    lines: List[str] = []
    lines.append(
        f"ПОГОДНЫЕ УСЛОВИЯ НА {date_local.strftime('%d.%m.%Y')} (\"{coords_text}\", \"{place_text}\")"
    )
    lines.append("")

    def fmt_speed(v: Optional[float]) -> str:
        if v is None:
            return "—"
        return f"{v:.0f} м/с"

    def fmt_height(v: Optional[float]) -> str:
        if v is None:
            return "—"
        return f"~{int(round(v, -1))} м"

    for item in intervals:
        lines.append(f"{item['label']} — {item['desc']}")
        wg = item.get("wind_ground_ms")
        gg = item.get("wind_gust_ms")
        gust_part = f" (порывы до ~{int(round(gg))} м/с)" if gg else ""
        lines.append(f"Ветер на земле: {fmt_speed(wg)}{gust_part}")
        lines.append(f"Ветер на 1500 метров: {fmt_speed(item.get('w1500'))}")
        lines.append(f"Ветер на 2500 метров: {fmt_speed(item.get('w2500'))}")
        lines.append(f"Ветер на 3500 метров: {fmt_speed(item.get('w3500'))}")
        lines.append(f"Нижняя граница облаков: {fmt_height(item.get('cloud_base_m'))}")
        lines.append("")

    return "\n".join(lines).strip()
