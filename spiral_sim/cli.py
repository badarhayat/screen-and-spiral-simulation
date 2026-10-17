"""Conversational command-line interface.

Questions are asked one at a time. Feed data come first. Plant data are
optional: with a survey the partition parameters are fitted, and without one
the run uses density-based starting values and says that they are approximate.
Splitter openings and cut densities can be changed afterwards without
entering the feed again.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from spiral_sim.calibrate import PlantData, fit_parameters
from spiral_sim.defaults import defaults_from_minerals, heavy_mineral_preset
from spiral_sim.examples import calibration_demo, heavy_mineral_sands
from spiral_sim.feed import Feed, Mineral, SizeClass
from spiral_sim.model import OperatingConditions, SplitterSettings
from spiral_sim.report import render, render_fit
from spiral_sim.session import Session, load_session, save_session
from spiral_sim.simulate import PRODUCT_LABELS, active_products
from spiral_sim.targets import match_mass_pulls


MAIN_MENU = [
    ("1", "Start a new simulation"),
    ("2", "Load a saved session"),
    ("3", "Run the built-in heavy-mineral sands example"),
    ("4", "Watch a calibration demo on a synthetic survey"),
    ("5", "Quit"),
]

INNER_MENU = [
    ("1", "Change splitter openings and re-simulate"),
    ("2", "Match a target mass yield and re-simulate"),
    ("3", "Edit partition parameters and re-simulate"),
    ("4", "Enter plant data and calibrate"),
    ("5", "Save this session"),
    ("6", "Load a saved session"),
    ("7", "Start over with a new feed"),
    ("8", "Quit"),
]


class UI:
    """Line-oriented prompts. Input and output can be replaced in tests."""

    def __init__(self, inp=None, out=None):
        self._in = inp if inp is not None else sys.stdin
        self._out = out if out is not None else sys.stdout

    def say(self, text: str = "") -> None:
        print(text, file=self._out)
        self._out.flush()

    def ask(self, prompt: str) -> str:
        self.say(prompt)
        self._out.write("> ")
        self._out.flush()
        line = self._in.readline()
        if line == "":
            raise EOFError
        return line.strip()

    def ask_yes(self, prompt: str) -> bool:
        while True:
            raw = self.ask(prompt + " (y/n)").lower()
            if raw in {"y", "yes"}:
                return True
            if raw in {"n", "no"}:
                return False
            self.say("Please answer y or n.")

    def ask_choice(self, title: str, options: list[tuple[str, str]]) -> str:
        self.say(title)
        for key, label in options:
            self.say(f"  {key}) {label}")
        allowed = {key.lower(): key for key, _label in options}
        while True:
            raw = self.ask("Your choice").lower()
            if raw in allowed:
                return allowed[raw]
            names = ", ".join(key for key, _label in options)
            self.say(f"Please choose one of: {names}.")

    def ask_float(
        self,
        prompt: str,
        low: float | None = None,
        high: float | None = None,
        allow_blank: bool = False,
    ) -> float | None:
        while True:
            raw = self.ask(prompt)
            if raw == "" and allow_blank:
                return None
            cleaned = raw.strip().replace("%", "").strip().replace(",", ".")
            try:
                value = float(cleaned)
            except ValueError:
                self.say("Please enter a number.")
                continue
            if low is not None and value < low:
                self.say(f"Please enter a number of at least {low:g}.")
                continue
            if high is not None and value > high:
                self.say(f"Please enter a number no higher than {high:g}.")
                continue
            return value

    def ask_int(self, prompt: str, low: int, high: int) -> int:
        while True:
            value = self.ask_float(prompt, low=low, high=high, allow_blank=False)
            assert value is not None
            if abs(value - round(value)) > 1e-9:
                self.say("Please enter a whole number.")
                continue
            return int(round(value))

    def ask_percent(
        self,
        prompt: str,
        low: float = 0.0,
        high: float = 100.0,
        allow_blank: bool = False,
    ) -> float | None:
        return self.ask_float(prompt, low=low, high=high, allow_blank=allow_blank)


class App:
    def __init__(self, ui: UI):
        self.ui = ui
        self.session: Session | None = None

    def run(self) -> int:
        self._banner()
        while True:
            choice = self.ui.ask_choice("What would you like to do?", MAIN_MENU)
            if choice == "1":
                self.session = self.collect_session()
                self.show()
                if self.interact() == "quit":
                    return 0
            elif choice == "2":
                loaded = self.load_dialog()
                if loaded is None:
                    continue
                self.session = loaded
                self.show()
                if self.interact() == "quit":
                    return 0
            elif choice == "3":
                self.session = heavy_mineral_sands()
                self.ui.say("")
                self.ui.say(
                    "This example is a heavy-mineral sands feed: quartz, rutile, and zircon. "
                    "No plant survey is loaded, so the cut densities are the general starting "
                    "values placed between the lightest and heaviest minerals."
                )
                self.show()
                if self.interact() == "quit":
                    return 0
            elif choice == "4":
                self._run_demo()
                if self.interact() == "quit":
                    return 0
            elif choice == "5":
                self.ui.say("Goodbye.")
                return 0
        return 0

    def _banner(self) -> None:
        self.ui.say("Spiral concentrator simulator")
        self.ui.say("-----------------------------")
        self.ui.say(
            "This follows the generalized size-density partition method in R.P. King's "
            "Modeling and Simulation of Mineral Processing Systems, Chapter 7."
        )
        self.ui.say(
            "A spiral with three products is two cuts in series. The outer splitter "
            "separates tails from the inner material, and the inner splitter then "
            "separates concentrate from middlings. Each cut depends on particle size "
            "and mineral density, with a short-circuit bypass."
        )
        self.ui.say(
            "The same equations are used for any minerals you enter. I will ask for "
            "the feed first, one question at a time. If you have a plant survey I can "
            "fit the cuts to it. If you do not, I will start from your mineral densities "
            "and typical spiral sharpness, and I will say that the results are approximate."
        )
        self.ui.say("")

    def _run_demo(self) -> None:
        self.ui.say("")
        self.ui.say(
            "I am generating a synthetic survey from a known heavy-mineral spiral, "
            "rounding it to 0.1 percentage points, and fitting from the density-based start. "
            "This is a demonstration of calibration, not a real plant survey."
        )
        session, truth, start, report = calibration_demo()
        self.session = session
        self.ui.say(
            f"The synthetic spiral used an outer cut of {truth.rho50_outer_ref:.2f} g/cm3, "
            f"an inner cut of {truth.rho50_inner_ref:.2f} g/cm3, and an imperfection of "
            f"{truth.imperfection_ref:.2f}."
        )
        self.ui.say(render_fit(start, report, session.n_products))
        self.show()

    def show(self) -> None:
        assert self.session is not None
        result = self.session.run()
        self.ui.say("")
        self.ui.say(render(self.session, result))
        self.ui.say(
            "You can change the splitter openings or the cut densities and run again "
            "without entering the feed again."
        )

    def interact(self) -> str:
        """Post-simulation menu. Returns 'quit' or 'main'."""
        while True:
            assert self.session is not None
            self.ui.say("")
            choice = self.ui.ask_choice("What would you like to do next?", INNER_MENU)
            if choice == "1":
                self.change_splitters()
                self.show()
            elif choice == "2":
                self.match_targets()
                self.show()
            elif choice == "3":
                self.edit_parameters()
                self.show()
            elif choice == "4":
                if self.calibrate_dialog():
                    self.show()
            elif choice == "5":
                self.save_dialog()
            elif choice == "6":
                loaded = self.load_dialog()
                if loaded is not None:
                    self.session = loaded
                    self.show()
            elif choice == "7":
                if self.ui.ask_yes("This starts the feed questions again. Continue?"):
                    self.session = self.collect_session()
                    self.show()
            elif choice == "8":
                self.ui.say("Goodbye.")
                return "quit"
        return "main"

    def collect_session(self) -> Session:
        while True:
            size_classes = self.collect_sizes()
            minerals = self.collect_minerals()
            composition = self.collect_composition(size_classes, minerals)
            operating = self.collect_operating()
            feed = Feed(size_classes=size_classes, minerals=minerals, composition=composition)
            if self.confirm_feed(feed, operating):
                break
            self.ui.say("Let's enter the feed again.")
            self.ui.say("")

        params, note = defaults_from_minerals(minerals)
        session = Session(
            feed=feed,
            params=params,
            operating=operating,
            parameter_source="density-defaults",
            source_note=note,
        )
        session.dense_is_concentrate = self.ask_dense_side()
        session.n_products = self.ask_product_count()

        self.ui.say("")
        has_data = self.ui.ask_choice(
            "Do you have measured plant results for this spiral (yields, grades, or recoveries)?",
            [
                ("1", "Yes. Fit the partition parameters to my data."),
                ("2", "No. Use starting parameters based on the mineral densities."),
            ],
        )
        if has_data == "1":
            self.ui.say("")
            accepted = self.calibrate_dialog(session)
            if not accepted:
                self.ui.say(
                    "I will use the starting parameters based on your mineral densities. "
                    "The results are approximate until the model is calibrated."
                )
                self._ask_initial_splitters(session)
        else:
            self.ui.say("")
            self.ui.say(
                "I will use starting parameters based on your mineral densities. "
                "Sharpness and bypass are typical of a spiral in good condition. "
                "The results are approximate until you calibrate with plant data."
            )
            self._ask_initial_splitters(session)
        return session

    def confirm_feed(self, feed: Feed, operating: OperatingConditions) -> bool:
        self.ui.say("")
        self.ui.say("Here is the feed I have.")
        for size_class in feed.size_classes:
            self.ui.say(f"  {size_class.label()}: {size_class.mass_fraction * 100:.2f}% of the feed")
        for index, mineral in enumerate(feed.minerals):
            self.ui.say(
                f"  {mineral.name}, {mineral.density:.3f} g/cm3: "
                f"{feed.overall_grade(index) * 100:.2f}% of the feed"
            )
        if operating.feed_rate_tph is None:
            self.ui.say("  Feed rate: not set")
        else:
            self.ui.say(f"  Feed rate: {operating.feed_rate_tph:g} t/h")
        if operating.pct_solids is None:
            self.ui.say("  Percent solids: 30% assumed")
        else:
            self.ui.say(f"  Percent solids: {operating.pct_solids:g}%")
        return self.ui.ask_yes("Shall I use this feed?")

    def collect_sizes(self) -> list[SizeClass]:
        self.ui.say("")
        self.ui.say("Let's describe the feed size distribution.")
        while True:
            count = self.ui.ask_int("How many size classes are in the feed?", low=1, high=40)
            raw: list[tuple[float, float, float]] = []
            for index in range(count):
                self.ui.say("")
                self.ui.say(f"Size class {index + 1} of {count}.")
                lower = self.ui.ask_float(
                    "Lower screen size in mm (enter 0 if this class passes the finest screen)",
                    low=0.0,
                    high=100.0,
                )
                assert lower is not None
                while True:
                    upper = self.ui.ask_float("Upper screen size in mm", low=0.0, high=100.0)
                    assert upper is not None
                    if upper > lower:
                        break
                    self.ui.say("The upper size needs to be larger than the lower size.")
                mass = self.ui.ask_percent(
                    "Mass percent of the feed in this class",
                    low=0.0,
                    high=100.0,
                )
                assert mass is not None
                raw.append((lower, upper, mass))

            total = sum(mass for _low, _high, mass in raw)
            self.ui.say("")
            self.ui.say("Size distribution:")
            for index, (lower, upper, mass) in enumerate(raw, start=1):
                self.ui.say(f"  {index}) {lower:.3f}-{upper:.3f} mm, {mass:.2f}%")
            self.ui.say(f"  Total {total:.2f}%")
            if total <= 0:
                self.ui.say("The mass percents add up to zero. Let's enter them again.")
                continue
            if abs(total - 100.0) > 0.5:
                self.ui.say(
                    f"Those mass percents add up to {total:.2f}%, and the feed needs to add up to 100%."
                )
                choice = self.ui.ask_choice(
                    "How should I handle that?",
                    [
                        ("1", "Scale them so they add up to 100%"),
                        ("2", "Enter the size classes again"),
                    ],
                )
                if choice == "2":
                    continue
                raw = [(lower, upper, 100.0 * mass / total) for lower, upper, mass in raw]
            if self.ui.ask_yes("Is this size distribution correct?"):
                percents = [mass for _lower, _upper, mass in raw]
                fractions = [percent / 100.0 for percent in percents]
                fractions[-1] = 1.0 - sum(fractions[:-1])
                return [
                    SizeClass(d_low=lower, d_high=upper, mass_fraction=max(0.0, fraction))
                    for (lower, upper, _mass), fraction in zip(raw, fractions)
                ]
            self.ui.say("Let's enter the size classes again.")

    def collect_minerals(self) -> list[Mineral]:
        self.ui.say("")
        self.ui.say("Now the minerals. Use one entry for a group if you assay it as a group.")
        while True:
            count = self.ui.ask_int(
                "How many minerals (or mineral groups) are in the feed?",
                low=1,
                high=12,
            )
            minerals: list[Mineral] = []
            seen: set[str] = set()
            for index in range(count):
                self.ui.say("")
                self.ui.say(f"Mineral {index + 1} of {count}.")
                while True:
                    name = self.ui.ask("Name")
                    if not name:
                        self.ui.say("Please give the mineral a name.")
                        continue
                    if name.lower() in seen:
                        self.ui.say("That name is already in the list. Use a different name.")
                        continue
                    break
                while True:
                    density = self.ui.ask_float(
                        "Density in g/cm3 (the same number as specific gravity)",
                        low=0.05,
                        high=30.0,
                    )
                    assert density is not None
                    if density < 1.0:
                        self.ui.say(
                            f"{density:g} g/cm3 is lighter than water, so this material will not settle."
                        )
                        if not self.ui.ask_yes("Use this density anyway?"):
                            continue
                    break
                seen.add(name.lower())
                minerals.append(Mineral(name, density))
            self.ui.say("")
            self.ui.say("Minerals:")
            for index, mineral in enumerate(minerals, start=1):
                self.ui.say(f"  {index}) {mineral.name}, {mineral.density:.3f} g/cm3")
            if self.ui.ask_yes("Is this mineral list correct?"):
                return minerals
            self.ui.say("Let's enter the minerals again.")

    def collect_composition(self, size_classes: list[SizeClass], minerals: list[Mineral]) -> list[list[float]]:
        self.ui.say("")
        if len(minerals) == 1:
            self.ui.say(
                f"Only {minerals[0].name} is in the feed, so every size class is 100% {minerals[0].name}."
            )
            return [[1.0] for _size in size_classes]
        choice = self.ui.ask_choice(
            "Do you have a separate mineral analysis for each size class?",
            [
                ("1", "Yes, I have assays by size"),
                ("2", "No. I only have the overall mineral grades."),
            ],
        )
        if choice == "2":
            self.ui.say(
                "Enter the overall mass percent of each mineral. "
                "Leave the last one blank and I will use whatever is left."
            )
            fractions = self.ask_percent_split([mineral.name for mineral in minerals])
            return [list(fractions) for _size in size_classes]

        rows: list[list[float]] = []
        for index, size_class in enumerate(size_classes):
            self.ui.say("")
            self.ui.say(
                f"Size class {index + 1} of {len(size_classes)} ({size_class.label()})."
            )
            if index > 0 and self.ui.ask_yes(
                "Use the same mineral percentages as the previous size class?"
            ):
                rows.append(list(rows[-1]))
                continue
            self.ui.say(
                "Enter the mass percent of each mineral in this class. "
                "Leave the last one blank to use the remainder."
            )
            rows.append(self.ask_percent_split([mineral.name for mineral in minerals]))
        return rows

    def ask_percent_split(self, labels: list[str]) -> list[float]:
        """Ask for percents that sum to 100. Returns fractions."""
        while True:
            percents: list[float] = []
            remaining = 100.0
            restart = False
            for index, label in enumerate(labels):
                last = index == len(labels) - 1
                if last:
                    value = self.ui.ask_percent(
                        f"{label}, mass percent (Enter for the remaining {max(remaining, 0.0):.2f}%)",
                        low=0.0,
                        high=max(remaining, 0.0) if remaining > 0 else 0.0,
                        allow_blank=True,
                    )
                    if value is None:
                        value = max(remaining, 0.0)
                else:
                    value = self.ui.ask_percent(
                        f"{label}, mass percent (up to {max(remaining, 0.0):.2f}% is still available)",
                        low=0.0,
                        high=max(remaining, 0.0),
                    )
                    assert value is not None
                if value - remaining > 0.051:
                    self.ui.say(f"That is more than the {remaining:.2f}% still available.")
                    restart = True
                    break
                remaining -= value
                percents.append(value)
            if restart:
                continue
            total = sum(percents)
            if abs(total - 100.0) <= 0.05:
                fractions = [percent / 100.0 for percent in percents]
                fractions[-1] = max(0.0, 1.0 - sum(fractions[:-1]))
                return fractions
            self.ui.say(f"Those add up to {total:.2f}%, and they need to add up to 100%.")
            choice = self.ui.ask_choice(
                "How should I handle that?",
                [
                    ("1", "Scale them so they add up to 100%"),
                    ("2", "Enter them again"),
                ],
            )
            if choice == "1":
                if total <= 0:
                    self.ui.say("I can't scale a list that adds up to zero.")
                    continue
                fractions = [percent / total for percent in percents]
                fractions[-1] = max(0.0, 1.0 - sum(fractions[:-1]))
                return fractions

    def collect_operating(self) -> OperatingConditions:
        self.ui.say("")
        rate = self.ui.ask_float(
            "Feed solids rate in tonnes per hour (Enter to skip)",
            low=1e-6,
            high=100000.0,
            allow_blank=True,
        )
        solids = self.ui.ask_float(
            "Percent solids by weight (Enter to assume 30%, a typical spiral feed)",
            low=1.0,
            high=85.0,
            allow_blank=True,
        )
        if solids is not None and solids > 55.0:
            self.ui.say(
                "Spirals are usually fed well below 55% solids. I will still use this number. "
                "The pulp-density correction is only a mild adjustment around 30% solids."
            )
        return OperatingConditions(feed_rate_tph=rate, pct_solids=solids)

    def ask_dense_side(self) -> bool:
        self.ui.say("")
        choice = self.ui.ask_choice(
            "Which product is the dense one on this spiral?",
            [
                (
                    "1",
                    "Concentrate is dense (the inner product). Usual for heavy minerals and most ores.",
                ),
                (
                    "2",
                    "Concentrate is light (the outer product). Usual for coal.",
                ),
            ],
        )
        return choice == "1"

    def ask_product_count(self) -> int:
        choice = self.ui.ask_choice(
            "How many products should the spiral make?",
            [
                ("1", "Three: concentrate, middlings, and tails"),
                ("2", "Two: concentrate and tails"),
            ],
        )
        return 3 if choice == "1" else 2

    def _ask_initial_splitters(self, session: Session) -> None:
        self.ui.say("")
        choice = self.ui.ask_choice(
            "How should I set the splitters for this run?",
            [
                ("1", "Mid position (opening 50), so the reference cut densities are used as they are"),
                ("2", "I will enter splitter openings"),
                ("3", "Match a concentrate mass yield I care about"),
            ],
        )
        if choice == "1":
            session.settings = SplitterSettings()
        elif choice == "2":
            self.change_splitters(session)
        else:
            self.match_targets(session)

    def change_splitters(self, session: Session | None = None) -> None:
        session = session or self.session
        assert session is not None
        self.ui.say("")
        if session.dense_is_concentrate:
            self.ui.say(
                "Opening 0 is tight: a higher cut density and less dense concentrate. "
                "Opening 100 is wide: a lower cut density and more dense concentrate. "
                "Opening 50 keeps the reference cut density."
            )
        else:
            self.ui.say(
                "On this light-concentrate spiral, a wider opening lets more dense material "
                "move inward to the tails, so the light concentrate yield falls. "
                "Opening 0 keeps more material in the outer concentrate. Opening 50 is the reference."
            )
        session.settings.outer_opening = self._ask_opening(
            "Outer splitter opening, from 0 to 100",
            session.settings.outer_opening,
        )
        if session.n_products >= 3:
            session.settings.inner_opening = self._ask_opening(
                "Inner splitter opening, from 0 to 100",
                session.settings.inner_opening,
            )

    def _ask_opening(self, prompt: str, current: float) -> float:
        while True:
            value = self.ui.ask_float(
                f"{prompt} (Enter keeps {current:.1f})",
                low=-100.0,
                high=250.0,
                allow_blank=True,
            )
            if value is None:
                return current
            if value < 0.0 or value > 100.0:
                self.ui.say(
                    "That is outside the usual 0 to 100 scale. I can still use it as an effective opening."
                )
                if not self.ui.ask_yes("Use it?"):
                    continue
            return value

    def match_targets(self, session: Session | None = None) -> None:
        session = session or self.session
        assert session is not None
        self.ui.say("")
        self.ui.say("Enter the mass yield you want, as a percent of feed. Tails take what is left.")
        while True:
            concentrate = self.ui.ask_percent("Concentrate mass yield, percent", low=0.0, high=100.0)
            assert concentrate is not None
            middlings = None
            if session.n_products >= 3:
                middlings = self.ui.ask_percent(
                    "Middlings mass yield, percent",
                    low=0.0,
                    high=max(0.0, 100.0 - concentrate),
                )
                assert middlings is not None
                if concentrate + middlings > 100.05:
                    self.ui.say("Concentrate and middlings together need to be at most 100%.")
                    continue
            break
        settings, _achieved, notes = match_mass_pulls(
            session.feed,
            session.params,
            session.settings,
            session.operating,
            session.n_products,
            session.dense_is_concentrate,
            concentrate_yield=concentrate / 100.0,
            middlings_yield=None if middlings is None else middlings / 100.0,
        )
        session.settings = settings
        for note in notes:
            self.ui.say(note)
        message = f"Outer opening is now {settings.outer_opening:.1f}."
        if session.n_products >= 3:
            message += f" Inner opening is now {settings.inner_opening:.1f}."
        self.ui.say(message)

    def edit_parameters(self, session: Session | None = None) -> None:
        session = session or self.session
        assert session is not None
        while True:
            params = session.params
            solids = (
                "30% assumed"
                if session.operating.pct_solids is None
                else f"{session.operating.pct_solids:.1f}%"
            )
            rate = (
                "not set"
                if session.operating.feed_rate_tph is None
                else f"{session.operating.feed_rate_tph:g} t/h"
            )
            dense = "concentrate" if session.dense_is_concentrate else "tails"
            self.ui.say("")
            self.ui.say("Partition parameters. Enter d when you want to simulate again.")
            self.ui.say(f"  1) Outer cut density at {params.d_ref:.3f} mm: {params.rho50_outer_ref:.3f} g/cm3")
            self.ui.say(f"  2) Inner cut density at {params.d_ref:.3f} mm: {params.rho50_inner_ref:.3f} g/cm3")
            self.ui.say(f"  3) Size exponent n: {params.n_cut:.3f}")
            self.ui.say(f"  4) Imperfection I*: {params.imperfection_ref:.3f}")
            self.ui.say(f"  5) Sharpness size exponent m: {params.m_sharp:.3f}")
            self.ui.say(f"  6) Coarse bypass to the outer product: {params.bypass_outer_coarse * 100:.2f}%")
            self.ui.say(f"  7) Fine-particle bypass amplitude: {params.bypass_outer_slime * 100:.2f}%")
            self.ui.say(f"  8) Fine-particle characteristic size: {params.d_slime:.3f} mm")
            self.ui.say(f"  9) Bypass to the inner product: {params.bypass_inner * 100:.2f}%")
            self.ui.say(f"  10) Curve: {params.curve_label()}")
            self.ui.say(f"  11) Percent solids: {solids}")
            self.ui.say(f"  12) Feed rate: {rate}")
            self.ui.say(f"  13) Number of products: {session.n_products}")
            self.ui.say(f"  14) Dense product: {dense}")
            self.ui.say("   r) Reset cuts, sharpness, and bypass from the mineral densities")
            self.ui.say("   h) Use heavy-mineral sands textbook cuts")
            self.ui.say("   d) Done")
            choice = self.ui.ask("Your choice").strip().lower()
            if choice == "d":
                return
            if choice == "r":
                params, note = defaults_from_minerals(session.feed.minerals)
                session.params = params
                session.parameter_source = "density-defaults"
                session.source_note = note
                session.fit_message = ""
                self.ui.say(note)
                continue
            if choice == "h":
                densities = [mineral.density for mineral in session.feed.minerals]
                unlike = max(densities) < 3.2 or min(densities) > 3.6
                if unlike:
                    self.ui.say(
                        "Those textbook cuts sit between about 3.2 and 4.0 g/cm3, "
                        "which is the quartz to heavy-mineral range."
                    )
                    if not self.ui.ask_yes("Use them with your minerals anyway?"):
                        continue
                session.params = heavy_mineral_preset()
                session.parameter_source = "heavy-mineral-preset"
                session.source_note = "Heavy-mineral textbook cuts replaced the previous parameters."
                session.fit_message = ""
                self.ui.say("Heavy-mineral textbook cuts are now in use. They are still uncalibrated.")
                continue
            if choice == "1":
                value = self.ui.ask_float(
                    "Outer cut density at the reference size, g/cm3",
                    low=session.params.rho_fluid + 0.05,
                    high=12.0,
                )
                assert value is not None
                session.params.rho50_outer_ref = value
                if session.params.rho50_inner_ref < value + 0.05:
                    session.params.rho50_inner_ref = value + 0.05
                    self.ui.say(
                        "I raised the inner cut to "
                        f"{session.params.rho50_inner_ref:.3f} g/cm3 so it stays above the outer cut."
                    )
                self._touch_manual(session)
            elif choice == "2":
                value = self.ui.ask_float(
                    "Inner cut density at the reference size, g/cm3",
                    low=session.params.rho50_outer_ref + 0.05,
                    high=12.0,
                )
                assert value is not None
                session.params.rho50_inner_ref = value
                self._touch_manual(session)
            elif choice == "3":
                session.params.n_cut = self._require(
                    self.ui.ask_float("Size exponent n (0 keeps the cut flat versus size)", low=0.0, high=1.5)
                )
                self._touch_manual(session)
            elif choice == "4":
                session.params.imperfection_ref = self._require(
                    self.ui.ask_float("Imperfection I* at the reference size", low=0.01, high=0.6)
                )
                self._touch_manual(session)
            elif choice == "5":
                session.params.m_sharp = self._require(
                    self.ui.ask_float(
                        "Sharpness size exponent m (0 keeps imperfection flat versus size)",
                        low=0.0,
                        high=1.5,
                    )
                )
                self._touch_manual(session)
            elif choice == "6":
                session.params.bypass_outer_coarse = self._require(
                    self.ui.ask_percent("Coarse bypass to the outer product, percent", low=0.0, high=40.0)
                ) / 100.0
                self._touch_manual(session)
            elif choice == "7":
                session.params.bypass_outer_slime = self._require(
                    self.ui.ask_percent("Fine-particle bypass amplitude, percent", low=0.0, high=80.0)
                ) / 100.0
                self._touch_manual(session)
            elif choice == "8":
                session.params.d_slime = self._require(
                    self.ui.ask_float("Fine-particle characteristic size, mm", low=0.005, high=1.0)
                )
                self._touch_manual(session)
            elif choice == "9":
                session.params.bypass_inner = self._require(
                    self.ui.ask_percent("Bypass to the inner product, percent", low=0.0, high=30.0)
                ) / 100.0
                self._touch_manual(session)
            elif choice == "10":
                curve = self.ui.ask_choice(
                    "Which partition curve should I use?",
                    [
                        ("1", "Error function, the form King uses for partition curves"),
                        ("2", "Logistic sigmoid, with the same 50% and 75% points"),
                    ],
                )
                session.params.curve = "erf" if curve == "1" else "logistic"
                self._touch_manual(session)
            elif choice == "11":
                solids_value = self.ui.ask_float(
                    "Percent solids by weight (Enter to assume 30%)",
                    low=1.0,
                    high=85.0,
                    allow_blank=True,
                )
                session.operating.pct_solids = solids_value
            elif choice == "12":
                rate_value = self.ui.ask_float(
                    "Feed solids rate in tonnes per hour (Enter to clear it)",
                    low=1e-6,
                    high=100000.0,
                    allow_blank=True,
                )
                session.operating.feed_rate_tph = rate_value
            elif choice == "13":
                session.n_products = self.ask_product_count()
            elif choice == "14":
                session.dense_is_concentrate = self.ask_dense_side()
            else:
                self.ui.say("Please choose a number from the list, or r, h, or d.")

    def _touch_manual(self, session: Session) -> None:
        if session.parameter_source == "fitted" and session.fit_message:
            if "edited after the fit" not in session.fit_message:
                session.fit_message = (
                    session.fit_message.rstrip() + " Parameters were edited after the fit."
                )
        if session.parameter_source in {"density-defaults", "heavy-mineral-preset"}:
            # The old note described the starting cuts, which no longer all apply.
            session.source_note = ""
        session.parameter_source = "manual"

    @staticmethod
    def _require(value: float | None) -> float:
        assert value is not None
        return value

    def calibrate_dialog(self, session: Session | None = None) -> bool:
        """Collect a survey and optionally replace the partition parameters."""
        session = session or self.session
        assert session is not None
        if session.plant is not None and session.plant.observation_count() > 0:
            if not self.ui.ask_yes("This replaces the plant survey stored in the session. Continue?"):
                self.ui.say("Kept the existing survey.")
                return False
        plant = self.collect_plant(session)
        if plant is None:
            self.ui.say("No measured numbers were entered, so I kept the current parameters.")
            return False
        self.ui.say("")
        self.ui.say("Fitting the cut density, sharpness, and bypass. This usually takes a moment.")
        before = replace(session.params)
        try:
            report = fit_parameters(
                session.feed,
                before,
                session.operating,
                plant,
                session.n_products,
                session.dense_is_concentrate,
            )
        except ValueError as exc:
            self.ui.say(str(exc))
            return False
        self.ui.say("")
        self.ui.say(render_fit(before, report, session.n_products))
        choice = self.ui.ask_choice(
            "Use the fitted parameters?",
            [
                ("1", "Yes. Use the fitted parameters."),
                ("2", "No. Keep the parameters from before this fit."),
            ],
        )
        if choice == "2":
            self.ui.say("Kept the previous parameters. The new survey was not stored.")
            return False
        session.params = report.parameters
        session.plant = plant
        session.parameter_source = "fitted"
        session.fit_message = report.message
        session.settings = SplitterSettings(
            outer_opening=plant.splitter_outer,
            inner_opening=plant.splitter_inner,
        )
        self.ui.say("The splitters are now at the position the survey was taken at.")
        return True

    def collect_plant(self, session: Session) -> PlantData | None:
        products = active_products(session.n_products)
        self.ui.say(
            "I will ask for one number at a time. Press Enter for any number you do not have. "
            "Yields are percent of feed. Grades are mass percent in the product. "
            "Recoveries are percent of the mineral in the feed."
        )
        yields = self.collect_yields(products)
        assay = self.ui.ask_choice(
            "Which assays do you have?",
            [
                ("1", "Grades of the products"),
                ("2", "Recoveries of each mineral"),
                ("3", "Both grades and recoveries"),
                ("4", "Neither. Yields, or a size split, are all I have."),
            ],
        )
        grades = {product: {} for product in products}
        recoveries = {product: {} for product in products}
        if assay in {"1", "3"}:
            for product in products:
                grades[product] = self.collect_grades(product, session.feed.minerals)
        if assay in {"2", "3"}:
            for product in products:
                recoveries[product] = self.collect_recoveries(product, session.feed.minerals)
            self._warn_recovery_closure(recoveries, session.feed.minerals, products)
        size_splits = self.collect_size_splits(session, products)
        self.ui.say("")
        self.ui.say(
            "Cut densities are stored at splitter opening 50. "
            "Tell me the openings during the survey and I will fit the opening-50 cuts "
            "so that this position reproduces your data. Enter for a mid setting of 50."
        )
        outer = self._ask_opening("Outer splitter opening during the survey", 50.0)
        inner = 50.0
        if session.n_products >= 3:
            inner = self._ask_opening("Inner splitter opening during the survey", 50.0)
        plant = PlantData(
            yields=yields,
            grades=grades,
            recoveries=recoveries,
            size_splits=size_splits,
            splitter_outer=outer,
            splitter_inner=inner,
        )
        if plant.observation_count() == 0:
            return None
        return plant

    def collect_yields(self, products: tuple[str, ...]) -> dict[str, float | None]:
        self.ui.say("")
        self.ui.say("Mass yields. Enter to skip a product you did not weigh.")
        while True:
            values: dict[str, float | None] = {}
            for product in products:
                values[product] = self.ui.ask_percent(
                    f"Mass yield of {PRODUCT_LABELS[product]}, percent of feed (Enter to skip)",
                    low=0.0,
                    high=100.0,
                    allow_blank=True,
                )
            given = {key: value for key, value in values.items() if value is not None}
            if len(given) == len(products) - 1:
                missing = next(key for key in products if values[key] is None)
                remainder = 100.0 - sum(given.values())
                if remainder < -0.05:
                    self.ui.say(
                        f"Those yields already add up to {sum(given.values()):.2f}%, which is over 100. "
                        "Let's enter the yields again."
                    )
                    continue
                remainder = max(0.0, remainder)
                values[missing] = remainder
                self.ui.say(
                    f"I set the {PRODUCT_LABELS[missing]} yield to {remainder:.2f}% "
                    "so the products sum to 100%."
                )
            known = [value for value in values.values() if value is not None]
            if known and len(known) == len(products):
                total = sum(known)
                if abs(total - 100.0) > 1.0:
                    self.ui.say(f"The yields add up to {total:.2f}%, not 100%.")
                    choice = self.ui.ask_choice(
                        "How should I use them?",
                        [
                            ("1", "Scale them to 100%"),
                            ("2", "Keep my numbers"),
                            ("3", "Enter them again"),
                        ],
                    )
                    if choice == "3":
                        continue
                    if choice == "1" and total > 0:
                        values = {
                            key: None if value is None else 100.0 * value / total
                            for key, value in values.items()
                        }
            return {
                key: None if value is None else value / 100.0 for key, value in values.items()
            }

    def collect_grades(self, product: str, minerals: list[Mineral]) -> dict[str, float | None]:
        while True:
            self.ui.say("")
            self.ui.say(
                f"{PRODUCT_LABELS[product]} grades. Enter to skip a mineral you did not assay."
            )
            values: dict[str, float | None] = {}
            for mineral in minerals:
                values[mineral.name] = self.ui.ask_percent(
                    f"Grade of {mineral.name} in the {PRODUCT_LABELS[product]}, "
                    "mass percent (Enter to skip)",
                    low=0.0,
                    high=100.0,
                    allow_blank=True,
                )
            entered = [value for value in values.values() if value is not None]
            if len(entered) == len(minerals) and minerals:
                total = sum(entered)
                if abs(total - 100.0) > 1.5:
                    self.ui.say(f"Those grades add up to {total:.2f}%, not 100%.")
                    choice = self.ui.ask_choice(
                        "How should I use them?",
                        [
                            ("1", "Scale them to 100%"),
                            ("2", "Keep my numbers"),
                            ("3", "Enter them again"),
                        ],
                    )
                    if choice == "3":
                        continue
                    if choice == "1" and total > 0:
                        values = {
                            name: None if value is None else 100.0 * value / total
                            for name, value in values.items()
                        }
            return {name: None if value is None else value / 100.0 for name, value in values.items()}

    def collect_recoveries(self, product: str, minerals: list[Mineral]) -> dict[str, float | None]:
        self.ui.say("")
        self.ui.say(
            f"Recovery to the {PRODUCT_LABELS[product]}. Enter to skip a mineral."
        )
        values: dict[str, float | None] = {}
        for mineral in minerals:
            values[mineral.name] = self.ui.ask_percent(
                f"Recovery of {mineral.name} to the {PRODUCT_LABELS[product]}, "
                "percent of that mineral in the feed (Enter to skip)",
                low=0.0,
                high=100.0,
                allow_blank=True,
            )
        return {name: None if value is None else value / 100.0 for name, value in values.items()}

    def _warn_recovery_closure(self, recoveries, minerals, products) -> None:
        for mineral in minerals:
            values = []
            complete = True
            for product in products:
                value = recoveries.get(product, {}).get(mineral.name)
                if value is None:
                    complete = False
                    break
                values.append(value)
            if not complete:
                continue
            total = sum(values) * 100.0
            if abs(total - 100.0) > 3.0:
                self.ui.say(
                    f"Recoveries of {mineral.name} add up to {total:.1f}% across the products. "
                    "A complete set is usually near 100%. I will still use the numbers you entered."
                )

    def collect_size_splits(self, session: Session, products: tuple[str, ...]):
        self.ui.say("")
        if not self.ui.ask_yes(
            "Do you know how much of each size class reported to each product?"
        ):
            return None
        self.ui.say(
            "This is the mass split of each size class, with no assays. "
            "You can skip any class."
        )
        splits = []
        for size_class in session.feed.size_classes:
            self.ui.say("")
            self.ui.say(f"{size_class.label()} is {size_class.mass_fraction * 100:.2f}% of the feed.")
            if not self.ui.ask_yes("Do you have the mass split for this size class?"):
                splits.append({product: None for product in products})
                continue
            values: dict[str, float | None] = {}
            for index, product in enumerate(products):
                known = [value for value in values.values() if value is not None]
                last = index == len(products) - 1
                if last and len(known) == len(products) - 1:
                    remaining = max(0.0, 100.0 - sum(known))
                    value = self.ui.ask_percent(
                        f"Percent of this class to the {PRODUCT_LABELS[product]} "
                        f"(Enter for the remaining {remaining:.2f}%)",
                        low=0.0,
                        high=100.0,
                        allow_blank=True,
                    )
                    if value is None:
                        value = remaining
                else:
                    value = self.ui.ask_percent(
                        f"Percent of this class to the {PRODUCT_LABELS[product]} (Enter to skip)",
                        low=0.0,
                        high=100.0,
                        allow_blank=True,
                    )
                values[product] = value
            splits.append(
                {key: None if value is None else value / 100.0 for key, value in values.items()}
            )
        if not any(any(value is not None for value in row.values()) for row in splits):
            return None
        return splits

    def save_dialog(self) -> None:
        assert self.session is not None
        self.ui.say("")
        path = self.ui.ask("File name to save (for example my_spiral.json)")
        if not path:
            self.ui.say("Nothing was saved.")
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        destination = Path(path).expanduser()
        if destination.exists() and not self.ui.ask_yes(f"{destination} already exists. Overwrite it?"):
            self.ui.say("Nothing was saved.")
            return
        try:
            save_session(destination, self.session)
        except OSError as exc:
            self.ui.say(f"Could not save the session ({exc}).")
            return
        self.ui.say(f"Saved the feed, parameters, splitters, and any plant data to {destination}.")

    def load_dialog(self) -> Session | None:
        self.ui.say("")
        path = self.ui.ask("File name to load")
        if not path:
            self.ui.say("Nothing was loaded.")
            return None
        source = Path(path).expanduser()
        if not source.exists():
            self.ui.say(f"I could not find {source}.")
            return None
        try:
            session = load_session(source)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.ui.say(f"Could not load that session ({exc}).")
            return None
        self.ui.say(f"Loaded {source}.")
        return session


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Interactive spiral concentrator simulator based on King's "
            "generalized size-density partition function."
        )
    )
    parser.add_argument(
        "--example",
        action="store_true",
        help="Simulate the built-in heavy-mineral sands feed and exit.",
    )
    parser.add_argument(
        "--demo-fit",
        action="store_true",
        help="Calibrate against a synthetic survey and exit.",
    )
    parser.add_argument(
        "--session",
        metavar="FILE",
        help="Load a saved session, simulate it, and exit.",
    )
    return parser


def main(argv: list[str] | None = None, stdin=None, stdout=None) -> int:
    args = _parser().parse_args(argv)
    ui = UI(stdin, stdout)
    try:
        if args.session:
            session = load_session(args.session)
            ui.say(render(session, session.run()))
            return 0
        if args.example:
            session = heavy_mineral_sands()
            ui.say(render(session, session.run()))
            return 0
        if args.demo_fit:
            ui.say(
                "Fitting a synthetic heavy-mineral survey. "
                "The survey is generated from a known spiral so the fit can be checked."
            )
            session, truth, start, report = calibration_demo()
            ui.say(
                f"Synthetic truth: outer cut {truth.rho50_outer_ref:.3f} g/cm3, "
                f"inner cut {truth.rho50_inner_ref:.3f} g/cm3, "
                f"imperfection {truth.imperfection_ref:.3f}."
            )
            ui.say(render_fit(start, report, session.n_products))
            ui.say(render(session, session.run()))
            return 0
        return App(ui).run()
    except EOFError:
        ui.say("")
        ui.say("End of input. Leaving the simulator.")
        return 0
    except KeyboardInterrupt:
        ui.say("")
        ui.say("Stopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
