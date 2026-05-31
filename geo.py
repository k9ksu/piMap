# geo.py
import math


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def wrap_lon(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


def nice_number(x):
    if x <= 0:
        return 1.0
    exp = math.floor(math.log10(x))
    f = x / (10 ** exp)
    if f < 1.5:
        nf = 1.0
    elif f < 3.5:
        nf = 2.0
    elif f < 7.5:
        nf = 5.0
    else:
        nf = 10.0
    return nf * (10 ** exp)


def lonlat_to_xy(lon_deg: float, lat_deg: float, width: int, height: int):
    """
    Convert lon/lat in degrees to x,y on an equirectangular map.
    lon: -180..+180, lat: -90..+90
    """
    x = (lon_deg + 180.0) / 360.0 * width
    y = (90.0 - lat_deg) / 180.0 * height
    return x, y


def xy_to_lonlat(x: float, y: float, width: int, height: int):
    """
    Convert x,y on an equirectangular map to lon/lat in degrees.
    """
    lon = x / width * 360.0 - 180.0
    lat = 90.0 - y / height * 180.0
    return lon, lat


def great_circle_points(
    lat1_deg: float,
    lon1_deg: float,
    lat2_deg: float,
    lon2_deg: float,
    n: int = 100,
):
    """
    Generate n+1 points along the great-circle between two lat/lon points.
    Returns list of (lat_deg, lon_deg).
    """
    phi1 = math.radians(lat1_deg)
    lam1 = math.radians(lon1_deg)
    phi2 = math.radians(lat2_deg)
    lam2 = math.radians(lon2_deg)

    d = 2 * math.asin(
        math.sqrt(
            math.sin((phi2 - phi1) / 2.0) ** 2
            + math.cos(phi1)
            * math.cos(phi2)
            * math.sin((lam2 - lam1) / 2.0) ** 2
        )
    )

    if d == 0.0:
        return [(lat1_deg, lon1_deg)]

    points = []
    for i in range(n + 1):
        f = i / float(n)
        A = math.sin((1.0 - f) * d) / math.sin(d)
        B = math.sin(f * d) / math.sin(d)
        x = (
            A * math.cos(phi1) * math.cos(lam1)
            + B * math.cos(phi2) * math.cos(lam2)
        )
        y = (
            A * math.cos(phi1) * math.sin(lam1)
            + B * math.cos(phi2) * math.sin(lam2)
        )
        z = A * math.sin(phi1) + B * math.sin(phi2)
        phi = math.atan2(z, math.sqrt(x * x + y * y))
        lam = math.atan2(y, x)
        lat_deg = math.degrees(phi)
        lon_deg = math.degrees(lam)
        points.append((lat_deg, lon_deg))

    return points


def split_path_at_dateline(points):
    """
    Take a list of (lat, lon) points along a great-circle path and split it
    into segments whenever the path crosses the +/-180 meridian.

    Returns: list of segments, where each segment is a list of (lat, lon).

    Longitudes are normalized into [-180, +180].
    """
    segments = []
    current = []
    prev_lon = None

    for lat, lon in points:
        # Normalize lon to [-180, 180]
        lon_norm = ((lon + 180.0) % 360.0) - 180.0

        if prev_lon is not None:
            delta = abs(lon_norm - prev_lon)
            # If the jump is > 180 degrees, we crossed the Date Line
            if delta > 180.0:
                if current:
                    segments.append(current)
                    current = []

        current.append((lat, lon_norm))
        prev_lon = lon_norm

    if current:
        segments.append(current)

    return segments
