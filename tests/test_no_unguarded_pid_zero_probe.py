"""Every `os.kill(pid, 0)` liveness probe in src/ must sit behind a Windows branch.

On Windows, os.kill with any signal other than CTRL_C_EVENT/CTRL_BREAK_EVENT is
TerminateProcess, so the POSIX "signal 0 existence check" kills the process it
asks about. Three probes that are routinely asked about the server's own pid
did exactly that (issue #278). This guard fails when a new signal-0 probe lands
in a function that never checks `os.name == "nt"` / `sys.platform` first.
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")


def _is_signal_zero_kill(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or len(node.args) < 2:
        return False
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "kill"
            and isinstance(func.value, ast.Name) and func.value.id == "os"):
        return False
    sig = node.args[1]
    return isinstance(sig, ast.Constant) and sig.value == 0


def _mentions_windows_branch(func: ast.AST) -> bool:
    source = ast.unparse(func)
    return 'os.name == "nt"' in source or "os.name == 'nt'" in source or "sys.platform" in source


class NoUnguardedPidZeroProbeTest(unittest.TestCase):
    def test_signal_zero_probes_branch_on_windows_first(self):
        offenders = []
        for dirpath, _dirs, files in os.walk(SRC):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, "r", encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=path)
                for func in ast.walk(tree):
                    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if not any(_is_signal_zero_kill(n) for n in ast.walk(func)):
                        continue
                    if not _mentions_windows_branch(func):
                        offenders.append(f"{os.path.relpath(path, ROOT)}:{func.lineno} {func.name}")
        self.assertEqual(
            offenders, [],
            "os.kill(pid, 0) terminates the process on Windows; branch on os.name == 'nt' "
            "and use src.utils.proc.windows_pid_alive there: " + ", ".join(offenders),
        )

    def test_the_guard_finds_the_probes_it_is_meant_to_cover(self):
        # Non-vacuous: the known probe sites are still seen as signal-0 kills.
        seen = set()
        for rel in ("src/utils/resolve_busy.py", "src/utils/mcp_transport.py",
                    "src/utils/resolve_bridge.py"):
            with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            if any(_is_signal_zero_kill(n) for n in ast.walk(tree)):
                seen.add(rel)
        self.assertEqual(len(seen), 3, seen)


if __name__ == "__main__":
    unittest.main()
