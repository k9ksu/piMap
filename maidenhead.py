# maidenhead.py

def latlon_to_maidenhead(lat: float, lon: float, precision: int = 6) -> str:
    """Convert lat/lon (decimal degrees) to a Maidenhead locator.

    precision = number of characters: 2 = field (e.g. 'EM'), 4 = square
    ('EM28'), 6 = subsquare ('EM28aa'). Clamped to valid ranges."""
    lat = max(-90.0, min(89.99999, lat)) + 90.0
    lon = max(-180.0, min(179.99999, lon)) + 180.0

    res = chr(ord('A') + int(lon // 20)) + chr(ord('A') + int(lat // 10))
    if precision <= 2:
        return res
    lon %= 20.0
    lat %= 10.0
    res += str(int(lon // 2)) + str(int(lat // 1))
    if precision <= 4:
        return res
    lon %= 2.0
    lat %= 1.0
    res += chr(ord('a') + int(lon // (2.0 / 24.0))) + chr(ord('a') + int(lat // (1.0 / 24.0)))
    return res[:precision]


def latlon_to_field(lat: float, lon: float) -> str:
    """2-character Maidenhead field for a lat/lon (e.g. 'EM')."""
    return latlon_to_maidenhead(lat, lon, 2)


def maidenhead_to_latlon(grid: str):
    """
    Convert a Maidenhead grid locator (4 or 6 chars) to (lat, lon) at the center.
    Example:
        "DM97"   -> (lat, lon)
        "DM97MW" -> more precise
    """
    if not grid:
        raise ValueError("Empty grid")

    grid = grid.strip().upper()
    if len(grid) < 4:
        raise ValueError("Grid must be at least 4 characters")

    # First pair: fields (A-R)
    lon = (ord(grid[0]) - ord('A')) * 20 - 180
    lat = (ord(grid[1]) - ord('A')) * 10 - 90

    # Second pair: squares (0-9)
    lon += int(grid[2]) * 2
    lat += int(grid[3]) * 1

    if len(grid) == 4:
        # Center of 2x1 degree square
        lon += 1.0
        lat += 0.5
        return (lat, lon)

    # Sub-squares (a-x) if 6 chars
    if len(grid) >= 6:
        lon += (ord(grid[4]) - ord('A')) * (2.0 / 24.0)
        lat += (ord(grid[5]) - ord('A')) * (1.0 / 24.0)
        # Center of subsquare
        lon += (2.0 / 24.0) / 2.0
        lat += (1.0 / 24.0) / 2.0
        return (lat, lon)

    # If >6, ignore extra precision
    lon += 1.0
    lat += 0.5
    return (lat, lon)
