"""Local web interface for the spiral simulator.

Run ``python3 -m spiral_sim --web`` and open http://127.0.0.1:8080 .
The page asks for the feed one step at a time, then calls the same partition
model, calibration, and mass balance as the command-line program.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from spiral_sim.calibrate import PlantData, fit_parameters
from spiral_sim.defaults import contrast_warning, defaults_from_minerals, heavy_mineral_preset
from spiral_sim.examples import heavy_mineral_sands
from spiral_sim.feed import Feed, Mineral, SizeClass, normalize
from spiral_sim.model import OperatingConditions, SplitterSettings
from spiral_sim.report import CALIBRATION_NOTE, SOURCE_SUMMARY
from spiral_sim.session import Session, session_from_dict, session_to_dict
from spiral_sim.simulate import PRODUCT_LABELS, SimulationResult, active_products
from spiral_sim.targets import match_mass_pulls


def static_page_path() -> Path:
    """Locate the browser page, including inside a PyInstaller one-file executable.

    A frozen executable unpacks data files under ``sys._MEIPASS``. The page is
    stored there as ``spiral_sim/static/index.html``. A normal Python run reads
    the file next to this module.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        return base / "spiral_sim" / "static" / "index.html"
    return Path(__file__).resolve().parent / "static" / "index.html"


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _float(value, label: str) -> float:
    if _blank(value):
        raise ValueError(f"Enter a number for {label}.")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} needs to be a number.") from exc


def _optional_float(value, label: str) -> float | None:
    if _blank(value):
        return None
    return _float(value, label)


def _percent(value, label: str) -> float:
    number = _float(value, label)
    if number < 0 or number > 100:
        raise ValueError(f"{label} needs to be from 0 to 100 percent.")
    return number


def _optional_percent(value, label: str) -> float | None:
    if _blank(value):
        return None
    return _percent(value, label)


def _fractions_from_percents(percents: list[float | None], label: str) -> tuple[list[float], list[str]]:
    """Turn a row of percents into fractions that sum to 1.

    One blank, and only in the last position, becomes the remainder.
    A complete row that does not add up to 100 is scaled.
    """
    notes: list[str] = []
    if not percents:
        raise ValueError(f"Enter the {label}.")
    blanks = [index for index, value in enumerate(percents) if value is None]
    if len(blanks) > 1:
        raise ValueError(f"For the {label}, leave only the last value blank.")
    if len(blanks) == 1 and blanks[0] != len(percents) - 1:
        raise ValueError(f"For the {label}, only the last value can be left blank.")
    numbers = list(percents)
    if numbers[-1] is None:
        used = sum(value for value in numbers[:-1] if value is not None)
        if used > 100.05:
            raise ValueError(f"The {label} already adds up to more than 100%.")
        numbers[-1] = max(0.0, 100.0 - used)
    total = sum(value or 0.0 for value in numbers)
    if total <= 0:
        raise ValueError(f"The {label} adds up to zero.")
    if abs(total - 100.0) > 0.05:
        notes.append(f"The {label} added up to {total:.2f}% and was scaled to 100%.")
        numbers = [100.0 * (value or 0.0) / total for value in numbers]
    try:
        fractions = normalize([float(value or 0.0) for value in numbers])
    except ValueError as exc:
        raise ValueError(f"The {label} could not be used.") from exc
    return fractions, notes


