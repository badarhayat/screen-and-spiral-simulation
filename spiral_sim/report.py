"""Text report for a spiral simulation.

The layout follows the quantities a metallurgist checks first: yield, grade,
recovery, size distribution, and the mineral balance, then the partition
parameters that produced them.
"""

from __future__ import annotations

import textwrap

from spiral_sim.calibrate import comparisons
from spiral_sim.defaults import contrast_warning
from spiral_sim.model import shift_cut
from spiral_sim.session import Session
from spiral_sim.simulate import PRODUCT_LABELS, SimulationResult


SOURCE_SUMMARY = {
    "density-defaults": (
        "These are approximate starting parameters. The cut densities were "
        "placed between your lightest and heaviest minerals. The sharpness and "
        "the fine particle bypass are typical starting values for a spiral, "
        "not a measurement of your machine."
    ),
    "heavy-mineral-preset": (
        "These are approximate textbook cuts for a heavy-mineral sands spiral: "
        "3.20 and 3.95 g/cm3 at 150 μm, with typical spiral sharpness and bypass. "
        "They are not a measurement of your machine."
    ),
    "fitted": "The partition parameters were fitted to the plant data in this session.",
    "manual": "The partition parameters include values edited in this session.",
    "loaded": "The partition parameters were loaded from a saved session.",
}

CALIBRATION_NOTE = (
    "Results improve significantly after calibration with real spiral data: "
    "mass yields and product grades (or recoveries) measured at a known splitter setting."
)


def _wrap(text: str, width: int = 78) -> str:
    return textwrap.fill(text.strip(), width=width)


def _pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.{digits}f}"


def _num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    """First column left-aligned, the rest right-aligned."""
    columns = len(headers)
    widths = [len(header) for header in headers]
    rendered = [[str(cell) for cell in row] for row in rows]
    for row in rendered:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def paint(row: list[str]) -> str:
        parts = []
        for index in range(columns):
            cell = row[index]
            if index == 0:
                parts.append(cell.ljust(widths[index]))
            else:
                parts.append(cell.rjust(widths[index]))
        return "  ".join(parts)

    lines = [paint(headers), paint(["-" * width for width in widths])]
    lines.extend(paint(row) for row in rendered)
    return "\n".join(lines)


def _product_headers(result: SimulationResult, include_feed: bool) -> list[str]:
    headers = ["Item"]
    if include_feed:
        headers.append("Feed")
    for product in result.products:
        headers.append(PRODUCT_LABELS[product])
    return headers


