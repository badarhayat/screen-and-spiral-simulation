"""Fit cut density, sharpness, and bypass to a plant survey.

The fit is ordinary least squares on the numbers the user was able to
measure: product mass yields, mineral grades, mineral recoveries, and
(when they have it) the mass split of each size class. Missing numbers are
skipped, so a partial survey can still be used.

Parameters that a bulk survey can actually identify are adjusted:

- outer cut density
- inner cut density (three-product spirals)
- imperfection
- coarse bypass and the extra fine-particle bypass

When the user also supplies the mass split by size, the size exponents on
the cut and on the imperfection are freed as well. With only bulk grades
those exponents are left at their starting values, because a single set of
product assays does not identify them.

The optimizer is a small Nelder-Mead search in a transformed parameter
space, restarted from several points. It uses only the Python standard
library so the program runs without a scientific stack.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from spiral_sim.feed import Feed
from spiral_sim.model import OperatingConditions, PartitionParameters, SplitterSettings
from spiral_sim.simulate import SimulationResult, simulate


@dataclass
class PlantData:
    """Observed spiral performance. Fractions are 0–1. None means "not measured"."""

    yields: dict[str, float | None] = field(default_factory=dict)
    grades: dict[str, dict[str, float | None]] = field(default_factory=dict)
    recoveries: dict[str, dict[str, float | None]] = field(default_factory=dict)
    # One entry per feed size class. Values are the fraction of THAT class
    # reporting to each product. None skips the class.
    size_splits: list[dict[str, float | None]] | None = None
    splitter_outer: float = 50.0
    splitter_inner: float = 50.0

    def observation_count(self) -> int:
        count = sum(1 for v in self.yields.values() if v is not None)
        for grades in self.grades.values():
            count += sum(1 for v in grades.values() if v is not None)
        for recoveries in self.recoveries.values():
            count += sum(1 for v in recoveries.values() if v is not None)
        if self.size_splits:
            for splits in self.size_splits:
                count += sum(1 for v in splits.values() if v is not None)
        return count

    def has_size_splits(self) -> bool:
        if not self.size_splits:
            return False
        return any(any(v is not None for v in splits.values()) for splits in self.size_splits)


@dataclass
class FitReport:
    parameters: PartitionParameters
    objective: float
    yield_mae_pp: float | None
    grade_mae_pp: float | None
    recovery_mae_pp: float | None
    size_split_mae_pp: float | None
    n_points: int
    comparisons: list[tuple[str, float | None, float | None]]
    message: str
    sparse: bool


def _settings_from_plant(plant: PlantData) -> SplitterSettings:
    return SplitterSettings(
        outer_opening=plant.splitter_outer,
        inner_opening=plant.splitter_inner,
    )


def _predict(
    feed: Feed,
    params: PartitionParameters,
    operating: OperatingConditions,
    plant: PlantData,
    n_products: int,
    dense_is_concentrate: bool,
) -> SimulationResult:
    return simulate(
        feed,
        params,
        _settings_from_plant(plant),
        operating,
        n_products,
        dense_is_concentrate,
    )


def comparisons(
    feed: Feed,
    result: SimulationResult,
    plant: PlantData,
    include_redundant_recoveries: bool = False,
) -> list[tuple[str, float | None, float | None]]:
    """Rows of (label, observed fraction, predicted fraction)."""
    rows: list[tuple[str, float | None, float | None]] = []
    for product in result.products:
        obs = plant.yields.get(product)
        if obs is not None:
            rows.append((f"Yield of {product}", obs, result.yield_of(product)))
        grades = plant.grades.get(product, {})
        for j, name in enumerate(result.mineral_names):
            if name in grades and grades[name] is not None:
                rows.append(
                    (f"Grade of {name} in {product}", grades[name], result.grade_of(product, j))
                )
        recoveries = plant.recoveries.get(product, {})
        for j, name in enumerate(result.mineral_names):
            grade_given = name in grades and grades[name] is not None
            if grade_given and not include_redundant_recoveries:
                # Grades already pull this mineral into the fit. Recoveries are
                # checked separately so the same assay is not counted twice.
                continue
            if name in recoveries and recoveries[name] is not None:
                rows.append(
                    (
                        f"Recovery of {name} to {product}",
                        recoveries[name],
                        result.recovery_of(product, j),
                    )
                )
    if plant.size_splits:
        for i, splits in enumerate(plant.size_splits):
            if i >= len(result.size_labels):
                break
            label = result.size_labels[i]
            for product in result.products:
                obs = splits.get(product)
                if obs is not None:
                    rows.append(
                        (
                            f"Split of {label} to {product}",
                            obs,
                            result.size_to_product(i, product),
                        )
                    )
    return rows


def _mae(rows: list[tuple[str, float | None, float | None]], prefix: str) -> float | None:
    errors = [
        abs(obs - pred) * 100.0
        for label, obs, pred in rows
        if label.startswith(prefix) and obs is not None and pred is not None
    ]
    if not errors:
        return None
    return sum(errors) / len(errors)


def _bounds(
    feed: Feed,
    params: PartitionParameters,
    n_products: int,
    fit_size: bool,
) -> list[tuple[str, float, float]]:
    densities = [m.density for m in feed.minerals] or [2.65, 4.5]
    rho_low = params.rho_fluid + 0.08
    rho_high = max(max(densities) + 1.2, rho_low + 0.8, 2.2)
    items = [
        ("rho50_outer_ref", rho_low, rho_high),
        ("imperfection_ref", 0.02, 0.45),
        ("bypass_outer_coarse", 0.0, 0.40),
        ("bypass_outer_slime", 0.0, 0.80),
    ]
    if n_products >= 3:
        # Stored as a gap so the inner cut stays above the outer cut.
        items.insert(1, ("cut_gap", 0.05, max(2.5, rho_high - rho_low)))
    if fit_size:
        items.append(("n_cut", 0.0, 0.80))
        items.append(("m_sharp", 0.0, 0.80))
    return items


def _pack(params: PartitionParameters, names: list[str]) -> list[float]:
    values = []
    for name in names:
        if name == "cut_gap":
            values.append(max(0.05, params.rho50_inner_ref - params.rho50_outer_ref))
        else:
            values.append(float(getattr(params, name)))
    return values


def _apply(params: PartitionParameters, names: list[str], values: list[float]) -> PartitionParameters:
    updated = replace(params)
    gap = None
    for name, value in zip(names, values):
        if name == "cut_gap":
            gap = value
        else:
            setattr(updated, name, value)
    if gap is not None:
        updated.rho50_inner_ref = updated.rho50_outer_ref + gap
    elif updated.rho50_inner_ref <= updated.rho50_outer_ref:
        updated.rho50_inner_ref = updated.rho50_outer_ref + 0.05
    return updated


def _to_unconstrained(x: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    t = (x - low) / (high - low)
    t = min(1.0 - 1e-6, max(1e-6, t))
    return math.log(t / (1.0 - t))


def _from_unconstrained(u: float, low: float, high: float) -> float:
    u = max(-12.0, min(12.0, u))
    t = 1.0 / (1.0 + math.exp(-u))
    return low + (high - low) * t


def _objective(
    values: list[float],
    names: list[str],
    base: PartitionParameters,
    feed: Feed,
    operating: OperatingConditions,
    plant: PlantData,
    n_products: int,
    dense_is_concentrate: bool,
    start_values: list[float],
) -> float:
    params = _apply(base, names, values)
    result = _predict(feed, params, operating, plant, n_products, dense_is_concentrate)
    sse = 0.0
    for label, obs, pred in comparisons(feed, result, plant):
        if obs is None or pred is None:
            continue
        if label.startswith("Yield"):
            weight = 2.0
        elif label.startswith("Grade"):
            weight = 1.6
        elif label.startswith("Recovery"):
            weight = 1.2
        else:
            weight = 0.8
        sse += (weight * (pred - obs)) ** 2
    # A light pull toward the starting point keeps an under-determined survey
    # from wandering off to an equally good but physically odd parameter set.
    for value, start, name in zip(values, start_values, names):
        scale = 1.0 if name.startswith("rho") or name == "cut_gap" else 0.2
        sse += 0.002 * ((value - start) / scale) ** 2
    return sse


def _nelder_mead(
    func,
    x0: list[float],
    step: float = 0.8,
    max_iter: int = 220,
    tol: float = 1e-10,
) -> tuple[list[float], float]:
    """Standard Nelder-Mead. ``func`` takes an unconstrained vector."""
    n = len(x0)
    simplex = [list(x0)]
    for i in range(n):
        point = list(x0)
        point[i] += step
        simplex.append(point)
    alpha, gamma, rho, sigma = 1.0, 2.0, 0.5, 0.5
    scores = [func(point) for point in simplex]

    def order() -> None:
        nonlocal simplex, scores
        ranked = sorted(range(n + 1), key=lambda i: scores[i])
        simplex = [simplex[i] for i in ranked]
        scores = [scores[i] for i in ranked]

    for _ in range(max_iter):
        order()
        if abs(scores[-1] - scores[0]) < tol:
            break
        centroid = [sum(simplex[i][j] for i in range(n)) / n for j in range(n)]
        reflected = [centroid[j] + alpha * (centroid[j] - simplex[-1][j]) for j in range(n)]
        score_r = func(reflected)
        if scores[0] <= score_r < scores[-2]:
            simplex[-1] = reflected
            scores[-1] = score_r
            continue
        if score_r < scores[0]:
            expanded = [centroid[j] + gamma * (reflected[j] - centroid[j]) for j in range(n)]
            score_e = func(expanded)
            if score_e < score_r:
                simplex[-1] = expanded
                scores[-1] = score_e
            else:
                simplex[-1] = reflected
                scores[-1] = score_r
            continue
        contracted = [centroid[j] + rho * (simplex[-1][j] - centroid[j]) for j in range(n)]
        score_c = func(contracted)
        if score_c < scores[-1]:
            simplex[-1] = contracted
            scores[-1] = score_c
            continue
        for i in range(1, n + 1):
            simplex[i] = [
                simplex[0][j] + sigma * (simplex[i][j] - simplex[0][j]) for j in range(n)
            ]
            scores[i] = func(simplex[i])
    order()
    return simplex[0], scores[0]


def _match_yields(
    params: PartitionParameters,
    feed: Feed,
    operating: OperatingConditions,
    plant: PlantData,
    n_products: int,
    dense_is_concentrate: bool,
) -> PartitionParameters:
    """Move the cut densities so the mass yields start close to the survey.

    The outer cut sets how much mass stays outside. The inner cut then splits
    the inner mass between concentrate and middlings. Doing this before the
    least-squares search gives the optimizer a sensible start.
    """
    updated = replace(params)

    def run(trial: PartitionParameters) -> SimulationResult:
        return _predict(feed, trial, operating, plant, n_products, dense_is_concentrate)

    def target_outer_stream() -> float | None:
        if dense_is_concentrate:
            if n_products < 3:
                tails = plant.yields.get("tails")
                conc = plant.yields.get("concentrate")
                if tails is not None:
                    return tails
                if conc is not None:
                    return 1.0 - conc
                return None
            tails = plant.yields.get("tails")
            if tails is not None:
                return tails
            conc = plant.yields.get("concentrate")
            midd = plant.yields.get("middlings")
            if conc is not None and midd is not None:
                return 1.0 - conc - midd
            return None
        conc = plant.yields.get("concentrate")
        if conc is not None:
            return conc
        return None

    def target_inner_stream() -> float | None:
        if dense_is_concentrate:
            return plant.yields.get("concentrate")
        if n_products < 3:
            tails = plant.yields.get("tails")
            conc = plant.yields.get("concentrate")
            if tails is not None:
                return tails
            if conc is not None:
                return 1.0 - conc
            return None
        tails = plant.yields.get("tails")
        if tails is not None:
            return tails
        conc = plant.yields.get("concentrate")
        midd = plant.yields.get("middlings")
        if conc is not None and midd is not None:
            return 1.0 - conc - midd
        return None

    def bisect_outer(target: float) -> float:
        low, high = 1.2, 8.0

        def outer_yield(rho: float) -> float:
            trial = replace(updated, rho50_outer_ref=rho)
            if trial.rho50_inner_ref < rho + 0.05:
                trial = replace(trial, rho50_inner_ref=rho + 0.05)
            result = run(trial)
            if dense_is_concentrate:
                return result.yield_of("tails")
            return result.yield_of("concentrate")

        y_low = outer_yield(low)
        y_high = outer_yield(high)
        # A higher cut sends more mass to the outer stream.
        if target <= y_low:
            return low
        if target >= y_high:
            return high
        for _ in range(40):
            mid = 0.5 * (low + high)
            y_mid = outer_yield(mid)
            if y_mid < target:
                low = mid
            else:
                high = mid
        return 0.5 * (low + high)

    def bisect_inner(target: float) -> float:
        low = updated.rho50_outer_ref + 0.05
        high = max(low + 0.2, 8.0)

        def inner_yield(rho: float) -> float:
            trial = replace(updated, rho50_inner_ref=rho)
            result = run(trial)
            if dense_is_concentrate:
                return result.yield_of("concentrate")
            return result.yield_of("tails")

        y_low = inner_yield(low)
        y_high = inner_yield(high)
        # A higher inner cut reduces the innermost (densest) product.
        if target >= y_low:
            return low
        if target <= y_high:
            return high
        for _ in range(40):
            mid = 0.5 * (low + high)
            y_mid = inner_yield(mid)
            if y_mid > target:
                low = mid
            else:
                high = mid
        return 0.5 * (low + high)

    outer_target = target_outer_stream()
    if outer_target is not None:
        updated.rho50_outer_ref = bisect_outer(outer_target)
        if updated.rho50_inner_ref < updated.rho50_outer_ref + 0.05:
            updated.rho50_inner_ref = updated.rho50_outer_ref + 0.05
    if n_products >= 3:
        inner_target = target_inner_stream()
        if inner_target is not None:
            updated.rho50_inner_ref = bisect_inner(inner_target)
    return updated


def recovery_consistency_notes(feed: Feed, plant: PlantData) -> list[str]:
    """Point out recoveries that disagree with the grades and yields entered."""
    notes: list[str] = []
    for product, recoveries in plant.recoveries.items():
        yield_obs = plant.yields.get(product)
        grades = plant.grades.get(product, {})
        if yield_obs is None:
            continue
        for j, mineral in enumerate(feed.minerals):
            stated = recoveries.get(mineral.name)
            grade = grades.get(mineral.name)
            feed_grade = feed.overall_grade(j)
            if stated is None or grade is None or feed_grade <= 1e-9:
                continue
            implied = grade * yield_obs / feed_grade
            if abs(implied - stated) > 0.03:
                notes.append(
                    f"{mineral.name} in the {product}: the grade and yield imply "
                    f"{implied * 100:.1f}% recovery, while the entered recovery is "
                    f"{stated * 100:.1f}%."
                )
    return notes


def fit_parameters(
    feed: Feed,
    params: PartitionParameters,
    operating: OperatingConditions,
    plant: PlantData,
    n_products: int,
    dense_is_concentrate: bool,
) -> FitReport:
    """Least-squares fit. ``params`` supplies the values that stay fixed."""
    if plant.observation_count() == 0:
        raise ValueError("There are no measured numbers to fit.")

    fit_size = plant.has_size_splits()
    seeded = _match_yields(params, feed, operating, plant, n_products, dense_is_concentrate)
    bound_list = _bounds(feed, seeded, n_products, fit_size)
    names = [name for name, _low, _high in bound_list]
    lows = [low for _name, low, _high in bound_list]
    highs = [high for _name, _low, high in bound_list]
    start_values = _pack(seeded, names)
    start_values = [min(high, max(low, value)) for value, low, high in zip(start_values, lows, highs)]

    def unconstrained_objective(u_values: list[float]) -> float:
        real = [_from_unconstrained(u, low, high) for u, low, high in zip(u_values, lows, highs)]
        return _objective(
            real,
            names,
            seeded,
            feed,
            operating,
            plant,
            n_products,
            dense_is_concentrate,
            start_values,
        )

    start_u = [_to_unconstrained(v, low, high) for v, low, high in zip(start_values, lows, highs)]
    starts = [start_u]
    # A few deterministic restarts. The first two coordinates are the cuts.
    for index in range(min(2, len(start_u))):
        for delta in (-1.1, 1.1):
            shifted = list(start_u)
            shifted[index] += delta
            starts.append(shifted)
    starts.append([0.0] * len(start_u))

    best_u = start_u
    best_score = unconstrained_objective(start_u)
    for start in starts:
        found, score = _nelder_mead(unconstrained_objective, start)
        if score < best_score:
            best_u, best_score = found, score

    best_values = [_from_unconstrained(u, low, high) for u, low, high in zip(best_u, lows, highs)]
    fitted = _apply(seeded, names, best_values)
    # Keep attributes that were not part of this fit.
    fitted.curve = params.curve
    fitted.d_ref = params.d_ref
    fitted.bypass_inner = params.bypass_inner
    fitted.d_slime = params.d_slime
    fitted.slime_exponent = params.slime_exponent
    fitted.rho_fluid = params.rho_fluid
    if not fit_size:
        fitted.n_cut = params.n_cut
        fitted.m_sharp = params.m_sharp

    result = _predict(feed, fitted, operating, plant, n_products, dense_is_concentrate)
    rows = comparisons(feed, result, plant)
    n_points = sum(1 for _label, obs, pred in rows if obs is not None and pred is not None)
    sparse = n_points < 4
    yield_mae = _mae(rows, "Yield")
    grade_mae = _mae(rows, "Grade")
    recovery_mae = _mae(rows, "Recovery")
    split_mae = _mae(rows, "Split")

    parts = [f"Fitted {n_points} measured numbers."]
    if yield_mae is not None:
        parts.append(f"Average yield error {yield_mae:.2f} percentage points.")
    if grade_mae is not None:
        parts.append(f"Average grade error {grade_mae:.2f} percentage points.")
    if recovery_mae is not None:
        parts.append(f"Average recovery error {recovery_mae:.2f} percentage points.")
    if split_mae is not None:
        parts.append(f"Average size-split error {split_mae:.2f} percentage points.")
    if sparse:
        parts.append(
            "The survey is sparse, so treat the fitted cuts as only partly determined."
        )
    if not fit_size:
        parts.append(
            "Size exponents were held at their starting values. A size-by-size mass split would let them be fitted."
        )
    parts.extend(recovery_consistency_notes(feed, plant))

    return FitReport(
        parameters=fitted,
        objective=best_score,
        yield_mae_pp=yield_mae,
        grade_mae_pp=grade_mae,
        recovery_mae_pp=recovery_mae,
        size_split_mae_pp=split_mae,
        n_points=n_points,
        comparisons=rows,
        message=" ".join(parts),
        sparse=sparse,
    )