def build_feed(data: dict) -> tuple[Feed, list[str]]:
    """Build a feed from the wizard payload. Percents are converted here."""
    notes: list[str] = []
    size_rows = data.get("sizes") or []
    if not size_rows:
        raise ValueError("Add at least one size class.")
    parsed: list[tuple[float, float, float]] = []
    for index, row in enumerate(size_rows, start=1):
        lower = _float(row.get("d_low"), f"size class {index} lower size")
        upper = _float(row.get("d_high"), f"size class {index} upper size")
        mass = _percent(row.get("mass_percent"), f"size class {index} mass percent")
        if lower < 0 or upper < 0:
            raise ValueError(f"Size class {index} cannot have a negative size.")
        if upper <= lower:
            raise ValueError(f"Size class {index}: the upper size needs to be larger than the lower size.")
        parsed.append((lower, upper, mass))
    total = sum(mass for _low, _high, mass in parsed)
    if total <= 0:
        raise ValueError("The size-class mass percents add up to zero.")
    if abs(total - 100.0) > 0.5:
        notes.append(
            f"Size-class mass percents added up to {total:.2f}% and were scaled to 100%."
        )
        parsed = [(lower, upper, 100.0 * mass / total) for lower, upper, mass in parsed]
    mass_fractions = [mass / 100.0 for _low, _high, mass in parsed]
    mass_fractions[-1] = max(0.0, 1.0 - sum(mass_fractions[:-1]))

    mineral_rows = data.get("minerals") or []
    if not mineral_rows:
        raise ValueError("Add at least one mineral.")
    minerals: list[Mineral] = []
    seen: set[str] = set()
    for index, row in enumerate(mineral_rows, start=1):
        name = str(row.get("name") or "").strip()
        if not name:
            raise ValueError(f"Mineral {index} needs a name.")
        if name.lower() in seen:
            raise ValueError(f"{name} is listed twice. Use a different name.")
        density = _float(row.get("density"), f"density of {name}")
        if density <= 0:
            raise ValueError(f"{name} needs a density greater than zero.")
        if density > 30:
            raise ValueError(f"{name} has a density above 30 g/cm3.")
        if density < 1:
            notes.append(f"{name} at {density:g} g/cm3 is lighter than water.")
        seen.add(name.lower())
        minerals.append(Mineral(name, density))

    if len(minerals) == 1:
        composition = [[1.0] for _row in parsed]
    elif data.get("composition_mode") == "by_size":
        rows = data.get("grades_by_size") or []
        if len(rows) != len(parsed):
            raise ValueError("Enter a mineral analysis for every size class.")
        composition = []
        for index, row in enumerate(rows, start=1):
            percents = [
                _optional_percent(row.get(mineral.name), f"{mineral.name} in size class {index}")
                for mineral in minerals
            ]
            fractions, grade_notes = _fractions_from_percents(
                percents, f"grades in size class {index}"
            )
            notes.extend(grade_notes)
            composition.append(fractions)
    else:
        overall = data.get("overall_grades") or {}
        percents = [
            _optional_percent(overall.get(mineral.name), f"overall grade of {mineral.name}")
            for mineral in minerals
        ]
        fractions, grade_notes = _fractions_from_percents(percents, "overall mineral grades")
        notes.extend(grade_notes)
        composition = [list(fractions) for _row in parsed]

    feed = Feed(
        size_classes=[
            SizeClass(d_low=lower, d_high=upper, mass_fraction=fraction)
            for (lower, upper, _mass), fraction in zip(parsed, mass_fractions)
        ],
        minerals=minerals,
        composition=composition,
    )
    problems = feed.validate()
    if problems:
        raise ValueError(" ".join(problems))
    return feed, notes


def build_operating(data: dict) -> OperatingConditions:
    feed_rate = _optional_float(data.get("feed_rate_tph"), "feed rate")
    if feed_rate is not None and feed_rate <= 0:
        raise ValueError("Feed rate needs to be greater than zero, or left blank.")
    solids = _optional_float(data.get("pct_solids"), "percent solids")
    if solids is not None and (solids < 1 or solids > 85):
        raise ValueError("Percent solids needs to be from 1 to 85, or left blank.")
    return OperatingConditions(feed_rate_tph=feed_rate, pct_solids=solids)


