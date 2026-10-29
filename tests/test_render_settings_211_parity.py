"""RenderSettings parity with the Resolve 21.1 typed stub (issue #207).

The compound `render set_settings` whitelist held 31 of the 33 `RenderSettings`
fields the 21.1 `DaVinciResolveScript.pyi` declares, so valid `AudioFormat` and
`UseUniqueFilenames` inputs came back in `ignored_settings` and never reached
Resolve. These guard the whitelist against the stub itself, and the boolean
coercion that keeps `"false"` from being forwarded as a truthy string.

Stub contracts only: the live evidence (suffixed filenames, 48 kHz/24-bit WAVs)
was reported by @legionsound on Studio 21.1.0.14 and is recorded in api_truth.
"""
import pathlib
import re
import unittest
from unittest import mock

import src.server as s
import src.granular.project as gproject
from src.utils.api_truth import ACTION_SYMBOLS, API_TRUTH, traps_for
from src.utils.bool_params import recognised_bool

ROOT = pathlib.Path(__file__).resolve().parents[1]
STUB = ROOT / "docs" / "reference" / "DaVinciResolveScript.pyi"


def _stub_render_settings_fields():
    text = STUB.read_text(encoding="utf-8")
    m = re.search(r"^class RenderSettings\(TypedDict, total=False\):\n(.*?)(?=^class )",
                  text, re.S | re.M)
    assert m, "RenderSettings TypedDict not found in the bundled 21.1 stub"
    return re.findall(r"^\t(\w+):", m.group(1), re.M)


class WhitelistMatchesStub(unittest.TestCase):
    def test_every_typed_render_setting_is_whitelisted(self):
        fields = _stub_render_settings_fields()
        self.assertEqual(len(fields), 33)
        self.assertEqual(sorted(set(fields) - set(s._RENDER_SETTING_KEYS)), [])

    def test_the_two_missing_keys_are_present(self):
        self.assertIn("AudioFormat", s._RENDER_SETTING_KEYS)
        self.assertIn("UseUniqueFilenames", s._RENDER_SETTING_KEYS)


class RecognisedBool(unittest.TestCase):
    def test_values(self):
        for value, expected in ((True, True), (False, False), ("false", False),
                                ("FALSE", False), (" true ", True), ("0", False),
                                ("yes", True), (1, True), (0, False)):
            with self.subTest(value=value):
                self.assertIs(recognised_bool(value), expected)
        for value in ("maybe", "", None, 2, 1.0, [], "high"):
            with self.subTest(value=value):
                self.assertIsNone(recognised_bool(value))


class CompoundSetSettings(unittest.TestCase):
    def _call(self, settings):
        captured = {}
        proj = mock.Mock()
        proj.SetRenderSettings.side_effect = lambda s_: captured.update(s=s_) or True
        with mock.patch.object(s, "_check", return_value=(mock.Mock(), proj, None)):
            out = s.render("set_settings", {"settings": settings})
        return out, captured, proj

    def test_new_keys_are_forwarded_not_ignored(self):
        out, captured, _ = self._call({"AudioFormat": "wav", "UseUniqueFilenames": True,
                                       "ExportVideo": False})
        self.assertEqual(captured["s"], {"AudioFormat": "wav", "UseUniqueFilenames": True,
                                         "ExportVideo": False})
        self.assertNotIn("ignored_settings", out)
        self.assertTrue(out["success"])

    def test_string_false_is_sent_as_false(self):
        _, captured, _ = self._call({"UseUniqueFilenames": "false"})
        self.assertIs(captured["s"]["UseUniqueFilenames"], False)

    def test_string_true_is_sent_as_true(self):
        _, captured, _ = self._call({"UseUniqueFilenames": "true"})
        self.assertIs(captured["s"]["UseUniqueFilenames"], True)

    def test_unrecognised_value_is_refused_before_the_call(self):
        out, _, proj = self._call({"UseUniqueFilenames": "maybe"})
        proj.SetRenderSettings.assert_not_called()
        self.assertFalse(out.get("success", False))
        self.assertIn("UseUniqueFilenames", str(out))

    def test_audio_format_with_video_export_warns(self):
        out, _, _ = self._call({"AudioFormat": "wav", "ExportVideo": True})
        self.assertTrue(any("AudioFormat" in w for w in out.get("warnings", [])))


