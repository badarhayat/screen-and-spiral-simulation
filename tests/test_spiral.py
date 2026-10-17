"""Tests for the partition model, the balance, calibration, and the dialogue."""

from __future__ import annotations

import io
import unittest
from pathlib import Path

from spiral_sim.cli import main
from spiral_sim.defaults import defaults_from_minerals
from spiral_sim.examples import calibration_demo, heavy_mineral_sands, survey_from_model
from spiral_sim.feed import Feed, Mineral, SizeClass
from spiral_sim.model import (
    OperatingConditions,
    PartitionParameters,
    SplitterSettings,
    corrected_partition,
    cut_density_at_size,
    bypass_outer_at_size,
    product_recoveries,
)
from spiral_sim.session import load_session, save_session
from spiral_sim.simulate import simulate
from spiral_sim.targets import match_mass_pulls
from spiral_sim.calibrate import PlantData, fit_parameters


def quartz_ilmenite_feed() -> Feed:
    grades = [0.70, 0.30]
    return Feed(
        size_classes=[
            SizeClass(0.150, 0.300, 0.50),
            SizeClass(0.075, 0.150, 0.30),
            SizeClass(0.000, 0.075, 0.20),
        ],
        minerals=[Mineral("Quartz", 2.65), Mineral("Ilmenite", 4.70)],
        composition=[list(grades) for _index in range(3)],
    )


class PartitionTests(unittest.TestCase):
    def test_error_function_passes_through_50_and_75(self):
        at_cut = corrected_partition(3.0, 3.0, 0.20, "erf")
        above = corrected_partition(3.20, 3.0, 0.20, "erf")
        below = corrected_partition(2.80, 3.0, 0.20, "erf")
        self.assertAlmostEqual(at_cut, 0.5, places=9)
        self.assertAlmostEqual(above, 0.75, places=6)
        self.assertAlmostEqual(below, 0.25, places=6)

    def test_logistic_passes_through_50_and_75(self):
        at_cut = corrected_partition(3.0, 3.0, 0.20, "logistic")
        above = corrected_partition(3.20, 3.0, 0.20, "logistic")
        self.assertAlmostEqual(at_cut, 0.5, places=9)
        self.assertAlmostEqual(above, 0.75, places=6)

    def test_streams_sum_to_one_and_rise_with_density(self):
        params = PartitionParameters()
        settings = SplitterSettings()
        previous = -1.0
        for density in (1.5, 2.2, 2.65, 3.2, 3.8, 4.5, 6.0):
            recoveries = product_recoveries(0.15, density, params, settings, 30.0, 3, True)
            self.assertAlmostEqual(sum(recoveries.values()), 1.0, places=9)
            self.assertTrue(all(value >= -1e-12 for value in recoveries.values()))
            self.assertGreaterEqual(recoveries["concentrate"], previous - 1e-9)
            previous = recoveries["concentrate"]

    def test_fine_particles_are_harder_to_send_inward(self):
        params = PartitionParameters()
        self.assertGreater(
            cut_density_at_size(0.045, params.rho50_outer_ref, params),
            cut_density_at_size(0.400, params.rho50_outer_ref, params),
        )
        self.assertGreater(
            bypass_outer_at_size(0.020, params),
            bypass_outer_at_size(0.500, params),
        )