def _complete_yields(raw: dict, products: tuple[str, ...]) -> tuple[dict[str, float | None], list[str]]:
    notes: list[str] = []
    values = {
        product: _optional_percent(raw.get(product), f"{PRODUCT_LABELS[product]} yield")
        for product in products
    }
    missing = [product for product in products if values[product] is None]
    if len(missing) == 1 and len(products) - len(missing) >= 1:
        used = sum(value for value in values.values() if value is not None)
        if used > 100.05:
            raise ValueError("Those yields already add up to more than 100%.")
        values[missing[0]] = max(0.0, 100.0 - used)
        notes.append(
            f"The {PRODUCT_LABELS[missing[0]]} yield was set to {values[missing[0]]:.2f}% "
            "so the products sum to 100%."
        )
    if missing == []:
        total = sum(value or 0.0 for value in values.values())
        if total > 0 and abs(total - 100.0) > 1.0:
            notes.append(f"Yields added up to {total:.2f}% and were scaled to 100%.")
            values = {product: 100.0 * (value or 0.0) / total for product, value in values.items()}
    return {
        product: None if values[product] is None else values[product] / 100.0
        for product in products
    }, notes


def build_plant(data: dict, n_products: int, mineral_names: list[str]) -> tuple[PlantData, list[str]]:
    notes: list[str] = []
    products = active_products(n_products)
    yields, yield_notes = _complete_yields(data.get("yields") or {}, products)
    notes.extend(yield_notes)
    grades: dict[str, dict[str, float | None]] = {}
    recoveries: dict[str, dict[str, float | None]] = {}
    for product in products:
        raw_grades = (data.get("grades") or {}).get(product) or {}
        raw_recoveries = (data.get("recoveries") or {}).get(product) or {}
        grade_values = {
            name: _optional_percent(raw_grades.get(name), f"grade of {name} in the {product}")
            for name in mineral_names
        }
        filled = [value for value in grade_values.values() if value is not None]
        if len(filled) == len(mineral_names) and mineral_names:
            total = sum(filled)
            if total > 0 and abs(total - 100.0) > 1.5:
                notes.append(
                    f"{PRODUCT_LABELS[product]} grades added up to {total:.2f}% and were scaled to 100%."
                )
                grade_values = {
                    name: None if value is None else 100.0 * value / total
                    for name, value in grade_values.items()
                }
        grades[product] = {
            name: None if value is None else value / 100.0 for name, value in grade_values.items()
        }
        recoveries[product] = {
            name: (
                None
                if _optional_percent(
                    raw_recoveries.get(name), f"recovery of {name} to the {product}"
                )
                is None
                else _optional_percent(
                    raw_recoveries.get(name), f"recovery of {name} to the {product}"
                )
                / 100.0
            )
            for name in mineral_names
        }
    outer = _optional_float(data.get("splitter_outer"), "survey outer opening")
    inner = _optional_float(data.get("splitter_inner"), "survey inner opening")
    plant = PlantData(
        yields=yields,
        grades=grades,
        recoveries=recoveries,
        splitter_outer=50.0 if outer is None else outer,
        splitter_inner=50.0 if inner is None else inner,
    )
    return plant, notes


def _pct(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value * 100.0, digits)


