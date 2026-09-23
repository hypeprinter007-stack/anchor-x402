#!/usr/bin/env python3
"""Guard the split-artifact builds against import drift.

Each Lambda in template.yaml is assembled by a hand-maintained `cp` list in the
Makefile, and those lists drift from what the code actually imports. When they
do, the artifact is not merely incomplete — it raises ImportError on the first
invocation. That is how the RefundCron backstop sat dead for six weeks: the
sanctions gate added `from services import screen` to refund.py, the cp list was
never updated, and every cron run died before reaching the DDB scan (fb5bf25).

`sam build` cannot catch this: it runs the cp list and never checks the result.
So this reads every first-party module the build shipped, extracts what it
imports (including imports nested inside functions, which is exactly the shape
refund_cron uses), and resolves each one against the artifact's own contents.

Static on purpose. The artifacts hold cross-compiled manylinux x86_64 wheels, so
importing them on a macOS build host fails on native modules like pydantic_core
regardless of whether the artifact is correct — a dynamic check would be noise
here and could only run inside a Linux container.

Run as `make build`'s last step; exits non-zero on drift.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = REPO_ROOT / ".aws-sam" / "build"

# Provided by the Lambda Python runtime. The Makefile strips the first four to
# stay under the 250 MB unzipped limit (keep in sync with its `rm -rf` line);
# dateutil and six arrive in the runtime as botocore's own dependencies.
LAMBDA_PROVIDED = {"boto3", "botocore", "s3transfer", "jmespath", "dateutil", "six"}

# Imports a function ships without, deliberately, because the code path holding
# them is unreachable from that handler. Each entry needs the reason and the
# reachability argument, because a wrong entry here reintroduces exactly the bug
# this script exists to catch. Reported as SKIP so they stay visible in the build.
OPTIONAL_IMPORTS: dict[tuple[str, str], str] = {
    ("RefundCronFunction", "solders"): (
        "refund.py imports solders inside _svm_payer_from_tx, which only "
        "parse_buyer_from_x_payment calls, which only app.py calls. The cron "
        "handler calls refund_failed_job and nothing else, so the import never "
        "executes. solders is a large native wheel; shipping it would undo the "
        "split build's size win for dead code."
    ),
}

EXT_SUFFIXES = (".so", ".pyd", ".abi3.so")


def _is_first_party(top: str) -> bool:
    """True for modules that live in this repo rather than site-packages."""
    return (REPO_ROOT / f"{top}.py").exists() or (REPO_ROOT / top / "__init__.py").exists()


def _resolve(root: Path, dotted: str) -> bool:
    """Can `dotted` be imported with `root` as the only sys.path entry?"""
    parts = dotted.split(".")
    cur = root
    for i, part in enumerate(parts):
        pkg = cur / part
        if (pkg / "__init__.py").exists():
            cur = pkg
            continue
        if i == len(parts) - 1:
            if (cur / f"{part}.py").exists():
                return True
            if any(p.name.startswith(f"{part}.") and p.name.endswith(EXT_SUFFIXES)
                   for p in cur.glob(f"{part}.*")):
                return True
            return pkg.is_dir()  # namespace package
        if pkg.is_dir():
            cur = pkg
            continue
        return False
    return True


def _bound_names(path: Path) -> set[str]:
    """Top-level names a module defines, so `from m import x` on an attribute
    (rather than a submodule) is not misread as a missing file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).partition(".")[0] for a in node.names)
    return names


