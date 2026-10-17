"""Starting partition parameters when the user has no plant survey.

Two starting points are provided.

1. Density-based cuts. The outer and inner cut densities are placed between
   the lightest and heaviest minerals the user entered. This is the general
   starting point: the equations are the same for every ore, and the cuts
   simply move to sit inside that ore's density range. A spiral cannot
   upgrade minerals that do not straddle its cut, so putting the cuts inside
   the user's density range is the scientifically useful default.

2. A heavy-mineral sands preset. Fixed cuts of 3.20 and 3.95 g/cm³ at
   150 μm match the range commonly reported for quartz–rutile–ilmenite–zircon
   spirals. Use this only when that is actually the duty.

Sharpness and bypass are typical of a spiral in good condition and do not
depend on the ore names. They are still approximate. Real pitch, wash water,
and splitter hardware move them, which is why calibration exists.

Nothing here claims to be a fitted model. Callers must tell the user that.
"""

from __future__ import annotations

from spiral_sim.feed import Mineral
from spiral_sim.model import PartitionParameters


def heavy_mineral_preset() -> PartitionParameters:
    """Textbook starting cuts for a typical heavy-mineral spiral at 150 μm."""
    return PartitionParameters(
        rho50_outer_ref=3.20,
        rho50_inner_ref=3.95,
    )


def place_cut_densities(
    minerals: list[Mineral],
    rho_fluid: float = 1.0,
) -> tuple[float, float, str]:
    """Choose outer and inner reference cuts from the minerals present.

    Returns (rho50_outer, rho50_inner, note).

    The outer cut is placed 35% of the way from the lightest mineral to the
    heaviest, and the inner cut 70% of the way. That is a rougher/cleaner
    pair: most of the light mineral starts on the tails side of the outer
    cut, and most of the heavy mineral starts on the concentrate side of the
    inner cut, with room between them for a middling. When every mineral has
    essentially the same density, the cuts are placed just either side of
    that density so the run still shows the (weak) size effect, and the note
    says that upgrading is not possible.
    """
    if not minerals:
        return 3.20, 3.95, "No minerals were available, so heavy-mineral cuts were used."
    densities = [m.density for m in minerals]
    rho_light = min(densities)
    rho_heavy = max(densities)
    span = rho_heavy - rho_light
    if span < 0.05:
        outer = max(rho_fluid + 0.1, rho_light - 0.10)
        inner = outer + 0.20
        note = (
            f"Every mineral has essentially the same density ({rho_light:.2f} g/cm3). "
            "A spiral separates by density, so this feed will not upgrade. "
            "The cuts were placed just either side of that density to show the "
            "small size effect only."
        )
        return outer, inner, note
    outer = rho_light + 0.35 * span
    inner = rho_light + 0.70 * span
    outer = max(outer, rho_fluid + 0.10)
    if inner < outer + 0.08:
        inner = outer + 0.08
    note = (
        f"Cut densities were placed between the lightest mineral "
        f"({rho_light:.2f} g/cm3) and the heaviest ({rho_heavy:.2f} g/cm3): "
        f"the outer cut at 35% of that gap and the inner cut at 70%. "
        "That is a typical uncalibrated rougher/cleaner starting point, "
        "not a measurement on your spiral."
    )
    return outer, inner, note


def defaults_from_minerals(minerals: list[Mineral]) -> tuple[PartitionParameters, str]:
    """General starting parameters for any mineral suite."""
    params = PartitionParameters()
    outer, inner, note = place_cut_densities(minerals, params.rho_fluid)
    params.rho50_outer_ref = outer
    params.rho50_inner_ref = inner
    return params, note


def contrast_warning(minerals: list[Mineral]) -> str | None:
    """Warn when the density range is too narrow for a useful split."""
    if len(minerals) < 2:
        return (
            "The feed contains one mineral. Every product grade is 100% of that "
            "mineral. The spiral can only move different sizes into different products."
        )
    span = max(m.density for m in minerals) - min(m.density for m in minerals)
    if span < 0.15:
        return (
            f"The mineral densities span only {span:.2f} g/cm3. A spiral needs a "
            "wider density gap than this to upgrade the feed. Expect little separation "
            "until the mineral list includes the real density contrast."
        )
    return None