def pack(session: Session, result: SimulationResult, notes: list[str] | None = None, fit_report=None) -> dict:
    """JSON the page can render without parsing the text report."""
    products = list(result.products)
    warnings = list(notes or [])
    warnings.extend(result.warnings)
    contrast = contrast_warning(session.feed.minerals)
    if contrast:
        warnings.append(contrast)
    comparisons = []
    if fit_report is not None:
        for label, observed, predicted in fit_report.comparisons:
            gap = None
            if observed is not None and predicted is not None:
                gap = round((predicted - observed) * 100.0, 2)
            comparisons.append(
                {
                    "label": label,
                    "observed": _pct(observed),
                    "predicted": _pct(predicted),
                    "gap": gap,
                }
            )
    return {
        "ok": True,
        "products": [{"key": product, "label": PRODUCT_LABELS[product]} for product in products],
        "yields": {product: _pct(result.yield_of(product)) for product in products},
        "rates_tph": (
            None
            if session.operating.feed_rate_tph is None
            else {
                "feed": round(session.operating.feed_rate_tph, 2),
                **{
                    product: round(session.operating.feed_rate_tph * result.yield_of(product), 2)
                    for product in products
                },
            }
        ),
        "minerals": [
            {
                "name": mineral.name,
                "density": round(mineral.density, 3),
                "feed_grade": _pct(session.feed.overall_grade(index)),
                "grades": {
                    product: _pct(result.grade_of(product, index)) for product in products
                },
                "recoveries": {
                    product: _pct(result.recovery_of(product, index)) for product in products
                },
                "balance": {
                    "feed": round(result.feed_mineral[index] * 100.0, 2),
                    **{
                        product: round(result.mineral_mass[product][index] * 100.0, 2)
                        for product in products
                    },
                },
            }
            for index, mineral in enumerate(session.feed.minerals)
        ],
        "sizes": [
            {
                "label": label,
                "feed": _pct(result.feed_size[index]),
                "distribution": {
                    product: _pct(result.size_distribution(product, index)) for product in products
                },
                "destination": {
                    product: _pct(result.size_to_product(index, product)) for product in products
                },
            }
            for index, label in enumerate(result.size_labels)
        ],
        "balance_total": {
            "feed": 100.0,
            **{product: round(result.total[product] * 100.0, 2) for product in products},
        },
        "closure": result.closure_error(),
        "parameters": {
            "curve": session.params.curve,
            "curve_label": session.params.curve_label(),
            "d_ref": session.params.d_ref,
            "rho50_outer_ref": round(session.params.rho50_outer_ref, 3),
            "rho50_inner_ref": round(session.params.rho50_inner_ref, 3),
            "imperfection_ref": round(session.params.imperfection_ref, 3),
            "n_cut": round(session.params.n_cut, 3),
            "m_sharp": round(session.params.m_sharp, 3),
            "bypass_outer_coarse": round(session.params.bypass_outer_coarse * 100.0, 2),
            "bypass_outer_slime": round(session.params.bypass_outer_slime * 100.0, 2),
            "d_slime": round(session.params.d_slime, 3),
            "bypass_inner": round(session.params.bypass_inner * 100.0, 2),
        },
        "splitters": {
            "outer_opening": round(session.settings.outer_opening, 1),
            "inner_opening": round(session.settings.inner_opening, 1),
        },
        "profiles": [
            {
                "label": label,
                "size_mm": round(profile.size_mm, 3),
                "rho50_outer": round(profile.rho50_outer, 3),
                "ep_outer": round(profile.ep_outer, 3),
                "rho50_inner": None if profile.rho50_inner is None else round(profile.rho50_inner, 3),
                "ep_inner": None if profile.ep_inner is None else round(profile.ep_inner, 3),
                "bypass_outer": _pct(profile.bypass_outer),
            }
            for label, profile in zip(result.size_labels, result.profiles)
        ],
        "n_products": session.n_products,
        "dense_is_concentrate": session.dense_is_concentrate,
        "parameter_source": session.parameter_source,
        "source_summary": SOURCE_SUMMARY.get(
            session.parameter_source, "Partition parameters for this run."
        ),
        "source_note": session.source_note,
        "fit_message": session.fit_message,
        "warnings": warnings,
        "calibration_note": CALIBRATION_NOTE,
        "fit_comparisons": comparisons,
        "session": session_to_dict(session),
    }


