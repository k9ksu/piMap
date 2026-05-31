# maidenhead.py

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
