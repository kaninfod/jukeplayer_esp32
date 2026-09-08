#!/usr/bin/env python3
"""Host-side static checks for the JukePlayer MicroPython codebase.

Runs on CPython (host), needs no device:
  1. Syntax gate: compiles every device .py file (same grammar as MicroPython
     for our purposes; catches indentation/syntax breakage before deploy).
  2. Display contract: every `display.<method>` referenced by app/services
     must exist on all display managers AND the dummy. This is the check that
     would have caught the silent button no-ops (backlight/encoder regressions).
  3. AppState contract: state constant values are unique, and every key in
     AppState's defaults is a known constant.

Usage: python3 scripts/check.py   (exit 0 = pass, 1 = failure)
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEVICE_FILES = [ROOT / "boot.py", ROOT / "main.py", ROOT / "toggle_mode.py"] + sorted(
    (ROOT / "jukeplayer").rglob("*.py")
)

MANAGERS = {
    "ST7735R DisplayManager": (ROOT / "jukeplayer/hardware/st7735r/display_manager.py", "DisplayManager"),
    "ILI9488 DisplayManager": (ROOT / "jukeplayer/hardware/ili9488/display_manager.py", "DisplayManager"),
    "DummyDisplay (headless)": (ROOT / "jukeplayer/mocks/dummy_display.py", "DummyDisplay"),
}

# The lifecycle/API contract every display backend must honor.
DISPLAY_CONTRACT = {"update", "start", "stop", "show_message", "toggle_backlight"}

# Files whose code is allowed to call display methods (the button-facing surface).
DISPLAY_CALLERS = [
    "jukeplayer/services/button_handler.py",
    "jukeplayer/services/hardware_service.py",
    "jukeplayer/services/nfc_service.py",
    "jukeplayer/app.py",
]


def dotted_parts(node):
    """Return the dotted path of an Attribute chain rooted at a Name, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        parts.reverse()
        return parts
    return None


def display_calls_referenced(tree):
    """Names x such that <...>.display.x appears in the tree."""
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            parts = dotted_parts(node)
            if parts and len(parts) >= 2 and parts[-2] == "display":
                calls.add(parts[-1])
    return calls


def class_methods(tree, class_name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return None


def parse(path):
    return ast.parse(path.read_text(), filename=str(path))


def main():
    failures = []

    # 1. Syntax gate
    for path in DEVICE_FILES:
        try:
            compile(path.read_text(), str(path), "exec")
        except SyntaxError as e:
            failures.append(f"SYNTAX {path.relative_to(ROOT)}: {e}")
    print(f"[1/3] syntax gate: {len(DEVICE_FILES)} device files compiled")

    # 2. Display contract
    trees = {name: parse(path) for name, (path, _) in MANAGERS.items()}
    referenced = set()
    for rel in DISPLAY_CALLERS:
        referenced |= display_calls_referenced(parse(ROOT / rel))
    required = DISPLAY_CONTRACT | referenced
    for label, (path, cls) in MANAGERS.items():
        methods = class_methods(trees[label], cls)
        if methods is None:
            failures.append(f"CONTRACT class {cls} not found in {path.relative_to(ROOT)}")
            continue
        missing = sorted(required - methods)
        if missing:
            failures.append(
                f"CONTRACT {label} ({cls}) missing: {', '.join(missing)}"
            )
    print(
        f"[2/3] display contract: required={sorted(required)} across "
        f"{len(MANAGERS)} backends -> {'FAIL' if any(f.startswith('CONTRACT') for f in failures) else 'ok'}"
    )

    # 3. AppState keys are known, unique state constants
    consts = {}
    for node in parse(ROOT / "jukeplayer/core/state_constants.py").body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = node.value.value
    seen = {}
    for name, value in consts.items():
        if value in seen:
            failures.append(f"STATE duplicate constant value {value}: {name} vs {seen[value]}")
        seen[value] = name
    app_state_tree = parse(ROOT / "jukeplayer/core/app_state.py")
    for node in ast.walk(app_state_tree):
        if isinstance(node, ast.Dict) and node.keys and isinstance(node.keys[0], ast.Name):
            for key in node.keys:
                if isinstance(key, ast.Name) and key.id not in consts:
                    failures.append(f"STATE AppState default key {key.id!r} is not a known constant")
    print(f"[3/3] state contract: {len(consts)} constants -> {'FAIL' if any(f.startswith('STATE') for f in failures) else 'ok'}")

    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())