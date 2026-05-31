"""Solar position + day/night terminator math for the greyline overlay.

Pure functions (NOAA approximations); no app state.
"""

import math


def solar_decl_and_eqtime(utc_struct):
    """Return (declination_rad, equation_of_time_minutes) for a UTC struct_time."""
    doy = utc_struct.tm_yday
    hour = utc_struct.tm_hour
    minute = utc_struct.tm_min
    second = utc_struct.tm_sec

    gamma = 2.0 * math.pi / 365.0 * (
        doy - 1 + (hour - 12) / 24.0 + minute / 1440.0 + second / 86400.0
    )

    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )

    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )

    return decl, eqtime


def subsolar_lon(utc_struct, eqtime_minutes):
    utc_minutes = (
        utc_struct.tm_hour * 60.0
        + utc_struct.tm_min
        + utc_struct.tm_sec / 60.0
    )
    lon = (720.0 - (utc_minutes + eqtime_minutes)) / 4.0
    return ((lon + 180.0) % 360.0) - 180.0


def cos_zenith(lat_rad, lon_deg, decl_rad, subsolar_lon_deg):
    h = math.radians(((lon_deg - subsolar_lon_deg + 180.0) % 360.0) - 180.0)
    return (
        math.sin(lat_rad) * math.sin(decl_rad)
        + math.cos(lat_rad) * math.cos(decl_rad) * math.cos(h)
    )


def terminator_lat(lon_deg, decl_rad, subsolar_lon_deg):
    sin_dec = math.sin(decl_rad)
    cos_dec = math.cos(decl_rad)
    h = math.radians(((lon_deg - subsolar_lon_deg + 180.0) % 360.0) - 180.0)
    if abs(sin_dec) < 1e-6:
        return 0.0
    tan_lat = -(cos_dec * math.cos(h)) / sin_dec
    return math.degrees(math.atan(tan_lat))
