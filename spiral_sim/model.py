"""Generalized size-density partition functions for a spiral concentrator.

The formulation follows R.P. King's treatment of gravity separators
(Modeling and Simulation of Mineral Processing Systems, Chapter 7). King
describes recovery to the dense product by a bivariate partition function
R(d, ρ): at each particle size the curve is a sigmoid in density, the cut
density and the sharpness both vary with size, and a short-circuit (bypass)
fraction does not separate at all.

A spiral with concentrate, middlings, and tails is represented here as two
of those cuts in sequence:

1. Outer cut — splits tails from the combined inner product.
2. Inner cut — splits that inner product into concentrate and middlings.

The same equations are used for every mineral. Only the particle density
and size change. Nothing in the partition function is hard-wired to a
particular ore.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# Ep / σ for a normal partition curve. At one Ecart probable above the cut
# the error-function curve equals 0.75, because
# Ep = σ * sqrt(2) * erf^{-1}(0.5) ≈ 0.67449 σ.
EP_OVER_SIGMA = 0.6744897501960817
LN_3 = math.log(3.0)


def corrected_partition(density: float, rho50: float, ep: float, curve: str = "erf") -> float:
    """Corrected fraction reporting to the dense side of one cut.

    No bypass is applied here. The result is 0.5 at ``rho50`` and about 0.75
    at ``rho50 + ep`` for both the error-function and the logistic forms.
    ``ep`` is the Ecart probable, (ρ75 − ρ25) / 2.
    """
    ep = max(float(ep), 1e-6)
    z = (float(density) - float(rho50)) / ep
    if curve == "logistic":
        # Logistic sigmoid scaled so that z = +1 (one Ep above the cut) gives 0.75.
        arg = max(-60.0, min(60.0, LN_3 * z))
        return 1.0 / (1.0 + math.exp(-arg))
    # Error-function form used by King for partition curves.
    arg = z * EP_OVER_SIGMA / math.sqrt(2.0)
    return 0.5 * (1.0 + math.erf(arg))


def _limit_bypass(bypass_outer: float, bypass_inner: float) -> tuple[float, float]:
    """Keep short-circuit fractions non-negative and leave room to separate."""
    outer = max(0.0, float(bypass_outer))
    inner = max(0.0, float(bypass_inner))
    if outer + inner > 0.98:
        scale = 0.98 / (outer + inner)
        outer *= scale
        inner *= scale
    return outer, inner


def with_bypass(corrected: float, bypass_outer: float, bypass_inner: float) -> float:
    """King short-circuit form of a single cut.

    R = C + (1 − A − C) * Ψ
    A is the fraction that short-circuits to the outer (light) product,
    C short-circuits to the inner (dense) product, and Ψ is the corrected
    partition. The remainder is actually separated by the cut.
    """
    outer, inner = _limit_bypass(bypass_outer, bypass_inner)
    return inner + (1.0 - outer - inner) * float(corrected)


@dataclass
class PartitionParameters:
    """Size-dependent cut, sharpness, and bypass parameters.

    Reference cut densities and the reference imperfection apply at ``d_ref``
    millimetres when both splitter openings are 50. Openings move the cuts;
    they do not change these stored base values.

    Cut density at size d (g/cm³):

        ρ50(d) = ρ_fluid + (ρ50_ref − ρ_fluid) * (d_ref / d) ** n_cut

    Finer particles receive a higher cut density when ``n_cut`` is positive,
    which is the usual spiral trend: drag keeps fines from migrating inward
    unless they are denser.

    Sharpness, written as an imperfection (dimensionless):

        I(d) = I_ref * (d_ref / d) ** m_sharp
        Ep(d) = I(d) * (ρ50(d) − ρ_fluid)

    Bypass to the outer product (entrainment), a fraction of the size class:

        A(d) = A_coarse + A_slime * exp(−(d / d_slime) ** p)

    ``bypass_inner`` is a small density-independent short-circuit into the
    innermost stream.
    """

    rho50_outer_ref: float = 3.20
    rho50_inner_ref: float = 3.95
    d_ref: float = 0.150
    n_cut: float = 0.18
    imperfection_ref: float = 0.10
    m_sharp: float = 0.25
    bypass_outer_coarse: float = 0.02
    bypass_outer_slime: float = 0.25
    d_slime: float = 0.075
    slime_exponent: float = 1.5
    bypass_inner: float = 0.005
    rho_fluid: float = 1.0
    curve: str = "erf"
    solids_ref: float = 30.0
    # Extra relative Ep per percentage point of solids away from solids_ref.
    solids_ep_coeff: float = 0.015
    # Extra outer bypass per percentage point of solids above solids_ref.
    solids_bypass_coeff: float = 0.004
    # How far the reference cut moves, in g/cm³, when an opening goes from 0 to 100.
    splitter_sensitivity: float = 1.20

    def curve_label(self) -> str:
        if self.curve == "logistic":
            return "logistic sigmoid"
        return "error function"


@dataclass
class SplitterSettings:
    """Splitter openings on a 0–100 scale.

    50 is the position at which the stored reference cut densities apply.
    A higher opening lowers the cut density and pulls more solids inward
    (a larger dense-product mass yield). Values outside 0–100 are allowed
    so a requested mass pull can be matched even when it sits outside the
    usual mechanical scale; the report flags that case.
    """

    outer_opening: float = 50.0
    inner_opening: float = 50.0


@dataclass
class OperatingConditions:
    """Optional plant operating numbers. Partitioning is on dry solids."""

    feed_rate_tph: float | None = None
    pct_solids: float | None = None


@dataclass
class SizeProfile:
    """Cut density, Ep, and bypass evaluated at one particle size."""

    size_mm: float
    rho50_outer: float
    ep_outer: float
    rho50_inner: float | None
    ep_inner: float | None
    bypass_outer: float
    bypass_inner: float
    outer_clamped: bool
    inner_clamped: bool


def solids_factors(pct_solids: float | None, params: PartitionParameters) -> tuple[float, float, bool]:
    """Mild pulp-density correction.

    Returns (ep_factor, extra_outer_bypass, solids_were_assumed).

    Percent solids is not a primary variable in King's partition function.
    High solids increase hindered settling, which blunts the cut and entrains
    more fines. The factors below are a small, explicit correction around a
    typical spiral feed of about 30% solids so that an unusually thick pulp
    does not look as sharp as a dilute one. Calibration still dominates.
    """
    assumed = pct_solids is None
    solids = params.solids_ref if pct_solids is None else float(pct_solids)
    delta = solids - params.solids_ref
    ep_factor = 1.0 + params.solids_ep_coeff * max(0.0, delta)
    ep_factor += 0.4 * params.solids_ep_coeff * max(0.0, -delta)
    extra_bypass = params.solids_bypass_coeff * max(0.0, delta)
    return ep_factor, extra_bypass, assumed


def shift_cut(rho50_ref: float, opening: float, params: PartitionParameters) -> tuple[float, bool]:
    """Reference cut density after the splitter opening is applied."""
    shift = params.splitter_sensitivity * ((50.0 - float(opening)) / 100.0)
    cut = float(rho50_ref) + shift
    low = params.rho_fluid + 0.05
    high = 12.0
    clamped_cut = min(max(cut, low), high)
    return clamped_cut, abs(clamped_cut - cut) > 1e-9


def representative_size(d_low: float, d_high: float) -> float:
    """Size (mm) used to evaluate the partition curve for a size class.

    Closed classes use the geometric mean. A class that starts at zero
    (minus the finest screen) uses half the top size, because a geometric
    mean with a zero bottom is not defined.
    """
    low = max(0.0, float(d_low))
    high = max(low, float(d_high))
    if high <= 0.0:
        return 0.001
    if low <= 0.0:
        return max(high * 0.5, 1e-4)
    return math.sqrt(low * high)


def cut_density_at_size(size_mm: float, rho50_ref: float, params: PartitionParameters) -> float:
    """Size-dependent cut density. See PartitionParameters."""
    size = max(float(size_mm), 1e-4)
    factor = (params.d_ref / size) ** params.n_cut
    reduced = max(float(rho50_ref) - params.rho_fluid, 0.05)
    return params.rho_fluid + reduced * factor


def ep_at_size(
    size_mm: float,
    rho50: float,
    params: PartitionParameters,
    ep_factor: float = 1.0,
) -> float:
    """Ecart probable at this size, widened for fine particles."""
    size = max(float(size_mm), 1e-4)
    imperfection = params.imperfection_ref * (params.d_ref / size) ** params.m_sharp
    imperfection = min(max(imperfection, 0.005), 0.80)
    ep = imperfection * max(rho50 - params.rho_fluid, 0.05) * max(ep_factor, 0.2)
    return max(ep, 0.01)


def bypass_outer_at_size(
    size_mm: float,
    params: PartitionParameters,
    extra: float = 0.0,
) -> float:
    """Short-circuit fraction to the outer product at this size."""
    size = max(float(size_mm), 1e-6)
    slime = params.bypass_outer_slime * math.exp(
        -((size / max(params.d_slime, 1e-6)) ** params.slime_exponent)
    )
    return min(0.95, max(0.0, params.bypass_outer_coarse + slime + extra))


def profile_at_size(
    size_mm: float,
    params: PartitionParameters,
    settings: SplitterSettings,
    pct_solids: float | None,
    n_products: int,
) -> SizeProfile:
    """Evaluate both cuts, or the single cut, at one particle size."""
    ep_factor, extra_bypass, _assumed = solids_factors(pct_solids, params)
    rho_outer_ref, outer_clamped = shift_cut(params.rho50_outer_ref, settings.outer_opening, params)
    rho50_outer = cut_density_at_size(size_mm, rho_outer_ref, params)
    ep_outer = ep_at_size(size_mm, rho50_outer, params, ep_factor)
    bypass_outer = bypass_outer_at_size(size_mm, params, extra_bypass)
    bypass_inner = min(0.95, max(0.0, params.bypass_inner))

    rho50_inner = None
    ep_inner = None
    inner_clamped = False
    if n_products >= 3:
        rho_inner_ref, inner_clamped = shift_cut(
            params.rho50_inner_ref, settings.inner_opening, params
        )
        # The inner splitter is a denser cut than the outer splitter.
        if rho_inner_ref < rho_outer_ref + 0.02:
            rho_inner_ref = rho_outer_ref + 0.02
            inner_clamped = True
        rho50_inner = cut_density_at_size(size_mm, rho_inner_ref, params)
        ep_inner = ep_at_size(size_mm, rho50_inner, params, ep_factor)
        if rho50_inner < rho50_outer:
            rho50_inner = rho50_outer
            inner_clamped = True

    return SizeProfile(
        size_mm=size_mm,
        rho50_outer=rho50_outer,
        ep_outer=ep_outer,
        rho50_inner=rho50_inner,
        ep_inner=ep_inner,
        bypass_outer=bypass_outer,
        bypass_inner=bypass_inner,
        outer_clamped=outer_clamped,
        inner_clamped=inner_clamped,
    )


def stream_recoveries(
    size_mm: float,
    density: float,
    params: PartitionParameters,
    settings: SplitterSettings,
    pct_solids: float | None,
    n_products: int,
) -> tuple[float, float, float]:
    """Return (R_inner, R_middling, R_outer) for one size and density.

    R_inner is recovery to the dense innermost stream, R_outer to the light
    outermost stream. For two products the middling recovery is zero and the
    outer splitter is the only cut.
    """
    prof = profile_at_size(size_mm, params, settings, pct_solids, n_products)
    psi_outer = corrected_partition(density, prof.rho50_outer, prof.ep_outer, params.curve)
    if n_products < 3:
        r_inner = with_bypass(psi_outer, prof.bypass_outer, prof.bypass_inner)
        return r_inner, 0.0, 1.0 - r_inner

    psi_inner = corrected_partition(
        density, prof.rho50_inner or prof.rho50_outer, prof.ep_inner or prof.ep_outer, params.curve
    )
    # A feasible sequential split: nothing can pass the inner splitter
    # unless it has also passed the outer splitter.
    psi_inner = min(psi_inner, psi_outer)
    bypass_o, bypass_i = _limit_bypass(prof.bypass_outer, prof.bypass_inner)
    separated = 1.0 - bypass_o - bypass_i
    r_inner = bypass_i + separated * psi_inner
    r_outer = bypass_o + separated * (1.0 - psi_outer)
    r_mid = separated * (psi_outer - psi_inner)
    total = r_inner + r_mid + r_outer
    if total <= 0.0:
        return 0.0, 0.0, 1.0
    return r_inner / total, r_mid / total, r_outer / total


def product_recoveries(
    size_mm: float,
    density: float,
    params: PartitionParameters,
    settings: SplitterSettings,
    pct_solids: float | None,
    n_products: int,
    dense_is_concentrate: bool,
) -> dict[str, float]:
    """Map the three physical streams onto Concentrate, Middlings, and Tails.

    On a heavy-mineral or metal spiral the concentrate is the dense inner
    product. On a coal spiral the concentrate is the light outer product.
    The partition equations themselves do not change.
    """
    r_inner, r_mid, r_outer = stream_recoveries(
        size_mm, density, params, settings, pct_solids, n_products
    )
    if dense_is_concentrate:
        return {"concentrate": r_inner, "middlings": r_mid, "tails": r_outer}
    return {"concentrate": r_outer, "middlings": r_mid, "tails": r_inner}
