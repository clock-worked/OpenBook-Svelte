"""Import boundary check: no cross-service private imports.

Private helpers (``_``-prefixed) are implementation details of the module that
defines them. One service reaching into a sibling's privates creates hidden
coupling (the original case: ``jev_service`` importing five private helpers
from ``local_dialogue_ai_service``). Shared cross-service helpers belong in
``dialogue_ai_common.py`` under public names.

The grandfather table below freezes pre-existing violations so this check is
green today; each entry names the branch that removes the violation, after
which the entry must be deleted.
"""

import ast
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent

GRANDFATHERED = {
    # (importer, imported_from): removal tracking
    (
        "jev_service",
        "local_dialogue_ai_service",
    ): "refactor/architecture-deepening moves the shared helpers into dialogue_ai_common",
    (
        "jev_verify_cli",
        "local_dialogue_ai_service",
    ): "survives refactor/architecture-deepening; move _load_local_env_file into dialogue_ai_common (public) as follow-up",
}


class ImportBoundaryTests(unittest.TestCase):
    def test_no_cross_service_private_imports(self):
        module_names = {path.stem for path in MODULE_DIR.glob("*.py")}
        violations = []
        for path in sorted(MODULE_DIR.glob("*.py")):
            if path.stem.startswith("test_"):
                continue  # tests may legitimately reach into a module's internals
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level != 0 or node.module is None:
                    continue  # only absolute imports; relative imports are package-internal
                imported_from = node.module.split(".")[0]
                if imported_from not in module_names or imported_from == path.stem:
                    continue  # not a sibling py_services module
                for alias in node.names:
                    if alias.name.startswith("_"):
                        if (path.stem, imported_from) in GRANDFATHERED:
                            continue
                        violations.append(f"{path.stem}: {alias.name} <- {imported_from}")
        self.assertFalse(
            violations,
            "cross-service private imports:\n" + "\n".join(violations),
        )