def _requirements(path: Path, package: str) -> list[tuple[str, int, bool]]:
    """(dotted module, lineno, strict) for every import in one file.

    `strict` marks a first-party target, where the full dotted path must resolve
    to a file. Third-party imports are checked at top-level only: packages lazily
    expose submodules often enough that demanding every dotted path resolve on
    disk would flag working artifacts.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    except (OSError, SyntaxError):
        return []

    out: list[tuple[str, int, bool]] = []
    for node in ast.walk(tree):  # walk, not iterate: refund_cron imports inside handler()
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.partition(".")[0]
                first = _is_first_party(top)
                out.append((alias.name if first else top, node.lineno, first))

        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative: resolve against this file's package
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                module = f"{base}.{node.module}" if node.module else base
                first = True
            else:
                if not node.module:
                    continue
                module = node.module
                first = _is_first_party(module.partition(".")[0])

            out.append((module if first else module.partition(".")[0], node.lineno, first))
            if first:
                # `from services import screen` needs services/screen.py unless
                # services/__init__.py binds the name itself.
                for alias in node.names:
                    if alias.name != "*":
                        out.append((f"{module}.{alias.name}", node.lineno, True))
    return out


def _check(artifact: Path, handler_module: str, logical: str) -> tuple[list[str], list[str]]:
    """(problems, skips) for one built artifact, as human-readable lines."""
    problems: list[str] = []
    skips: list[str] = []
    if not _resolve(artifact, handler_module):
        problems.append(f"handler module '{handler_module}' is not in the artifact at all")

    # First-party == shipped by a cp line, i.e. present at the same path in the
    # repo. Everything else in the artifact is a vendored dependency we skip.
    shipped = sorted(
        p for p in artifact.rglob("*.py")
        if (REPO_ROOT / p.relative_to(artifact)).exists()
    )

    seen: set[str] = set()
    for path in shipped:
        rel = path.relative_to(artifact)
        package = ".".join(rel.parts[:-1]) if rel.parts[:-1] else ""
        for dotted, lineno, strict in _requirements(path, package):
            if dotted in seen or _resolve(artifact, dotted):
                continue
            top = dotted.partition(".")[0]
            if top in sys.stdlib_module_names or top in LAMBDA_PROVIDED:
                continue
            if (logical, top) in OPTIONAL_IMPORTS:
                if top not in seen:
                    seen.add(top)
                    skips.append(f"'{top}' absent by design — {OPTIONAL_IMPORTS[(logical, top)]}")
                continue
            if strict and "." in dotted:
                # Might be an attribute of a shipped module, not a submodule.
                parent = dotted.rsplit(".", 1)[0]
                parent_file = artifact / Path(*parent.split(".")) / "__init__.py"
                if not parent_file.exists():
                    parent_file = artifact / (Path(*parent.split(".")).as_posix() + ".py")
                if parent_file.exists() and dotted.rsplit(".", 1)[1] in _bound_names(parent_file):
                    continue
            seen.add(dotted)
            hint = ("add it to the cp line" if strict
                    else "add it to this function's requirements file")
            problems.append(f"{rel}:{lineno} imports '{dotted}', missing from the artifact — {hint}")
    return problems, skips


def _functions() -> list[tuple[str, str]]:
    """(logical id, handler module) for each Python function in template.yaml."""
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    # SAM templates carry CFN intrinsics (!Ref, !Sub, !GetAtt) that SafeLoader
    # rejects; we only read Handler and Runtime, so discard them.
    Loader.add_multi_constructor("!", lambda *_: None)

    doc = yaml.load((REPO_ROOT / "template.yaml").read_text(), Loader=Loader)
    default_runtime = (doc.get("Globals", {}).get("Function", {}) or {}).get("Runtime", "")

    out = []
    for logical, res in (doc.get("Resources") or {}).items():
        if res.get("Type") != "AWS::Serverless::Function":
            continue
        props = res.get("Properties") or {}
        if not str(props.get("Runtime") or default_runtime).startswith("python"):
            continue  # a non-Python artifact would need its own check
        handler = props.get("Handler", "")
        if "." in handler:
            out.append((logical, handler.rsplit(".", 1)[0]))
    return out


def main() -> int:
    functions = _functions()
    if not functions:
        print("verify-artifacts: no Python functions found in template.yaml", file=sys.stderr)
        return 1

    failed = False
    for logical, module in functions:
        artifact = BUILD_DIR / logical
        if not artifact.is_dir():
            print(f"  FAIL  {logical}: nothing built at {artifact.relative_to(REPO_ROOT)}")
            failed = True
            continue
        problems, skips = _check(artifact, module, logical)
        for s in skips:
            print(f"  SKIP  {logical}: {s}")
        if not problems:
            print(f"  PASS  {logical} ({module})")
            continue
        failed = True
        for p in problems:
            print(f"  FAIL  {logical}: {p}")

    print("\nartifact drift detected — these Lambdas would fail at invocation" if failed
          else "all artifacts carry every module their code imports")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
