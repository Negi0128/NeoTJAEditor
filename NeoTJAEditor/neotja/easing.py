def curve_value(t: float, curve_type: str) -> float:
    if "加速" in curve_type:
        return t * t
    elif "減速" in curve_type:
        return 1 - (1 - t) ** 2
    elif "S字" in curve_type:
        return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2
    return t