def render(session: Session, result: SimulationResult) -> str:
    """Full plain-text report."""
    lines: list[str] = []
    title = "SPIRAL CONCENTRATOR SIMULATION"
    lines.append(title)
    lines.append("-" * len(title))
    if session.n_products >= 3:
        structure = (
            "Three products are two cuts in series: the outer splitter, then the inner splitter."
        )
    else:
        structure = "Two products are a single cut between concentrate and tails."
    lines.append(
        _wrap(
            "Model: generalized bivariate size-density partition, after R.P. King, "
            "Modeling and Simulation of Mineral Processing Systems, Chapter 7. "
            f"{structure} At each size the density curve uses the "
            f"{session.params.curve_label()}, plus a short-circuit bypass."
        )
    )
    lines.append("")
    source = SOURCE_SUMMARY.get(session.parameter_source, "Partition parameters for this run.")
    lines.append(_wrap(source))
    if session.source_note:
        lines.append(_wrap(session.source_note))
    if session.fit_message and session.parameter_source == "fitted":
        lines.append(_wrap(session.fit_message))
    lines.append("")

    warnings = list(result.warnings)
    contrast = contrast_warning(session.feed.minerals)
    if contrast:
        warnings.append(contrast)
    for mineral in session.feed.minerals:
        if mineral.density < session.params.rho_fluid:
            warnings.append(
                f"{mineral.name} at {mineral.density:.2f} g/cm3 is lighter than the "
                f"fluid ({session.params.rho_fluid:.2f} g/cm3), so it travels with the outer stream."
            )
        elif mineral.density == session.params.rho_fluid:
            warnings.append(
                f"{mineral.name} has the same density as the fluid, so the settling cut does not act on it."
            )
    if warnings:
        lines.append("Notes on this run")
        for warning in warnings:
            lines.append(_wrap(warning))
        lines.append("")

    lines.append("Feed")
    if session.operating.feed_rate_tph is None:
        lines.append("  Solids rate: not set (results are per 100 mass units of feed)")
    else:
        lines.append(f"  Solids rate: {session.operating.feed_rate_tph:.3g} t/h")
    if session.operating.pct_solids is None:
        lines.append(
            f"  Percent solids: {session.params.solids_ref:.0f}% assumed for the pulp-density correction"
        )
    else:
        lines.append(f"  Percent solids: {session.operating.pct_solids:.1f}% by weight")
    if session.dense_is_concentrate:
        lines.append("  Dense stream: concentrate (inner product). Usual for heavy minerals and metals.")
    else:
        lines.append("  Dense stream: tails (inner product). Concentrate is the light outer product.")
    lines.append(f"  Products: {len(result.products)}")
    lines.append("  Minerals:")
    for index, mineral in enumerate(session.feed.minerals):
        grade = session.feed.overall_grade(index)
        lines.append(
            f"    {mineral.name}: {mineral.density:.3f} g/cm3, {_pct(grade)}% of feed"
        )
    lines.append("")

    # Mass yield
    yield_row = ["Mass yield %", _pct(1.0)]
    for product in result.products:
        yield_row.append(_pct(result.yield_of(product)))
    rows = [yield_row]
    if session.operating.feed_rate_tph is not None:
        rate = session.operating.feed_rate_tph
        rate_row = ["Solids t/h", f"{rate:.2f}"]
        for product in result.products:
            rate_row.append(f"{rate * result.yield_of(product):.2f}")
        rows.append(rate_row)
    lines.append("Mass yield")
    lines.append(format_table(_product_headers(result, include_feed=True), rows))
    lines.append("")

    # Grades
    grade_rows = []
    for index, name in enumerate(result.mineral_names):
        row = [name, _pct(session.feed.overall_grade(index))]
        for product in result.products:
            row.append(_pct(result.grade_of(product, index)))
        grade_rows.append(row)
    lines.append("Grade of each mineral, mass %")
    lines.append(format_table(_product_headers(result, include_feed=True), grade_rows))
    lines.append("")

    # Recoveries
    recovery_rows = []
    for index, name in enumerate(result.mineral_names):
        row = [name, "100.00" if result.feed_mineral[index] > 1e-15 else "n/a"]
        for product in result.products:
            row.append(_pct(result.recovery_of(product, index)))
        recovery_rows.append(row)
    lines.append("Recovery of each mineral, % of that mineral in the feed")
    lines.append(format_table(_product_headers(result, include_feed=True), recovery_rows))
    lines.append("")

    # Size distribution within each product
    size_rows = []
    for index, label in enumerate(result.size_labels):
        row = [label, _pct(result.feed_size[index])]
        for product in result.products:
            row.append(_pct(result.size_distribution(product, index)))
        size_rows.append(row)
    lines.append("Size distribution of each product, mass % of that product")
    lines.append("Each column sums to 100.")
    lines.append(format_table(_product_headers(result, include_feed=True), size_rows))
    lines.append("")

    # Where each size class went
    dest_headers = ["Size class"] + [PRODUCT_LABELS[product] for product in result.products]
    dest_rows = []
    for index, label in enumerate(result.size_labels):
        row = [label]
        for product in result.products:
            row.append(_pct(result.size_to_product(index, product)))
        dest_rows.append(row)
    lines.append("Where each size class reports, % of that size class")
    lines.append("Each row sums to 100.")
    lines.append(format_table(dest_headers, dest_rows))
    lines.append("")

    # Mineral balance per 100 units
    balance_headers = ["Mineral", "Feed"] + [PRODUCT_LABELS[p] for p in result.products] + ["Sum"]
    balance_rows = []
    for index, name in enumerate(result.mineral_names):
        feed_mass = result.feed_mineral[index] * 100.0
        produced = [result.mineral_mass[product][index] * 100.0 for product in result.products]
        row = [name, f"{feed_mass:.2f}"]
        row.extend(f"{value:.2f}" for value in produced)
        row.append(f"{sum(produced):.2f}")
        balance_rows.append(row)
    feed_total = 100.0
    produced_totals = [result.total[product] * 100.0 for product in result.products]
    total_row = ["TOTAL", f"{feed_total:.2f}"]
    total_row.extend(f"{value:.2f}" for value in produced_totals)
    total_row.append(f"{sum(produced_totals):.2f}")
    balance_rows.append(total_row)
    lines.append("Mineral and mass balance, mass per 100 mass units of feed")
    lines.append(format_table(balance_headers, balance_rows))
    gap = result.closure_error() * 100.0
    if gap < 1e-6:
        lines.append("Balance closure: closed.")
    else:
        lines.append(f"Balance closure: largest gap is {gap:.4f} per 100 units of feed.")
    if session.operating.feed_rate_tph is not None:
        lines.append(
            "Multiply any column by the feed rate and divide by 100 to read it in t/h."
        )
    lines.append("")

    lines.extend(_parameter_section(session, result))
    lines.append("")
    if session.plant is not None and session.plant.observation_count() > 0:
        lines.extend(_observed_section(session, result))
        lines.append("")
    lines.append(_wrap(CALIBRATION_NOTE))
    lines.append("")
    return "\n".join(lines)