class SimulationTests(unittest.TestCase):
    def test_balance_closes_and_heavies_report_to_concentrate(self):
        feed = quartz_ilmenite_feed()
        params, _note = defaults_from_minerals(feed.minerals)
        result = simulate(
            feed,
            params,
            SplitterSettings(),
            OperatingConditions(feed_rate_tph=10, pct_solids=30),
            n_products=3,
            dense_is_concentrate=True,
        )
        self.assertLess(result.closure_error(), 1e-9)
        self.assertAlmostEqual(sum(result.total.values()), 1.0, places=9)
        quartz = result.recovery_of("concentrate", 0)
        ilmenite = result.recovery_of("concentrate", 1)
        self.assertIsNotNone(quartz)
        self.assertIsNotNone(ilmenite)
        assert quartz is not None and ilmenite is not None
        self.assertGreater(ilmenite, 0.70)
        self.assertLess(quartz, 0.20)
        self.assertGreater(ilmenite, quartz)

    def test_two_products_have_no_middlings(self):
        feed = quartz_ilmenite_feed()
        params, _note = defaults_from_minerals(feed.minerals)
        result = simulate(
            feed, params, SplitterSettings(), OperatingConditions(pct_solids=30), 2, True
        )
        self.assertEqual(result.products, ("concentrate", "tails"))
        self.assertAlmostEqual(
            result.yield_of("concentrate") + result.yield_of("tails"), 1.0, places=9
        )

    def test_single_mineral_grades_stay_at_100(self):
        feed = Feed(
            size_classes=[SizeClass(0.1, 0.3, 1.0)],
            minerals=[Mineral("Quartz", 2.65)],
            composition=[[1.0]],
        )
        params, _note = defaults_from_minerals(feed.minerals)
        result = simulate(
            feed, params, SplitterSettings(), OperatingConditions(pct_solids=30), 3, True
        )
        self.assertLess(result.closure_error(), 1e-9)
        for product in result.products:
            if result.yield_of(product) > 1e-8:
                self.assertAlmostEqual(result.grade_of(product, 0), 1.0, places=9)

    def test_coal_mode_sends_the_light_mineral_to_concentrate(self):
        feed = Feed(
            size_classes=[SizeClass(0.5, 2.0, 0.6), SizeClass(0.15, 0.5, 0.4)],
            minerals=[Mineral("Coal", 1.40), Mineral("Shale", 2.20)],
            composition=[[0.75, 0.25], [0.75, 0.25]],
        )
        params, _note = defaults_from_minerals(feed.minerals)
        result = simulate(
            feed, params, SplitterSettings(), OperatingConditions(pct_solids=30), 3, False
        )
        coal = result.recovery_of("concentrate", 0)
        shale = result.recovery_of("concentrate", 1)
        assert coal is not None and shale is not None
        self.assertGreater(coal, shale)
        self.assertLess(result.closure_error(), 1e-9)

    def test_opening_the_splitter_increases_dense_concentrate_yield(self):
        feed = quartz_ilmenite_feed()
        params, _note = defaults_from_minerals(feed.minerals)
        operating = OperatingConditions(pct_solids=30)
        tight = simulate(feed, params, SplitterSettings(0, 0), operating, 3, True)
        wide = simulate(feed, params, SplitterSettings(100, 100), operating, 3, True)
        self.assertGreater(wide.yield_of("concentrate"), tight.yield_of("concentrate"))

    def test_target_mass_pull_is_met(self):
        feed = quartz_ilmenite_feed()
        params, _note = defaults_from_minerals(feed.minerals)
        settings, achieved, _notes = match_mass_pulls(
            feed,
            params,
            SplitterSettings(),
            OperatingConditions(pct_solids=30),
            n_products=3,
            dense_is_concentrate=True,
            concentrate_yield=0.22,
            middlings_yield=0.18,
        )
        self.assertAlmostEqual(achieved["concentrate"], 0.22, delta=0.015)
        self.assertAlmostEqual(achieved["middlings"], 0.18, delta=0.015)
        self.assertGreater(settings.outer_opening, -100)
        self.assertLess(settings.outer_opening, 250)


class CalibrationTests(unittest.TestCase):
    def test_fit_reproduces_a_synthetic_survey(self):
        feed = quartz_ilmenite_feed()
        truth = PartitionParameters(
            rho50_outer_ref=3.15,
            rho50_inner_ref=3.95,
            imperfection_ref=0.13,
            bypass_outer_coarse=0.03,
            bypass_outer_slime=0.22,
        )
        operating = OperatingConditions(pct_solids=32.0)
        settings = SplitterSettings(60.0, 40.0)
        plant = survey_from_model(feed, truth, operating, settings, 3, True)
        start, _note = defaults_from_minerals(feed.minerals)
        report = fit_parameters(feed, start, operating, plant, 3, True)
        self.assertIsNotNone(report.yield_mae_pp)
        self.assertIsNotNone(report.grade_mae_pp)
        assert report.yield_mae_pp is not None and report.grade_mae_pp is not None
        self.assertLess(report.yield_mae_pp, 1.0)
        self.assertLess(report.grade_mae_pp, 1.5)
        self.assertGreater(report.parameters.rho50_inner_ref, report.parameters.rho50_outer_ref)

    def test_size_splits_are_fitted(self):
        feed = quartz_ilmenite_feed()
        truth = PartitionParameters(rho50_outer_ref=3.20, rho50_inner_ref=4.00, n_cut=0.35)
        operating = OperatingConditions(pct_solids=30.0)
        plant = survey_from_model(
            feed, truth, operating, SplitterSettings(), 3, True, include_size_splits=True
        )
        start, _note = defaults_from_minerals(feed.minerals)
        start.n_cut = 0.05
        report = fit_parameters(feed, start, operating, plant, 3, True)
        self.assertIsNotNone(report.size_split_mae_pp)
        assert report.size_split_mae_pp is not None
        self.assertLess(report.size_split_mae_pp, 2.0)

    def test_empty_survey_is_rejected(self):
        feed = quartz_ilmenite_feed()
        params, _note = defaults_from_minerals(feed.minerals)
        with self.assertRaises(ValueError):
            fit_parameters(
                feed,
                params,
                OperatingConditions(pct_solids=30),
                PlantData(),
                3,
                True,
            )

    def test_builtin_demo_fit_tracks_the_synthetic_survey(self):
        _session, _truth, _start, report = calibration_demo()
        self.assertIsNotNone(report.yield_mae_pp)
        self.assertIsNotNone(report.grade_mae_pp)
        assert report.yield_mae_pp is not None and report.grade_mae_pp is not None
        self.assertLess(report.yield_mae_pp, 1.0)
        self.assertLess(report.grade_mae_pp, 1.5)


