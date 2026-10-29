"""PID liveness probes must never call os.kill(pid, 0) on Windows.

On Windows `os.kill` with any signal other than CTRL_C_EVENT/CTRL_BREAK_EVENT
is `TerminateProcess`, so the POSIX "signal 0 existence check" kills the
process it asks about. Every probe below is routinely asked about this very
process (the busy sidecar, the transport state file and the panel pidfile all
record the server's own pid), so on Windows the check terminated the server --
and, under the offline suite, the test runner (issue #278).

The routing tests run everywhere: they pretend to be Windows and fail if
`os.kill` is reached. The real-API tests run only on Windows.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from src import server
from src.utils import mcp_transport, proc, resolve_busy


def _no_kill(*_args, **_kwargs):
    raise AssertionError("os.kill reached on Windows: that terminates the process")


class WindowsRoutingTests(unittest.TestCase):
    @staticmethod
    @contextlib.contextmanager
    def _as_windows(module, alive):
        """Pretend to be Windows for the probe call only, never longer."""
        with mock.patch.object(os, "name", "nt"), \
                mock.patch.object(os, "kill", _no_kill), \
                mock.patch.object(module, "windows_pid_alive", return_value=alive) as probe:
            yield probe

    def test_busy_probe_uses_the_windows_api(self):
        with self._as_windows(resolve_busy, True) as probe:
            self.assertTrue(resolve_busy._pid_alive(os.getpid()))
        probe.assert_called_once_with(os.getpid())

    def test_busy_probe_treats_unknown_as_gone(self):
        with self._as_windows(resolve_busy, None):
            self.assertFalse(resolve_busy._pid_alive(os.getpid()))

    def test_control_panel_probe_uses_the_windows_api(self):
        with self._as_windows(server, True) as probe:
            self.assertTrue(server._control_panel_pid_alive(os.getpid()))
        probe.assert_called_once_with(os.getpid())

    def test_control_panel_probe_treats_unknown_as_gone(self):
        with self._as_windows(server, None):
            self.assertFalse(server._control_panel_pid_alive(os.getpid()))

    def _transport_state(self, alive):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mcp_transport.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "transport": "stdio"}, handle)
            with mock.patch.object(mcp_transport, "TRANSPORT_STATE_PATH", path), \
                    self._as_windows(mcp_transport, alive):
                return mcp_transport.read_transport_state()

    def test_transport_state_of_a_live_pid_is_kept(self):
        state = self._transport_state(True)
        self.assertEqual(state["pid"], os.getpid())

    def test_transport_state_of_a_gone_pid_is_stale(self):
        self.assertIsNone(self._transport_state(False))


@unittest.skipUnless(os.name == "nt", "exercises the real Windows process API")
class WindowsPidAliveTests(unittest.TestCase):
    def test_this_process_is_alive_and_survives_the_question(self):
        self.assertIs(proc.windows_pid_alive(os.getpid()), True)
        self.assertIs(proc.windows_pid_alive(os.getpid()), True)

    def test_an_exited_child_is_gone(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
        pid = child.pid
        # Keep the Popen handle open until after the probe: while any handle
        # is held the pid cannot be reused, so the answer is about this child.
        child.wait(timeout=30)
        self.assertIs(proc.windows_pid_alive(pid), False)

    def test_a_non_positive_pid_is_not_alive(self):
        self.assertIs(proc.windows_pid_alive(0), False)
        self.assertIs(proc.windows_pid_alive(-1), False)


if __name__ == "__main__":
    unittest.main()
