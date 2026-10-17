"""Feed description: size classes, minerals, and composition.

Minerals are whatever the user names. The only property the separator uses
is density, so the same feed structure works for heavy-mineral sands, iron
ore, chromite, coal, or any other suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from spiral_sim.model import representative_size


@dataclass
class Mineral:
    name: str
    density: float  # g/cm³, numerically equal to specific gravity


@dataclass
class SizeClass:
    d_low: float  # mm
    d_high: float  # mm
    mass_fraction: float  # of feed solids, 0–1

    @property
    def size_mm(self) -> float:
        return representative_size(self.d_low, self.d_high)

    def label(self) -> str:
        return f"{self.d_low:.3f}-{self.d_high:.3f} mm"


@dataclass
class Feed:
    """Dry-solids feed.

    ``composition[i][j]`` is the mass fraction of mineral j inside size class i.
    Each row sums to 1. Size-class mass fractions sum to 1.
    """

    size_classes: list[SizeClass] = field(default_factory=list)
    minerals: list[Mineral] = field(default_factory=list)
    composition: list[list[float]] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Return human-readable problems. An empty list means the feed is usable."""
        problems: list[str] = []
        if not self.size_classes:
            problems.append("The feed needs at least one size class.")
        if not self.minerals:
            problems.append("The feed needs at least one mineral.")
        total = sum(sc.mass_fraction for sc in self.size_classes)
        if self.size_classes and abs(total - 1.0) > 1e-6:
            problems.append(f"Size-class mass fractions sum to {total:.6f}, not 1.")
        if len(self.composition) != len(self.size_classes):
            problems.append("Each size class needs a mineral composition.")
        for i, row in enumerate(self.composition):
            if len(row) != len(self.minerals):
                problems.append(f"Size class {i + 1} has a composition of the wrong length.")
                continue
            if abs(sum(row) - 1.0) > 1e-5:
                problems.append(f"Size class {i + 1} mineral grades sum to {sum(row):.4f}, not 1.")
            if any(v < -1e-12 for v in row):
                problems.append(f"Size class {i + 1} has a negative mineral grade.")
        names = [m.name.strip().lower() for m in self.minerals]
        if len(names) != len(set(names)):
            problems.append("Mineral names must be unique.")
        for mineral in self.minerals:
            if mineral.density <= 0:
                problems.append(f"{mineral.name} needs a density greater than zero.")
        return problems

    def mineral_index(self, name: str) -> int | None:
        key = name.strip().lower()
        for i, mineral in enumerate(self.minerals):
            if mineral.name.strip().lower() == key:
                return i
        return None

    def overall_grade(self, mineral_index: int) -> float:
        return sum(
            sc.mass_fraction * self.composition[i][mineral_index]
            for i, sc in enumerate(self.size_classes)
        )

    def component_masses(self) -> list[list[float]]:
        """Mass of each (size, mineral) per unit of feed solids."""
        masses: list[list[float]] = []
        for i, size_class in enumerate(self.size_classes):
            row = [size_class.mass_fraction * g for g in self.composition[i]]
            masses.append(row)
        return masses

    def density_span(self) -> float:
        if not self.minerals:
            return 0.0
        densities = [m.density for m in self.minerals]
        return max(densities) - min(densities)


def normalize(values: list[float]) -> list[float]:
    """Scale a list of non-negative numbers so it sums to 1."""
    cleaned = [max(0.0, float(v)) for v in values]
    total = sum(cleaned)
    if total <= 0.0:
        raise ValueError("Cannot normalize values that sum to zero.")
    return [v / total for v in cleaned]