class SessionTests(unittest.TestCase):
    def test_save_and_load_repeat_the_same_balance(self):
        session = heavy_mineral_sands()
        before = session.run()
        path = Path(self.id().replace(".", "_") + ".json")
        try:
            # Keep the temp file in the system temp via the test's cwd alternative.
            import tempfile

            with tempfile.TemporaryDirectory() as folder:
                destination = Path(folder) / "session.json"
                save_session(destination, session)
                loaded = load_session(destination)
                after = loaded.run()
                self.assertEqual(before.products, after.products)
                for product in before.products:
                    self.assertAlmostEqual(before.yield_of(product), after.yield_of(product), places=9)
                    for index in range(len(before.mineral_names)):
                        self.assertAlmostEqual(
                            before.grade_of(product, index),
                            after.grade_of(product, index),
                            places=9,
                        )
                self.assertEqual(loaded.parameter_source, "density-defaults")
        finally:
            if path.exists():
                path.unlink()


class DialogueTests(unittest.TestCase):
    def _run(self, lines: list[str], argv: list[str] | None = None) -> tuple[int, str]:
        stdout = io.StringIO()
        stdin = io.StringIO("\n".join(lines) + ("\n" if lines else ""))
        code = main([] if argv is None else argv, stdin=stdin, stdout=stdout)
        return code, stdout.getvalue()

    def test_example_dialogue_prints_a_closed_balance(self):
        code, output = self._run(["nope", "3", "8"])
        self.assertEqual(code, 0)
        self.assertIn("Balance closure: closed.", output)
        self.assertIn("Rutile", output)
        self.assertIn("Results improve significantly", output)
        self.assertIn("Goodbye.", output)

    def test_new_feed_then_splitter_change_and_save(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            destination = str(Path(folder) / "run.json")
            lines = [
                "1",
                "2",
                "0.1",
                "0.3",
                "50",
                "0",
                "0.1",
                "50",
                "y",
                "2",
                "Quartz",
                "2.65",
                "Ilmenite",
                "4.7",
                "y",
                "2",
                "70",
                "",
                "",
                "",
                "y",
                "1",
                "2",
                "2",
                "1",
                "1",
                "80",
                "5",
                destination,
                "8",
            ]
            code, output = self._run(lines)
            self.assertEqual(code, 0, output)
            self.assertIn("Balance closure: closed.", output)
            self.assertIn("Outer splitter opening: 80.0", output)
            self.assertIn("approximate", output.lower())
            self.assertTrue(Path(destination).exists(), output)
            loaded_out = io.StringIO()
            code = main(["--session", destination], stdout=loaded_out)
            self.assertEqual(code, 0)
            self.assertIn("Balance closure: closed.", loaded_out.getvalue())
            self.assertIn("Ilmenite", loaded_out.getvalue())

    def test_end_of_input_exits_cleanly(self):
        code, output = self._run([])
        self.assertEqual(code, 0)
        self.assertIn("End of input", output)

    def test_example_flag(self):
        code, output = self._run([], argv=["--example"])
        self.assertEqual(code, 0)
        self.assertIn("Zircon", output)
        self.assertIn("Balance closure: closed.", output)

    def test_target_yield_and_manual_cut_from_the_example(self):
        code, output = self._run(["3", "2", "25", "15", "8"])
        self.assertEqual(code, 0, output)
        self.assertIn("Mass yield %  100.00        25.04      14.87  60.08", output)

        code, output = self._run(["3", "3", "1", "3.0", "d", "8"])
        self.assertEqual(code, 0, output)
        self.assertIn("Outer cut density at 0.150 mm, opening 50: 3.000 g/cm3", output)
        self.assertIn("include values edited in this session", output)

    def test_calibration_dialogue_accepts_a_survey(self):
        lines = [
            "3",
            "4",
            "12",
            "20",
            "",
            "1",
            "10",
            "50",
            "40",
            "70",
            "20",
            "10",
            "95",
            "3",
            "2",
            "n",
            "",
            "",
            "1",
            "8",
        ]
        code, output = self._run(lines)
        self.assertEqual(code, 0, output)
        self.assertIn("Calibration result", output)
        self.assertIn("were fitted to the plant data", output)
        self.assertIn("Balance closure: closed.", output)


if __name__ == "__main__":
    unittest.main()