class ValidatorPaths(unittest.TestCase):
    def test_validate_coerces_and_reports(self):
        ok, err = s._validate_render_settings_payload({"UseUniqueFilenames": "false"})
        self.assertIsNone(err)
        self.assertTrue(ok["valid"], ok)
        self.assertIs(ok["settings"]["UseUniqueFilenames"], False)
        self.assertEqual(ok["unknown_keys"], [])

    def test_validate_rejects_unrecognised_bool(self):
        bad, _ = s._validate_render_settings_payload({"UseUniqueFilenames": "maybe"})
        self.assertFalse(bad["valid"])
        self.assertTrue(any("UseUniqueFilenames" in e for e in bad["errors"]))

    def test_validate_rejects_empty_audio_format(self):
        bad, _ = s._validate_render_settings_payload({"AudioFormat": ""})
        self.assertFalse(bad["valid"])

    def test_safe_set_forwards_the_coerced_value(self):
        captured = {}
        proj = mock.Mock()
        proj.SetRenderSettings.side_effect = lambda s_: captured.update(s=s_) or True
        proj.GetRenderSettings.return_value = {}
        with mock.patch.object(s, "_render_settings_snapshot", return_value={}):
            s._safe_set_render_settings(proj, {"settings": {"UseUniqueFilenames": "false"}})
        self.assertIs(captured["s"]["UseUniqueFilenames"], False)


class GranularSetRenderSettings(unittest.TestCase):
    def setUp(self):
        self.proj = mock.Mock()
        self.proj.SetRenderSettings.return_value = True
        resolve = mock.Mock()
        resolve.GetProjectManager.return_value.GetCurrentProject.return_value = self.proj
        self._orig = gproject.get_resolve
        gproject.get_resolve = lambda: resolve

    def tearDown(self):
        gproject.get_resolve = self._orig

    def test_string_false_is_coerced(self):
        out = gproject.set_render_settings({"UseUniqueFilenames": "false", "AudioFormat": "wav"})
        self.assertTrue(out["success"])
        self.proj.SetRenderSettings.assert_called_once_with(
            {"UseUniqueFilenames": False, "AudioFormat": "wav"})

    def test_unrecognised_value_is_refused(self):
        out = gproject.set_render_settings({"UseUniqueFilenames": "maybe"})
        self.assertIn("error", out)
        self.proj.SetRenderSettings.assert_not_called()

    def test_docstring_names_both_keys(self):
        doc = gproject.set_render_settings.__doc__ or ""
        self.assertIn("UseUniqueFilenames", doc)
        self.assertIn("AudioFormat", doc)


class StartRenderingLedger(unittest.TestCase):
    def test_entry_is_reported_and_stamped(self):
        entry = next(e for e in API_TRUTH if e["symbol"] == "Project.StartRendering")
        self.assertIn("reported", entry["tags"])
        self.assertEqual(entry["verified_on"], "DaVinci Resolve Studio 21.1.0.14")
        self.assertEqual(entry["issue"], 207)
        self.assertNotIn("destroys_prior_work", entry)
        self.assertIn("auto-retry", entry["recommended"])

    def test_render_start_pushes_the_fact(self):
        self.assertEqual(ACTION_SYMBOLS[("render", "start")], ["Project.StartRendering"])
        self.assertEqual([t["symbol"] for t in traps_for("render", "start")],
                         ["Project.StartRendering"])


if __name__ == "__main__":
    unittest.main()
