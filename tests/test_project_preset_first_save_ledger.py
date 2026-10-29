"""Ledger guard: project-settings presets before the first SaveProject (issue #207).

Reported by @legionsound on Studio 21.1.0.14: mutating project-settings presets
on a never-saved project left it unloadable while every call returned True. The
entry has to reach the Blackmagic-facing report and name the real 21.1 method
names, because the preset wrappers that will cite it have not shipped yet.

`destroys_prior_work` is deliberately absent: that flag is symbol-level and
turns into a hard refusal, it requires an action mapping and a live probe that
do not exist yet, and it would also refuse the same calls on a saved project,
where they were measured safe. The warning lives in the tags and the text until
the preset controls land with their own first-save guard.
"""
import pathlib
import re
import unittest

from src.utils.api_truth import API_TRUTH, lookup_api_truth, submittable_limitations

SYMBOL = "Project settings presets mutated before the project's first SaveProject"
STUB = pathlib.Path(__file__).resolve().parents[1] / "docs" / "reference" / "DaVinciResolveScript.pyi"


class PresetFirstSaveEntry(unittest.TestCase):
    def setUp(self):
        self.entry = next(e for e in API_TRUTH if e["symbol"] == SYMBOL)

    def test_reported_bug_on_211(self):
        self.assertEqual(self.entry["submit"], "bug")
        self.assertEqual(self.entry["issue"], 207)
        self.assertEqual(self.entry["verified_on"], "DaVinci Resolve Studio 21.1.0.14")
        for tag in ("reported", "destructive", "unrecoverable", "project"):
            self.assertIn(tag, self.entry["tags"])
        self.assertIn(self.entry, submittable_limitations()["bug"])

    def test_not_a_refusing_trap_yet(self):
        self.assertNotIn("destroys_prior_work", self.entry)

    def test_names_real_21_1_methods(self):
        stub = STUB.read_text(encoding="utf-8")
        for method in re.findall(r"\b([A-Z]\w*ProjectSettings\w*Preset)\b", self.entry["signature"]):
            with self.subTest(method=method):
                self.assertIn(f"def {method}(", stub)

    def test_findable_by_method_name(self):
        for query in ("UpdateProjectSettingsPreset", "Sm2MultiSysData", "SaveProject"):
            with self.subTest(query=query):
                self.assertIn(self.entry, lookup_api_truth(query))


if __name__ == "__main__":
    unittest.main()
