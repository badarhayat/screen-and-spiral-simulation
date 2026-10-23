"""Tests for the localhost spiral simulator."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from pathlib import Path

from spiral_sim.cli import _parser, should_serve_web
from spiral_sim.examples import heavy_mineral_sands
from spiral_sim.report import render
from spiral_sim.session import session_from_dict
from spiral_sim.web import example_payload, make_server, rerun_session, run_wizard, static_page_path
from spiral_sim.workspace_store import (
    WorkspaceError,
    clear_workspace,
    discard_to_checkpoint,
    draft_is_meaningful,
    import_workspace,
    read_workspace,
    save_workspace,
)


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

    def test_entered_feed_grades_stay_after_simulation(self):
        """A complete grade row below 100% must not be shown as the feed grade.

        The partition still normalizes the assayed minerals so each size class
        sums to 1. Those proportions are stored and labeled separately.
        """
        gold = 0.0001
        iron = 0.07
        payload = quartz_ilmenite()
        payload["minerals"] = [
            {"name": "Au", "density": 19.3},
            {"name": "Fe2O3", "density": 5.26},
        ]
        payload["overall_grades"] = {"Au": gold, "Fe2O3": iron}
        snapshot = copy.deepcopy(payload)
        result = run_wizard(payload)
        self.assertEqual(payload, snapshot)

        minerals = {item["name"]: item for item in result["minerals"]}
        self.assertAlmostEqual(minerals["Au"]["feed_grade"], gold, places=8)
        self.assertAlmostEqual(minerals["Fe2O3"]["feed_grade"], iron, places=8)
        assayed = gold + iron
        self.assertAlmostEqual(minerals["Au"]["normalized_proportion"], 100.0 * gold / assayed, places=4)
        self.assertAlmostEqual(minerals["Fe2O3"]["normalized_proportion"], 100.0 * iron / assayed, places=4)
        self.assertAlmostEqual(
            sum(item["normalized_proportion"] for item in result["minerals"]), 100.0, places=4
        )
        self.assertNotAlmostEqual(minerals["Au"]["feed_grade"], minerals["Au"]["normalized_proportion"], places=3)
        self.assertAlmostEqual(sum(result["yields"].values()), 100.0, delta=0.05)
        self.assertLess(result["closure"], 1e-6)
        self.assertAlmostEqual(result["unassigned"]["feed"], 100.0 - assayed, places=4)
        assigned = sum(item["balance"]["feed"] for item in result["minerals"])
        self.assertAlmostEqual(assigned + result["unassigned"]["feed"], 100.0, places=6)
        for product, yield_percent in result["yields"].items():
            mineral_mass = sum(item["balance"][product] for item in result["minerals"])
            self.assertAlmostEqual(mineral_mass + result["unassigned"][product], yield_percent, places=5)
        for mineral in result["minerals"]:
            for product, recovery in mineral["recoveries"].items():
                from_recovery = (recovery / 100.0) * mineral["feed_grade"]
                from_grade = mineral["grades"][product] * result["yields"][product] / 100.0
                self.assertAlmostEqual(from_recovery, from_grade, places=5)

        entered = result["session"]["feed"]["entered_composition_percent"]
        self.assertEqual(len(entered), len(result["session"]["feed"]["composition"]))
        for row in entered:
            self.assertAlmostEqual(row[0], gold, places=8)
            self.assertAlmostEqual(row[1], iron, places=8)
        for row in result["session"]["feed"]["composition"]:
            self.assertAlmostEqual(sum(row), 1.0, places=6)
            self.assertAlmostEqual(row[0], gold / assayed, places=6)
        self.assertTrue(
            any("entered grades are unchanged" in note for note in result["warnings"])
        )

        again = rerun_session(
            {"session": result["session"], "splitters": {"outer_opening": 60, "inner_opening": 40}}
        )
        again_minerals = {item["name"]: item for item in again["minerals"]}
        self.assertAlmostEqual(again_minerals["Au"]["feed_grade"], gold, places=8)
        self.assertAlmostEqual(again_minerals["Fe2O3"]["feed_grade"], iron, places=8)
        self.assertAlmostEqual(
            again["session"]["feed"]["entered_composition_percent"][0][0], gold, places=8
        )

        restored = session_from_dict(result["session"])
        self.assertAlmostEqual(restored.feed.entered_overall_percent(0), gold, places=8)
        self.assertAlmostEqual(restored.feed.entered_overall_percent(1), iron, places=8)
        report = render(restored, restored.run())
        self.assertIn("0.0001% entered feed grade", report)
        self.assertIn("0.07% entered feed grade", report)
        self.assertIn("normalized proportion of the assayed minerals", report)
        self.assertNotIn("0.14% of feed", report)

        legacy = json.loads(json.dumps(result["session"]))
        del legacy["feed"]["entered_composition_percent"]
        old = session_from_dict(legacy)
        self.assertIsNone(old.feed.entered_composition_percent)
        self.assertFalse(old.feed.validate())

    def test_remainder_grade_is_not_treated_as_a_rescaled_assay(self):
        result = run_wizard(quartz_ilmenite())
        self.assertAlmostEqual(result["minerals"][0]["feed_grade"], 75.0, places=4)
        self.assertAlmostEqual(result["minerals"][1]["feed_grade"], 25.0, places=4)
        self.assertNotIn("normalized_proportion", result["minerals"][0])
        self.assertNotIn("normalized_proportion", result["minerals"][1])
        self.assertIsNone(result["unassigned"])
        page = static_page_path().read_text(encoding="utf-8")
        self.assertIn("Normalized proportion of the assayed minerals", page)
        self.assertIn("not the entered feed grades", page)
        self.assertIn("Unassigned solids", page)
        self.assertIn("entered_composition_percent", page)


class LaunchTests(unittest.TestCase):
    def test_web_flag_and_frozen_double_click_open_the_browser(self):
        self.assertTrue(should_serve_web(_parser().parse_args(["--web"]), frozen=False))
        self.assertTrue(should_serve_web(_parser().parse_args([]), frozen=True))
        self.assertFalse(should_serve_web(_parser().parse_args([]), frozen=False))
        self.assertFalse(should_serve_web(_parser().parse_args(["--example"]), frozen=True))
        self.assertFalse(should_serve_web(_parser().parse_args(["--cli"]), frozen=True))
        self.assertFalse(should_serve_web(_parser().parse_args(["--session", "feed.json"]), frozen=True))

    def test_browser_page_and_windows_icon_are_present(self):
        page = static_page_path()
        self.assertTrue(page.is_file())
        self.assertIn("Spiral concentrator", page.read_text(encoding="utf-8"))
        icon = Path(__file__).resolve().parents[1] / "packaging" / "spiral.ico"
        data = icon.read_bytes()
        self.assertEqual(data[:4], b"\x00\x00\x01\x00")
        self.assertGreaterEqual(int.from_bytes(data[4:6], "little"), 4)

    def test_results_back_button_keeps_the_page_in_place(self):
        page = static_page_path().read_text(encoding="utf-8")
        self.assertIn("← Back", page)
        self.assertIn("Decimal precision", page)
        self.assertIn("Saving…", page)
        self.assertIn("Save failed", page)
        self.assertIn("Discard changes", page)
        self.assertIn("Discard changes since the last simulation?", page)
        self.assertIn("Reset simulation", page)
        self.assertIn("Reset the simulation?", page)
        self.assertIn("New simulation", page)
        self.assertIn("The feed, settings, and results on this page will be cleared.", page)
        self.assertIn("popstate", page)
        self.assertIn("history.pushState", page)
        self.assertNotIn('location.href = "http', page)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("SPIRAL_WORKSPACE_DIR")
        os.environ["SPIRAL_WORKSPACE_DIR"] = self.directory.name

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SPIRAL_WORKSPACE_DIR", None)
        else:
            os.environ["SPIRAL_WORKSPACE_DIR"] = self.previous
        self.directory.cleanup()

    def draft(self, name="Ilmenite", precision=4, step="minerals"):
        return {
            "step": step,
            "precision": precision,
            "state": {"minerals": [{"name": name, "density": "4.7"}]},
            "result": None,
        }

    def test_save_survives_a_new_read(self):
        save_workspace(self.draft(), commit=True)
        loaded = read_workspace()
        self.assertEqual(loaded["draft"]["state"]["minerals"][0]["name"], "Ilmenite")
        self.assertEqual(loaded["checkpoint"]["state"]["minerals"][0]["name"], "Ilmenite")

    def test_empty_page_does_not_replace_a_saved_session(self):
        save_workspace(self.draft())
        with self.assertRaises(WorkspaceError):
            save_workspace({"step": "welcome", "precision": 4, "state": {"sizes": [{"d_low": "", "d_high": "", "mass_percent": ""}]}})
        self.assertEqual(read_workspace()["draft"]["state"]["minerals"][0]["name"], "Ilmenite")

    def test_damaged_file_is_left_in_place(self):
        path = os.path.join(self.directory.name, "workspace.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(WorkspaceError):
            read_workspace()
        with self.assertRaises(WorkspaceError):
            save_workspace(self.draft())
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "{not json")

    def test_discard_returns_to_the_checkpoint_and_reset_deletes_it(self):
        save_workspace(self.draft("Quartz"), commit=True)
        save_workspace(self.draft("Ilmenite"))
        restored = discard_to_checkpoint()
        self.assertEqual(restored["draft"]["state"]["minerals"][0]["name"], "Quartz")
        clear_workspace()
        self.assertIsNone(read_workspace())

    def test_display_precision_does_not_change_the_balance(self):
        session = heavy_mineral_sands()
        first = session.run()
        second = session.run()
        for product in first.products:
            self.assertEqual(first.yield_of(product), second.yield_of(product))
        packed = example_payload()
        concentrate = packed["yields"]["concentrate"]
        self.assertGreater(abs(concentrate - round(concentrate, 2)), 1e-6)
        self.assertNotEqual(round(concentrate, 4), round(concentrate, 5))
        self.assertFalse(draft_is_meaningful({"step": "welcome", "precision": 5, "state": {}}))
        with self.assertRaises(WorkspaceError):
            import_workspace({"version": 1})
        self.assertIsNone(read_workspace())
        save_workspace(self.draft())
        with self.assertRaises(WorkspaceError):
            import_workspace({"draft": {"step": "minerals"}})
        self.assertEqual(read_workspace()["draft"]["state"]["minerals"][0]["name"], "Ilmenite")

    def test_http_workspace_round_trip(self):
        server = make_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/workspace",
                data=json.dumps({"action": "save", "commit": True, "draft": self.draft(precision=5)}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request) as response:
                saved = json.load(response)
            self.assertTrue(saved["ok"])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/workspace") as response:
                loaded = json.load(response)
            self.assertEqual(loaded["workspace"]["draft"]["precision"], 5)
            empty = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/workspace",
                data=json.dumps({"action": "save", "draft": {"step": "welcome", "precision": 4, "state": {}}}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(empty) as response:
                rejected = json.load(response)
            self.assertFalse(rejected["ok"])
            self.assertIn("not saved", rejected["error"])
            damaged = os.path.join(self.directory.name, "workspace.json")
            with open(damaged, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/workspace",
                        data=b"{not json",
                        headers={"Content-Type": "application/json"},
                    )
                )
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/workspace") as response:
                unreadable = json.load(response)
            self.assertFalse(unreadable["ok"])
            refused = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/workspace",
                data=json.dumps({"action": "save", "draft": self.draft()}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(refused) as response:
                blocked = json.load(response)
            self.assertFalse(blocked["ok"])
            with open(damaged, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "{not json")
        finally:
            server.shutdown()
            server.server_close()


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