def _parameter_section(session: Session, result: SimulationResult) -> list[str]:
    params = session.params
    settings = session.settings
    lines = ["Partition parameters used"]
    lines.append(_wrap(
        "Stored cut densities apply at the reference size when the splitter "
        "opening is 50. A higher opening lowers the cut density and increases "
        "the flow to the inner, denser stream. The size table below is what "
        "the balance actually used."
    ))
    outer_ref, _ = shift_cut(params.rho50_outer_ref, settings.outer_opening, params)
    lines.append(f"  Curve: {params.curve_label()}")
    lines.append(f"  Reference size: {params.d_ref:.3f} mm")
    lines.append(f"  Outer splitter opening: {settings.outer_opening:.1f}")
    lines.append(
        f"  Outer cut density at {params.d_ref:.3f} mm, opening 50: "
        f"{params.rho50_outer_ref:.3f} g/cm3"
    )
    lines.append(
        f"  Outer cut density at {params.d_ref:.3f} mm, this opening: "
        f"{outer_ref:.3f} g/cm3"
    )
    if session.n_products >= 3:
        inner_ref, _ = shift_cut(params.rho50_inner_ref, settings.inner_opening, params)
        lines.append(f"  Inner splitter opening: {settings.inner_opening:.1f}")
        lines.append(
            f"  Inner cut density at {params.d_ref:.3f} mm, opening 50: "
            f"{params.rho50_inner_ref:.3f} g/cm3"
        )
        lines.append(
            f"  Inner cut density at {params.d_ref:.3f} mm, this opening: "
            f"{inner_ref:.3f} g/cm3"
        )
    lines.append(f"  Imperfection I* at the reference size: {params.imperfection_ref:.3f}")
    lines.append(f"  Size exponent on cut density, n: {params.n_cut:.3f}")
    lines.append(f"  Size exponent on imperfection, m: {params.m_sharp:.3f}")
    lines.append(
        f"  Coarse bypass to the outer product: {params.bypass_outer_coarse * 100:.2f}%"
    )
    lines.append(
        f"  Extra fine-particle bypass amplitude: {params.bypass_outer_slime * 100:.2f}%"
    )
    lines.append(f"  Fine-particle characteristic size: {params.d_slime:.3f} mm")
    lines.append(f"  Bypass to the inner product: {params.bypass_inner * 100:.2f}%")
    lines.append(
        f"  Splitter sensitivity: {params.splitter_sensitivity:.2f} g/cm3 "
        "across the full 0 to 100 opening range"
    )
    if session.plant is not None:
        lines.append(
            "  Plant survey openings: "
            f"outer {session.plant.splitter_outer:.1f}, "
            f"inner {session.plant.splitter_inner:.1f}"
        )
    if settings.outer_opening < 0.0 or settings.outer_opening > 100.0:
        lines.append(
            f"  The outer opening {settings.outer_opening:.0f} is outside the usual 0 to 100 scale."
        )
    if session.n_products >= 3 and (
        settings.inner_opening < 0.0 or settings.inner_opening > 100.0
    ):
        lines.append(
            f"  The inner opening {settings.inner_opening:.0f} is outside the usual 0 to 100 scale."
        )
    lines.append("")
    lines.append(
        _wrap(
            "Size-dependent cuts. Ep is the Ecart probable: the corrected curve "
            "is 75% one Ep above the cut and 25% one Ep below it."
        )
    )
    headers = ["Size class", "Rep. mm", "Cut outer", "Ep outer", "Bypass out %"]
    if session.n_products >= 3:
        headers[4:4] = ["Cut inner", "Ep inner"]
    rows = []
    for label, profile in zip(result.size_labels, result.profiles):
        row = [
            label,
            _num(profile.size_mm, 3),
            _num(profile.rho50_outer, 3),
            _num(profile.ep_outer, 3),
        ]
        if session.n_products >= 3:
            row.extend([_num(profile.rho50_inner, 3), _num(profile.ep_inner, 3)])
        row.append(_pct(profile.bypass_outer))
        rows.append(row)
    lines.append(format_table(headers, rows))
    lines.append(f"Inner-product bypass used at every size: {_pct(params.bypass_inner)}%.")
    return lines