def run_wizard(data: dict) -> dict:
    """Validate a wizard submission, fit if asked, and simulate."""
    if not isinstance(data, dict):
        raise ValueError("The simulation request was empty.")
    feed, notes = build_feed(data)
    operating = build_operating(data)
    try:
        n_products = int(data.get("n_products") or 3)
    except (TypeError, ValueError) as exc:
        raise ValueError("Choose two or three products.") from exc
    if n_products not in (2, 3):
        raise ValueError("Choose two or three products.")
    dense = bool(data.get("dense_is_concentrate", True))
    # A survey replaces the starting cuts, so the preset only matters when
    # there is nothing to fit.
    if data.get("plant"):
        params, note = defaults_from_minerals(feed.minerals)
        source = "density-defaults"
    elif data.get("preset") == "heavy-mineral":
        params = heavy_mineral_preset()
        source = "heavy-mineral-preset"
        note = SOURCE_SUMMARY["heavy-mineral-preset"]
    else:
        params, note = defaults_from_minerals(feed.minerals)
        source = "density-defaults"
    session = Session(
        feed=feed,
        params=params,
        operating=operating,
        n_products=n_products,
        dense_is_concentrate=dense,
        parameter_source=source,
        source_note=note,
    )
    fit_report = None
    if data.get("plant"):
        plant, plant_notes = build_plant(
            data["plant"], n_products, [mineral.name for mineral in feed.minerals]
        )
        notes.extend(plant_notes)
        if plant.observation_count() == 0:
            raise ValueError(
                "Enter at least one yield, grade, or recovery, or go back and skip calibration."
            )
        fit_report = fit_parameters(
            feed, params, operating, plant, n_products, dense
        )
        session.params = fit_report.parameters
        session.plant = plant
        session.parameter_source = "fitted"
        session.fit_message = fit_report.message
        session.settings = SplitterSettings(plant.splitter_outer, plant.splitter_inner)
    splitters = data.get("splitters") or {}
    if not _blank(splitters.get("outer_opening")):
        session.settings.outer_opening = _float(splitters.get("outer_opening"), "outer opening")
    if n_products >= 3 and not _blank(splitters.get("inner_opening")):
        session.settings.inner_opening = _float(splitters.get("inner_opening"), "inner opening")
    targets = data.get("targets") or {}
    if not _blank(targets.get("concentrate")):
        concentrate = _percent(targets.get("concentrate"), "concentrate yield") / 100.0
        middlings = None
        if n_products >= 3:
            middlings = _percent(targets.get("middlings"), "middlings yield") / 100.0
            if concentrate + middlings > 1.0005:
                raise ValueError("Concentrate and middlings together need to be at most 100%.")
        settings, _achieved, target_notes = match_mass_pulls(
            feed,
            session.params,
            session.settings,
            operating,
            n_products,
            dense,
            concentrate,
            middlings,
        )
        session.settings = settings
        notes.extend(target_notes)
    return pack(session, session.run(), notes, fit_report)


def rerun_session(data: dict) -> dict:
    """Re-simulate a saved session after splitter or parameter edits."""
    if not isinstance(data, dict) or "session" not in data:
        raise ValueError("There is no session to run again.")
    session = session_from_dict(data["session"])
    notes: list[str] = []
    edited = False
    parameters = data.get("parameters") or {}
    if parameters:
        _apply_parameter_edits(session, parameters)
        edited = True
    splitters = data.get("splitters") or {}
    if splitters:
        if not _blank(splitters.get("outer_opening")):
            session.settings.outer_opening = _float(splitters.get("outer_opening"), "outer opening")
        if session.n_products >= 3 and not _blank(splitters.get("inner_opening")):
            session.settings.inner_opening = _float(splitters.get("inner_opening"), "inner opening")
    targets = data.get("targets") or {}
    if not _blank(targets.get("concentrate")):
        concentrate = _percent(targets.get("concentrate"), "concentrate yield") / 100.0
        middlings = None
        if session.n_products >= 3:
            middlings = _percent(targets.get("middlings"), "middlings yield") / 100.0
            if concentrate + middlings > 1.0005:
                raise ValueError("Concentrate and middlings together need to be at most 100%.")
        settings, _achieved, target_notes = match_mass_pulls(
            session.feed,
            session.params,
            session.settings,
            session.operating,
            session.n_products,
            session.dense_is_concentrate,
            concentrate,
            middlings,
        )
        session.settings = settings
        notes.extend(target_notes)
    if edited:
        if session.parameter_source == "fitted" and "edited after the fit" not in session.fit_message:
            session.fit_message = (session.fit_message + " Parameters were edited after the fit.").strip()
        if session.parameter_source in {"density-defaults", "heavy-mineral-preset"}:
            session.source_note = ""
        session.parameter_source = "manual"
    return pack(session, session.run(), notes)


