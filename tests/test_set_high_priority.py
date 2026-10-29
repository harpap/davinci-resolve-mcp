"""SetHighPriority across the 19.x / 21.1 signature change (issue #207).

Resolve 21.1 types `SetHighPriority(highPriority: bool) -> bool`. Reported by
@legionsound on Studio 21.1.0.14: (False) -> True, (True) -> True, () -> False
(a no-op), (1) -> False, ("high") -> False. On 19.1.3.7 the method takes no
argument and cannot be lowered again within the session. No build has a getter.

Stub contracts only — nothing here touches a live Resolve.
"""
import unittest
from unittest import mock

import src.server as s
import src.granular.resolve_control as rc
from src.utils import destructive_hook
from src.utils.api_truth import ACTION_SYMBOLS, API_TRUTH, traps_for
from src.utils.app_control import set_high_priority
from src.utils.execution_lifecycle import classify_operation_risk

SYMBOL = "Resolve.SetHighPriority (write-only; irreversible before 21.1)"


class Resolve211:
    """Mirrors the reported 21.1.0.14 behaviour: only a real bool is accepted."""

    def __init__(self, version=(21, 1, 0, 14, "")):
        self.version = list(version)
        self.calls = []

    def GetVersion(self):
        return self.version

    def SetHighPriority(self, *args):
        self.calls.append(args)
        return len(args) == 1 and isinstance(args[0], bool)


class Resolve19:
    """19.x: no argument accepted."""

    def __init__(self, version=(19, 1, 3, 7, "")):
        self.version = list(version) if version is not None else None
        self.calls = []

    def GetVersion(self):
        return self.version

    def SetHighPriority(self):
        self.calls.append(())
        return True


class HelperOn211(unittest.TestCase):
    def test_default_raises_with_a_real_bool(self):
        r = Resolve211()
        out = set_high_priority(r)
        self.assertEqual(r.calls, [(True,)])
        self.assertTrue(out["success"])
        self.assertTrue(out["reversible"])
        self.assertEqual(out["signature"], "SetHighPriority(bool)")
        self.assertIsNone(out["readback"])

    def test_false_restores_normal(self):
        r = Resolve211()
        out = set_high_priority(r, False)
        self.assertEqual(r.calls, [(False,)])
        self.assertTrue(out["success"])
        self.assertFalse(out["high_priority"])

    def test_string_false_is_coerced_to_a_real_bool(self):
        r = Resolve211()
        set_high_priority(r, "false")
        self.assertEqual(r.calls, [(False,)])
        self.assertIs(r.calls[0][0], False)

    def test_int_one_reaches_resolve_as_bool(self):
        r = Resolve211()
        set_high_priority(r, 1)
        self.assertIs(r.calls[0][0], True)

    def test_unrecognised_value_is_refused_before_any_call(self):
        r = Resolve211()
        out = set_high_priority(r, "high")
        self.assertEqual(r.calls, [])
        self.assertTrue(out["refused"])
        self.assertFalse(out["success"])

    def test_native_false_warns_without_claiming_state(self):
        r = Resolve211()
        r.SetHighPriority = lambda *a: False
        out = set_high_priority(r, True)
        self.assertFalse(out["success"])
        self.assertIn("warning", out)


class HelperOnLegacy(unittest.TestCase):
    def test_high_uses_the_no_arg_call(self):
        r = Resolve19()
        out = set_high_priority(r, True)
        self.assertEqual(r.calls, [()])
        self.assertTrue(out["success"])
        self.assertFalse(out["reversible"])
        self.assertEqual(out["signature"], "SetHighPriority()")

    def test_normal_is_refused_because_it_cannot_be_lowered(self):
        r = Resolve19()
        out = set_high_priority(r, False)
        self.assertEqual(r.calls, [])
        self.assertTrue(out["refused"])
        self.assertIn("cannot be lowered", out["error"])

    def test_unreadable_version_falls_back_only_on_type_error(self):
        r = Resolve19(version=None)
        out = set_high_priority(r, True)
        self.assertEqual(r.calls, [()])
        self.assertEqual(out["signature"], "SetHighPriority()")

    def test_unreadable_version_uses_typed_form_when_it_works(self):
        r = Resolve211(version=())
        out = set_high_priority(r, False)
        self.assertEqual(r.calls, [(False,)])
        self.assertTrue(out["reversible"])


class Compound(unittest.TestCase):
    def _call(self, native, params=None):
        with mock.patch.object(s, "get_resolve", return_value=native), \
             mock.patch.object(s, "_check", return_value=(native, None, None)):
            return s.resolve_control("set_high_priority", params)

    def test_bare_call_keeps_the_old_meaning(self):
        r = Resolve211()
        out = self._call(r)
        self.assertEqual(r.calls, [(True,)])
        self.assertTrue(out["success"])

    def test_high_false_on_211(self):
        r = Resolve211()
        out = self._call(r, {"high": "false"})
        self.assertEqual(r.calls, [(False,)])
        self.assertTrue(out["success"])

    def test_high_false_refused_on_19(self):
        r = Resolve19()
        out = self._call(r, {"high": False})
        self.assertEqual(r.calls, [])
        self.assertFalse(out.get("success", False))
        self.assertEqual(out["error"]["code"], "HIGH_PRIORITY_REFUSED")

    def test_fact_rides_along(self):
        r = Resolve211()
        out = self._call(r, {"high": True})
        self.assertEqual([k["symbol"] for k in out.get("known_limitation", [])], [SYMBOL])


class Granular(unittest.TestCase):
    def setUp(self):
        self._orig = rc.get_resolve

    def tearDown(self):
        rc.get_resolve = self._orig

    def test_granular_tool_calls_the_typed_form(self):
        r = Resolve211()
        rc.get_resolve = lambda: r
        out = rc.set_high_priority(high=False)
        self.assertEqual(r.calls, [(False,)])
        self.assertTrue(out["success"])

    def test_granular_tool_refuses_normal_on_legacy(self):
        r = Resolve19()
        rc.get_resolve = lambda: r
        out = rc.set_high_priority(high=False)
        self.assertEqual(r.calls, [])
        self.assertIn("error", out)

    def test_granular_tool_is_rated_as_a_write(self):
        level = destructive_hook.granular_risk_level("set_high_priority", rc.set_high_priority)
        self.assertEqual(level, "medium")


class WriteTables(unittest.TestCase):
    def test_registered_in_the_destructive_registry(self):
        self.assertTrue(destructive_hook.is_destructive("resolve_control", "set_high_priority"))

    def test_classified_as_a_recognised_medium_write(self):
        risk = classify_operation_risk("resolve_control", "set_high_priority", {"high": True})
        self.assertTrue(risk.recognised, risk)
        self.assertTrue(risk.destructive, risk)
        self.assertEqual(risk.level.value, "medium")


class Ledger(unittest.TestCase):
    def test_entry_is_version_scoped_and_reported(self):
        entry = next(e for e in API_TRUTH if e["symbol"] == SYMBOL)
        self.assertIn("reported", entry["tags"])
        self.assertIn("version-gated", entry["tags"])
        self.assertIn("highPriority: bool", entry["signature"])
        self.assertIn("REVERSIBLE", entry["reality"])
        self.assertIn("no getter", entry["reality"])
        self.assertEqual(entry["issue"], 207)

    def test_action_maps_to_the_entry(self):
        self.assertEqual(ACTION_SYMBOLS[("resolve_control", "set_high_priority")], [SYMBOL])
        self.assertEqual([t["symbol"] for t in traps_for("resolve_control", "set_high_priority")],
                         [SYMBOL])


if __name__ == "__main__":
    unittest.main()
