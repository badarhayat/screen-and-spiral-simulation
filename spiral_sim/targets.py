"""Move splitter openings until the simulation hits a requested mass pull.

Openings are on the same 0–100 scale used everywhere else, but the search
is allowed to go outside that scale. A requested pull that the usual range
cannot reach is still matched when a more extreme cut density can do it,
and the report shows the effective opening.
"""

from __future__ import annotations

from dataclasses import replace

from spiral_sim.feed import Feed
from spiral_sim.model import OperatingConditions, PartitionParameters, SplitterSettings
from spiral_sim.simulate import simulate


def _yields(
    feed: Feed,
    params: PartitionParameters,
    settings: SplitterSettings,
    operating: OperatingConditions,
    n_products: int,
    dense_is_concentrate: bool,
) -> dict[str, float]:
    result = simulate(feed, params, settings, operating, n_products, dense_is_concentrate)
    return {product: result.yield_of(product) for product in result.products}


def match_mass_pulls(
    feed: Feed,
    params: PartitionParameters,
    settings: SplitterSettings,
    operating: OperatingConditions,
    n_products: int,
    dense_is_concentrate: bool,
    concentrate_yield: float,
    middlings_yield: float | None = None,
) -> tuple[SplitterSettings, dict[str, float], list[str]]:
    """Return openings, the yields actually achieved, and any warnings.

    Yields are fractions of feed. For two products only the concentrate yield
    is used. Tails take the remainder.
    """
    notes: list[str] = []
    updated = replace(settings)

    def outer_stream(opening: float) -> float:
        trial = replace(updated, outer_opening=opening)
        yields = _yields(feed, params, trial, operating, n_products, dense_is_concentrate)
        if dense_is_concentrate:
            return yields.get("tails", 0.0)
        return yields.get("concentrate", 0.0)

    def inner_stream(opening: float) -> float:
        trial = replace(updated, inner_opening=opening)
        yields = _yields(feed, params, trial, operating, n_products, dense_is_concentrate)
        if dense_is_concentrate:
            return yields.get("concentrate", 0.0)
        return yields.get("tails", 0.0)

    if n_products < 3:
        if dense_is_concentrate:
            outer_target = 1.0 - concentrate_yield
        else:
            outer_target = concentrate_yield
        # A wider opening lowers the cut and sends less mass to the outer stream.
        opening, hit = _bisect(outer_stream, outer_target, increasing=False)
        updated.outer_opening = opening
        if not hit:
            notes.append(
                "The concentrate yield was set as close as the cut-density range allows."
            )
    else:
        if middlings_yield is None:
            raise ValueError("A three-product target needs a middlings yield.")
        if dense_is_concentrate:
            outer_target = 1.0 - concentrate_yield - middlings_yield
            inner_target = concentrate_yield
        else:
            # Concentrate is the outer stream. Tails are the inner stream.
            outer_target = concentrate_yield
            inner_target = 1.0 - concentrate_yield - middlings_yield
        opening, hit = _bisect(outer_stream, outer_target, increasing=False)
        updated.outer_opening = opening
        if not hit:
            notes.append(
                "The outer splitter was set as close as the cut-density range allows."
            )
        # A wider inner opening lowers the inner cut and increases the dense stream.
        opening, hit = _bisect(inner_stream, inner_target, increasing=True)
        updated.inner_opening = opening
        if not hit:
            notes.append(
                "The inner splitter was set as close as the cut-density range allows."
            )

    achieved = _yields(feed, params, updated, operating, n_products, dense_is_concentrate)
    for name, opening in (("outer", updated.outer_opening), ("inner", updated.inner_opening)):
        if n_products < 3 and name == "inner":
            continue
        if opening < 0.0 or opening > 100.0:
            notes.append(
                f"The {name} splitter effective opening is {opening:.0f}, "
                "which is outside the usual 0 to 100 scale. The base cut densities "
                "were left unchanged."
            )
    return updated, achieved, notes


def _bisect(func, target: float, increasing: bool) -> tuple[float, bool]:
    """Search openings from -100 to 250. ``increasing`` describes func(opening)."""
    low, high = -100.0, 250.0
    y_low = func(low)
    y_high = func(high)
    if not increasing:
        # Flip the bracket so the search still assumes an increasing function.
        def flipped(opening: float) -> float:
            return -func(opening)

        opening, hit = _bisect(flipped, -target, increasing=True)
        return opening, hit

    span = max(abs(y_high - y_low), 1e-9)
    tol = max(0.001, 0.002 * span)
    if target <= y_low + tol:
        return low, abs(y_low - target) <= tol
    if target >= y_high - tol:
        return high, abs(y_high - target) <= tol
    for _ in range(50):
        mid = 0.5 * (low + high)
        y_mid = func(mid)
        if abs(y_mid - target) <= tol:
            return mid, True
        if y_mid < target:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high), True