def _apply_parameter_edits(session: Session, parameters: dict) -> None:
    params = session.params
    if not _blank(parameters.get("rho50_outer_ref")):
        params.rho50_outer_ref = _float(parameters.get("rho50_outer_ref"), "outer cut density")
    if not _blank(parameters.get("rho50_inner_ref")):
        params.rho50_inner_ref = _float(parameters.get("rho50_inner_ref"), "inner cut density")
    if params.rho50_outer_ref <= params.rho_fluid:
        raise ValueError("The outer cut density needs to be above the fluid density.")
    if params.rho50_inner_ref < params.rho50_outer_ref + 0.05:
        params.rho50_inner_ref = params.rho50_outer_ref + 0.05
    if not _blank(parameters.get("imperfection_ref")):
        params.imperfection_ref = _float(parameters.get("imperfection_ref"), "imperfection")
        if not 0.01 <= params.imperfection_ref <= 0.6:
            raise ValueError("Imperfection needs to be from 0.01 to 0.6.")
    if not _blank(parameters.get("n_cut")):
        params.n_cut = _float(parameters.get("n_cut"), "size exponent")
    if not _blank(parameters.get("m_sharp")):
        params.m_sharp = _float(parameters.get("m_sharp"), "sharpness exponent")
    if not _blank(parameters.get("bypass_outer_coarse")):
        params.bypass_outer_coarse = _percent(
            parameters.get("bypass_outer_coarse"), "coarse bypass"
        ) / 100.0
    if not _blank(parameters.get("bypass_outer_slime")):
        params.bypass_outer_slime = _percent(
            parameters.get("bypass_outer_slime"), "fine bypass"
        ) / 100.0
    if not _blank(parameters.get("bypass_inner")):
        params.bypass_inner = _percent(parameters.get("bypass_inner"), "inner bypass") / 100.0
    if parameters.get("curve") in {"erf", "logistic"}:
        params.curve = parameters["curve"]


def run_saved(data: dict) -> dict:
    """Simulate a session file uploaded by the browser."""
    if not isinstance(data, dict):
        raise ValueError("That file is not a spiral session.")
    session = session_from_dict(data)
    return pack(session, session.run())


def example_payload() -> dict:
    session = heavy_mineral_sands()
    return pack(session, session.run())


def make_server(host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler name
            path = self.path.split("?", 1)[0]
            if path in {"/", "/index.html"}:
                self._send_page()
            elif path == "/api/example":
                self._send_json(example_payload())
            else:
                self._send_json({"ok": False, "error": "That page does not exist."}, 404)

        def do_POST(self):  # noqa: N802 - stdlib handler name
            path = self.path.split("?", 1)[0]
            try:
                payload = self._read_json()
                if path == "/api/simulate":
                    result = run_wizard(payload)
                elif path == "/api/rerun":
                    result = rerun_session(payload)
                elif path == "/api/session":
                    result = run_saved(payload)
                else:
                    self._send_json({"ok": False, "error": "That action does not exist."}, 404)
                    return
            except (ValueError, KeyError, TypeError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            self._send_json(result)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 2_000_000:
                raise ValueError("That request is too large.")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("The request was not valid JSON.") from exc
            if not isinstance(data, dict):
                raise ValueError("The request needs to be a JSON object.")
            return data

        def _send_page(self) -> None:
            page = static_page_path()
            if not page.exists():
                self._send_json({"ok": False, "error": "The page file is missing."}, 500)
                return
            body = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            print(f"[spiral] {self.address_string()} {format % args}")

    return ThreadingHTTPServer((host, port), Handler)


def _open_browser_later(url: str) -> None:
    """Give the server a moment to listen, then open the user's browser."""
    time.sleep(0.6)
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        print(f"Open this address in your browser: {url}")


def serve(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = False) -> None:
    """Serve the simulator until the process is stopped."""
    server = make_server(host, port)
    shown_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{shown_host}:{port}"
    print("Spiral Concentrator")
    print(f"The simulator is running at {url}")
    print("Leave this window open while you use it.")
    print("Closing this window stops the simulator.")
    if open_browser:
        print("Your browser should open on its own. If it does not, paste the address above into the browser.")
        threading.Thread(target=_open_browser_later, args=(url,), daemon=True).start()
    else:
        print("Open that address in a browser.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
