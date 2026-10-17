"""Built-in feeds.

The heavy-mineral example is a typical spiral feed: quartz with rutile and
zircon, heavies concentrated in the mid sizes. It is not tied to one plant.
The calibration demo manufactures a survey from a known parameter set, then
fits from the density-based starting point, so the fitting path can be seen
without real plant data.
"""

from __future__ import annotations

from spiral_sim.calibrate import PlantData, fit_parameters
from spiral_sim.defaults import defaults_from_minerals, heavy_mineral_preset
from spiral_sim.feed import Feed, Mineral, SizeClass
from spiral_sim.model import OperatingConditions, PartitionParameters, SplitterSettings
from spiral_sim.session import Session
from spiral_sim.simulate import simulate


def heavy_mineral_feed() -> Feed:
    """Quartz, rutile, and zircon. Mass fractions are fractions of solids."""
    # (lower mm, upper mm, mass fraction, quartz, rutile, zircon within the class)
    rows = [
        (0.600, 1.000, 0.08, (0.92, 0.05, 0.03)),
        (0.300, 0.600, 0.17, (0.88, 0.07, 0.05)),
        (0.150, 0.300, 0.30, (0.80, 0.12, 0.08)),
        (0.106, 0.150, 0.25, (0.78, 0.13, 0.09)),
        (0.075, 0.106, 0.12, (0.82, 0.11, 0.07)),
        (0.045, 0.075, 0.05, (0.90, 0.06, 0.04)),
        (0.000, 0.045, 0.03, (0.95, 0.03, 0.02)),
    ]
    return Feed(
        size_classes=[
            SizeClass(d_low=low, d_high=high, mass_fraction=mass)
            for low, high, mass, _grades in rows
        ],
        minerals=[
            Mineral("Quartz", 2.65),
            Mineral("Rutile", 4.25),
            Mineral("Zircon", 4.65),
        ],
        composition=[list(grades) for _low, _high, _mass, grades in rows],
    )


def heavy_mineral_sands() -> Session:
    """Uncalibrated heavy-mineral spiral, ready to simulate or adjust."""
    feed = heavy_mineral_feed()
    params, note = defaults_from_minerals(feed.minerals)
    return Session(
        feed=feed,
        params=params,
        settings=SplitterSettings(),
        operating=OperatingConditions(feed_rate_tph=20.0, pct_solids=32.0),
        n_products=3,
        dense_is_concentrate=True,
        parameter_source="density-defaults",
        source_note=note,
    )


def _round_yields(raw: dict[str, float]) -> dict[str, float]:
    """Round to 0.1 percentage points and rescale so the products still sum to 1."""
    keys = list(raw)
    rounded = {key: round(max(0.0, raw[key]), 3) for key in keys}
    total = sum(rounded.values())
    if total <= 0.0:
        share = 1.0 / len(keys)
        return {key: share for key in keys}
    return {key: value / total for key, value in rounded.items()}


def survey_from_model(
    feed: Feed,
    params: PartitionParameters,
    operating: OperatingConditions,
    settings: SplitterSettings,
    n_products: int,
    dense_is_concentrate: bool,
    include_size_splits: bool = False,
) -> PlantData:
    """Treat a model run as a plant survey, rounded to 0.1 percentage points."""
    result = simulate(feed, params, settings, operating, n_products, dense_is_concentrate)
    grades: dict[str, dict[str, float | None]] = {}
    for product in result.products:
        grades[product] = {}
        for index, name in enumerate(result.mineral_names):
            value = result.grade_of(product, index)
            grades[product][name] = None if value is None else round(value, 3)
    size_splits = None
    if include_size_splits:
        size_splits = []
        for index in range(len(result.size_labels)):
            splits = {}
            running = 0.0
            products = list(result.products)
            for product in products[:-1]:
                value = result.size_to_product(index, product)
                rounded = 0.0 if value is None else round(max(0.0, value), 3)
                splits[product] = rounded
                running += rounded
            last = products[-1]
            splits[last] = max(0.0, 1.0 - running)
            total = sum(splits.values())
            if total > 0.0:
                splits = {key: value / total for key, value in splits.items()}
            size_splits.append(splits)
    return PlantData(
        yields=_round_yields({product: result.yield_of(product) for product in result.products}),
        grades=grades,
        size_splits=size_splits,
        splitter_outer=settings.outer_opening,
        splitter_inner=settings.inner_opening,
    )


def calibration_demo() -> tuple[Session, PartitionParameters, PartitionParameters, object]:
    """Fit a synthetic heavy-mineral survey.

    Returns the fitted session, the parameters that generated the survey,
    the density-based start, and the fit report.

    The "truth" is a plausible spiral, deliberately different from the
    density-based start, so the fit has to move the cuts, the imperfection,
    and the bypass. The survey is that truth, rounded as a lab would.
    """
    base = heavy_mineral_sands()
    truth = heavy_mineral_preset()
    truth.rho50_outer_ref = 3.05
    truth.rho50_inner_ref = 4.10
    truth.imperfection_ref = 0.14
    truth.bypass_outer_coarse = 0.04
    truth.bypass_outer_slime = 0.32
    settings = SplitterSettings(outer_opening=50.0, inner_opening=45.0)
    plant = survey_from_model(
        base.feed,
        truth,
        base.operating,
        settings,
        n_products=3,
        dense_is_concentrate=True,
    )
    start, _note = defaults_from_minerals(base.feed.minerals)
    report = fit_parameters(
        base.feed,
        start,
        base.operating,
        plant,
        n_products=3,
        dense_is_concentrate=True,
    )
    session = Session(
        feed=base.feed,
        params=report.parameters,
        settings=settings,
        operating=base.operating,
        n_products=3,
        dense_is_concentrate=True,
        parameter_source="fitted",
        source_note=(
            "Demonstration only. The survey was generated from a known spiral "
            "and rounded to 0.1 percentage points, then fitted from the "
            "density-based starting cuts. It is not a real plant survey."
        ),
        plant=plant,
        fit_message=report.message,
    )
    return session, truth, start, report
