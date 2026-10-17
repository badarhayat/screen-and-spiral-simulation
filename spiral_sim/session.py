"""Save and load a complete simulator session.

A session is the feed, the operating numbers, the partition parameters, the
splitter openings, and any plant survey used for calibration. The file is
JSON so it can be edited by hand and checked into a plant record.
Fractions in the file run from 0 to 1. The conversational interface speaks
in percent and converts at the boundary.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from spiral_sim.calibrate import PlantData
from spiral_sim.feed import Feed, Mineral, SizeClass
from spiral_sim.model import OperatingConditions, PartitionParameters, SplitterSettings
from spiral_sim.simulate import SimulationResult, simulate


SESSION_VERSION = 1

UNITS = {
    "size": "mm",
    "density": "g/cm3",
    "mass_fraction": "0 to 1",
    "grade": "0 to 1",
    "yield": "0 to 1",
    "recovery": "0 to 1",
    "splitter_opening": "0 to 100; 50 applies the stored reference cut densities",
}


@dataclass
class Session:
    """Everything needed to repeat a simulation."""

    feed: Feed
    params: PartitionParameters
    settings: SplitterSettings = field(default_factory=SplitterSettings)
    operating: OperatingConditions = field(default_factory=OperatingConditions)
    n_products: int = 3
    dense_is_concentrate: bool = True
    parameter_source: str = "density-defaults"
    source_note: str = ""
    plant: PlantData | None = None
    fit_message: str = ""

    def run(self) -> SimulationResult:
        return simulate(
            self.feed,
            self.params,
            self.settings,
            self.operating,
            self.n_products,
            self.dense_is_concentrate,
        )


def _filter_fields(cls, data: dict):
    known = {item.name for item in fields(cls)}
    return cls(**{key: value for key, value in data.items() if key in known})


def session_to_dict(session: Session) -> dict:
    """Plain data suitable for JSON. Dataclasses are expanded."""
    return {
        "version": SESSION_VERSION,
        "description": (
            "Spiral concentrator session. The separation model is the "
            "generalized bivariate size-density partition function "
            "(R.P. King, Modeling and Simulation of Mineral Processing "
            "Systems, Chapter 7), applied as an outer cut and then an inner cut."
        ),
        "units": UNITS,
        "feed": {
            "size_classes": [asdict(size_class) for size_class in session.feed.size_classes],
            "minerals": [asdict(mineral) for mineral in session.feed.minerals],
            "composition": session.feed.composition,
        },
        "operating": asdict(session.operating),
        "parameters": asdict(session.params),
        "splitters": asdict(session.settings),
        "n_products": session.n_products,
        "dense_is_concentrate": session.dense_is_concentrate,
        "parameter_source": session.parameter_source,
        "source_note": session.source_note,
        "fit_message": session.fit_message,
        "plant": None if session.plant is None else asdict(session.plant),
    }


def session_from_dict(data: dict) -> Session:
    """Rebuild a session. Unknown keys are ignored so older files still open."""
    version = data.get("version", 1)
    if version != SESSION_VERSION:
        # Version 1 is the only reader. A newer file may still have these keys.
        pass
    feed_data = data["feed"]
    feed = Feed(
        size_classes=[_filter_fields(SizeClass, item) for item in feed_data["size_classes"]],
        minerals=[_filter_fields(Mineral, item) for item in feed_data["minerals"]],
        composition=feed_data["composition"],
    )
    plant = None
    if data.get("plant"):
        plant = _filter_fields(PlantData, data["plant"])
    session = Session(
        feed=feed,
        params=_filter_fields(PartitionParameters, data["parameters"]),
        settings=_filter_fields(SplitterSettings, data.get("splitters", {})),
        operating=_filter_fields(OperatingConditions, data.get("operating", {})),
        n_products=int(data.get("n_products", 3)),
        dense_is_concentrate=bool(data.get("dense_is_concentrate", True)),
        parameter_source=str(data.get("parameter_source", "loaded")),
        source_note=str(data.get("source_note", "")),
        plant=plant,
        fit_message=str(data.get("fit_message", "")),
    )
    problems = session.feed.validate()
    if problems:
        raise ValueError(" ".join(problems))
    if session.n_products not in (2, 3):
        raise ValueError("n_products must be 2 or 3.")
    if session.params.curve not in ("erf", "logistic"):
        session.params.curve = "erf"
    return session


def save_session(path: str | Path, session: Session) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = session_to_dict(session)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_session(path: str | Path) -> Session:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not read {source.name} as a session file ({exc}).") from exc
    if not isinstance(data, dict) or "feed" not in data or "parameters" not in data:
        raise ValueError(f"{source.name} is not a spiral session file.")
    return session_from_dict(data)
