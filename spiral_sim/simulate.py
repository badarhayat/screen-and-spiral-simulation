"""Mass balance over King's partition function.

Each (size class, mineral) particle group is an independent feed component.
Its recovery to concentrate, middlings, and tails is the partition value at
that size and density. Product grades, yields, and size distributions are
sums of those components. Because every component is conserved, the mineral
and size balances close by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from spiral_sim.feed import Feed
from spiral_sim.model import (
    OperatingConditions,
    PartitionParameters,
    SizeProfile,
    SplitterSettings,
    product_recoveries,
    profile_at_size,
    solids_factors,
)


PRODUCT_LABELS = {
    "concentrate": "Concentrate",
    "middlings": "Middlings",
    "tails": "Tails",
}


def active_products(n_products: int) -> tuple[str, ...]:
    if n_products <= 2:
        return ("concentrate", "tails")
    return ("concentrate", "middlings", "tails")


@dataclass
class SimulationResult:
    """Dry-solids masses per 1.0 mass unit of feed."""

    mineral_names: list[str]
    size_labels: list[str]
    feed_mineral: list[float]
    feed_size: list[float]
    mineral_mass: dict[str, list[float]]
    size_mass: dict[str, list[float]]
    total: dict[str, float]
    profiles: list[SizeProfile]
    warnings: list[str] = field(default_factory=list)
    products: tuple[str, ...] = ("concentrate", "middlings", "tails")

    def yield_of(self, product: str) -> float:
        return self.total.get(product, 0.0)

    def grade_of(self, product: str, mineral_index: int) -> float | None:
        total = self.total.get(product, 0.0)
        if total <= 1e-15:
            return None
        return self.mineral_mass[product][mineral_index] / total

    def recovery_of(self, product: str, mineral_index: int) -> float | None:
        feed = self.feed_mineral[mineral_index]
        if feed <= 1e-15:
            return None
        return self.mineral_mass[product][mineral_index] / feed

    def size_distribution(self, product: str, size_index: int) -> float | None:
        """Mass fraction of this size inside the product."""
        total = self.total.get(product, 0.0)
        if total <= 1e-15:
            return None
        return self.size_mass[product][size_index] / total

    def size_to_product(self, size_index: int, product: str) -> float | None:
        """Fraction of this feed size class that reports to the product."""
        feed = self.feed_size[size_index]
        if feed <= 1e-15:
            return None
        return self.size_mass[product][size_index] / feed

    def closure_error(self) -> float:
        """Largest absolute mass imbalance, feed versus sum of products."""
        worst = 0.0
        for j in range(len(self.mineral_names)):
            produced = sum(self.mineral_mass[p][j] for p in self.products)
            worst = max(worst, abs(produced - self.feed_mineral[j]))
        produced_total = sum(self.total[p] for p in self.products)
        worst = max(worst, abs(produced_total - 1.0))
        return worst


def simulate(
    feed: Feed,
    params: PartitionParameters,
    settings: SplitterSettings,
    operating: OperatingConditions,
    n_products: int = 3,
    dense_is_concentrate: bool = True,
) -> SimulationResult:
    """Run the partition model and accumulate the solids balance."""
    problems = feed.validate()
    if problems:
        raise ValueError(" ".join(problems))

    products = active_products(n_products)
    n_minerals = len(feed.minerals)
    n_sizes = len(feed.size_classes)
    mineral_mass = {p: [0.0] * n_minerals for p in products}
    size_mass = {p: [0.0] * n_sizes for p in products}
    feed_mineral = [0.0] * n_minerals
    feed_size = [sc.mass_fraction for sc in feed.size_classes]
    profiles: list[SizeProfile] = []
    warnings: list[str] = []
    any_clamped = False

    _ep_factor, _extra, solids_assumed = solids_factors(operating.pct_solids, params)
    if solids_assumed:
        warnings.append(
            f"Percent solids was not given, so {params.solids_ref:.0f}% solids "
            "was used for the small pulp-density correction."
        )
    elif operating.pct_solids is not None and operating.pct_solids > 55.0:
        warnings.append(
            f"Percent solids is {operating.pct_solids:.0f}%. Spirals are usually "
            "fed much more dilute than this, and the pulp-density correction is "
            "only a mild adjustment around 30% solids."
        )

    for i, size_class in enumerate(feed.size_classes):
        prof = profile_at_size(
            size_class.size_mm, params, settings, operating.pct_solids, n_products
        )
        profiles.append(prof)
        any_clamped = any_clamped or prof.outer_clamped or prof.inner_clamped
        for j, mineral in enumerate(feed.minerals):
            mass = size_class.mass_fraction * feed.composition[i][j]
            feed_mineral[j] += mass
            # Generalized partition, after King: this size and density class
            # reports on its own. Nothing here is specific to one ore.
            recoveries = product_recoveries(
                size_class.size_mm,
                mineral.density,
                params,
                settings,
                operating.pct_solids,
                n_products,
                dense_is_concentrate,
            )
            for product in products:
                amount = mass * recoveries[product]
                mineral_mass[product][j] += amount
                size_mass[product][i] += amount

    if any_clamped:
        warnings.append(
            "A splitter setting pushed a cut density outside the range the model "
            "will use, so the cut was held at the nearest allowed value."
        )

    totals = {p: sum(mineral_mass[p]) for p in products}
    return SimulationResult(
        mineral_names=[m.name for m in feed.minerals],
        size_labels=[sc.label() for sc in feed.size_classes],
        feed_mineral=feed_mineral,
        feed_size=feed_size,
        mineral_mass=mineral_mass,
        size_mass=size_mass,
        total=totals,
        profiles=profiles,
        warnings=warnings,
        products=products,
    )