def _observed_section(session: Session, result: SimulationResult) -> list[str]:
    assert session.plant is not None
    rows_raw = comparisons(
        session.feed,
        result,
        session.plant,
        include_redundant_recoveries=True,
    )
    lines = ["Plant data compared with this run"]
    lines.append("Differences are predicted minus observed, in percentage points.")
    table_rows = []
    for label, observed, predicted in rows_raw:
        if observed is None and predicted is None:
            continue
        gap = None
        if observed is not None and predicted is not None:
            gap = (predicted - observed) * 100.0
        table_rows.append(
            [
                label,
                _pct(observed),
                _pct(predicted),
                "n/a" if gap is None else f"{gap:+.2f}",
            ]
        )
    if not table_rows:
        lines.append("No comparable numbers were stored.")
        return lines
    lines.append(format_table(["Measurement", "Observed %", "Predicted %", "Gap"], table_rows))
    return lines


def render_fit(
    before_params,
    report,
    n_products: int,
) -> str:
    """Short before/after view shown when a fit finishes."""
    after = report.parameters
    lines = ["Calibration result", "------------------", _wrap(report.message), ""]
    headers = ["Parameter", "Before", "Fitted"]
    rows = [
        ["Outer cut at reference size, g/cm3", f"{before_params.rho50_outer_ref:.3f}", f"{after.rho50_outer_ref:.3f}"],
        ["Imperfection I*", f"{before_params.imperfection_ref:.3f}", f"{after.imperfection_ref:.3f}"],
        ["Coarse outer bypass %", f"{before_params.bypass_outer_coarse * 100:.2f}", f"{after.bypass_outer_coarse * 100:.2f}"],
        ["Fine bypass amplitude %", f"{before_params.bypass_outer_slime * 100:.2f}", f"{after.bypass_outer_slime * 100:.2f}"],
    ]
    if n_products >= 3:
        rows.insert(
            1,
            ["Inner cut at reference size, g/cm3", f"{before_params.rho50_inner_ref:.3f}", f"{after.rho50_inner_ref:.3f}"],
        )
    rows.append(["Size exponent n", f"{before_params.n_cut:.3f}", f"{after.n_cut:.3f}"])
    rows.append(["Sharpness exponent m", f"{before_params.m_sharp:.3f}", f"{after.m_sharp:.3f}"])
    lines.append(format_table(headers, rows))
    lines.append("")
    lines.append("Observed survey versus the fitted model")
    table_rows = []
    for label, observed, predicted in report.comparisons:
        gap = None
        if observed is not None and predicted is not None:
            gap = (predicted - observed) * 100.0
        table_rows.append(
            [
                label,
                _pct(observed),
                _pct(predicted),
                "n/a" if gap is None else f"{gap:+.2f}",
            ]
        )
    lines.append(format_table(["Measurement", "Observed %", "Fitted %", "Gap"], table_rows))
    lines.append("")
    return "\n".join(lines)
