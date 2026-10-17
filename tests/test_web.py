"""Tests for the localhost spiral simulator."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.request

from spiral_sim.web import example_payload, make_server, rerun_session, run_wizard


def quartz_ilmenite() -> dict:
    return {
        "sizes": [
            {"d_low": 0.15, "d_high": 0.30, "mass_percent": 60},
            {"d_low": 0, "d_high": 0.15, "mass_percent": 40},
        ],
        "minerals": [
            {"name": "Quartz", "density": 2.65},
            {"name": "Ilmenite", "density": 4.70},
        ],
        "composition_mode": "overall",
        "overall_grades": {"Quartz": 75, "Ilmenite": ""},
        "n_products": 3,
        "dense_is_concentrate": True,
        "pct_solids": 30,
        "preset": "density",
    }


class WizardTests(unittest.TestCase):
    def test_wizard_upgrades_the_heavy_mineral(self):
        result = run_wizard(quartz_ilmenite())
        self.assertTrue(result["ok"])
        self.assertLess(result["closure"], 1e-9)
        self.assertAlmostEqual(sum(result["yields"].values()), 100.0, delta=0.05)
        quartz = result["minerals"][0]["recoveries"]["concentrate"]
        ilmenite = result["minerals"][1]["recoveries"]["concentrate"]
        self.assertGreater(ilmenite, quartz)
        self.assertIn("approximate", result["source_summary"].lower())
        self.assertIn("improve significantly", result["calibration_note"])

    def test_single_mineral_is_accepted(self):
        payload = quartz_ilmenite()
        payload["minerals"] = [{"name": "Quartz", "density": 2.65}]
        result = run_wizard(payload)
        self.assertAlmostEqual(result["minerals"][0]["grades"]["concentrate"], 100.0, delta=0.01)

    def test_calibration_and_a_later_splitter_change(self):
        payload = quartz_ilmenite()
        payload["plant"] = {
            "yields": {"concentrate": 25, "middlings": 20, "tails": ""},
            "grades": {
                "concentrate": {"Quartz": 20, "Ilmenite": 80},
                "middlings": {"Quartz": 60, "Ilmenite": 40},
                "tails": {"Quartz": 95, "Ilmenite": 5},
            },
            "splitter_outer": 50,
            "splitter_inner": 50,
        }
        fitted = run_wizard(payload)
        self.assertEqual(fitted["parameter_source"], "fitted")
        self.assertTrue(fitted["fit_comparisons"])
        tight = rerun_session(
            {"session": fitted["session"], "splitters": {"outer_opening": 0, "inner_opening": 0}}
        )
        wide = rerun_session(
            {"session": fitted["session"], "splitters": {"outer_opening": 100, "inner_opening": 100}}
        )
        self.assertGreater(wide["yields"]["concentrate"], tight["yields"]["concentrate"])

    def test_target_mass_pull(self):
        payload = quartz_ilmenite()
        payload["targets"] = {"concentrate": 20, "middlings": 15}
        result = run_wizard(payload)
        self.assertAlmostEqual(result["yields"]["concentrate"], 20.0, delta=1.5)
        self.assertAlmostEqual(result["yields"]["middlings"], 15.0, delta=1.5)

    def test_bad_size_class_is_rejected(self):
        payload = quartz_ilmenite()
        payload["sizes"][0]["d_high"] = 0.10
        with self.assertRaises(ValueError):
            run_wizard(payload)


class ServerTests(unittest.TestCase):
    def test_pages_and_example(self):
        server = make_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as response:
                page = response.read().decode("utf-8")
            self.assertIn("Spiral concentrator", page)
            self.assertIn("one step at a time", page)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/example") as response:
                example = json.load(response)
            self.assertTrue(example["ok"])
            self.assertIn("Rutile", [mineral["name"] for mineral in example["minerals"]])
            self.assertAlmostEqual(sum(example["yields"].values()), 100.0, delta=0.05)
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/simulate",
                data=json.dumps(quartz_ilmenite()).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request) as response:
                simulated = json.load(response)
            self.assertTrue(simulated["ok"])
            self.assertLess(simulated["closure"], 1e-9)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
