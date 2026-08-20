"""Static checks that stand in for the parts of `pytest` an offline box cannot run.

Not a replacement for the test suite. It answers the two questions that a
missing package registry otherwise leaves open — does every first-party import
resolve, and does every call to a first-party function match that function's
signature — by reading the AST rather than by importing anything. It needs no
third-party package, so it runs where `pytest` cannot.

    python -m scripts.static_check

Exits non-zero on the first category with findings, and prints every finding.
"""

import ast
import pathlib
from collections.abc import Iterator

ROOT = pathlib.Path(__file__).resolve().parent.parent
# `alembic` is deliberately absent: the directory of that name is a migration
# folder, not an importable package, and `from alembic import op` refers to the
# installed library. Migrations are covered by `check_migrations`, which reads
# the directory directly.
PACKAGES = ("app", "tests", "scripts")


def _modules() -> Iterator[tuple[str, pathlib.Path]]:
    for package in PACKAGES:
        base = ROOT / package
        if not base.exists():
            continue

        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue

            relative = path.relative_to(ROOT).with_suffix("")
            parts = list(relative.parts)

            if parts[-1] == "__init__":
                parts.pop()

            yield ".".join(parts), path


def _parse() -> dict[str, tuple[pathlib.Path, ast.Module]]:
    parsed: dict[str, tuple[pathlib.Path, ast.Module]] = {}

    for name, path in _modules():
        parsed[name] = (path, ast.parse(path.read_text(encoding="utf-8"), str(path)))

    return parsed


def _exported(tree: ast.Module) -> set[str]:
    """Every name a module binds at top level, including re-exports."""

    names: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)

    return names


def check_imports(parsed: dict[str, tuple[pathlib.Path, ast.Module]]) -> list[str]:
    """Every `from app... import X` names something that module actually binds."""

    exports = {name: _exported(tree) for name, (_, tree) in parsed.items()}
    problems: list[str] = []

    for _name, (path, tree) in parsed.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level:
                continue

            module = node.module or ""

            if not module.startswith(PACKAGES):
                continue

            if module not in exports:
                # A submodule import (`from app.services.features.email import
                # draft_service`) resolves to a module, not to a bound name.
                if any(key.startswith(f"{module}.") for key in exports):
                    for alias in node.names:
                        if f"{module}.{alias.name}" not in exports and (
                            alias.name not in exports.get(module, set())
                        ):
                            problems.append(
                                f"{path}:{node.lineno}: {module} has no {alias.name!r}"
                            )
                    continue

                problems.append(f"{path}:{node.lineno}: unknown module {module!r}")
                continue

            for alias in node.names:
                if alias.name == "*":
                    continue

                if (
                    alias.name not in exports[module]
                    and f"{module}.{alias.name}" not in exports
                ):
                    problems.append(
                        f"{path}:{node.lineno}: {module} does not export {alias.name!r}"
                    )

    return problems


def _signatures(
    parsed: dict[str, tuple[pathlib.Path, ast.Module]],
) -> dict[tuple[str, str], ast.arguments]:
    found: dict[tuple[str, str], ast.arguments] = {}

    for name, (_, tree) in parsed.items():
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found[(name, node.name)] = node.args

    return found


def check_calls(parsed: dict[str, tuple[pathlib.Path, ast.Module]]) -> list[str]:
    """Every `module.function(...)` call matches that function's signature.

    Only checks calls made through an imported module alias, which is the
    project's dominant style (`draft_service.get_draft(...)`). That is where a
    renamed keyword argument silently survives a lint and fails at runtime.
    """

    signatures = _signatures(parsed)
    problems: list[str] = []

    for _name, (path, tree) in parsed.items():
        aliases: dict[str, str] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                PACKAGES
            ):
                for alias in node.names:
                    target = f"{node.module}.{alias.name}"
                    if target in parsed:
                        aliases[alias.asname or alias.name] = target

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func = node.func

            if not isinstance(func, ast.Attribute) or not isinstance(
                func.value, ast.Name
            ):
                continue

            module = aliases.get(func.value.id)

            if module is None:
                continue

            args = signatures.get((module, func.attr))

            if args is None:
                if any(key == (module, func.attr) for key in signatures):
                    continue

                exported = _exported(parsed[module][1])
                if func.attr not in exported:
                    problems.append(
                        f"{path}:{node.lineno}: {module} has no {func.attr!r}"
                    )
                continue

            positional = [arg.arg for arg in (*args.posonlyargs, *args.args)]
            keyword_only = {arg.arg for arg in args.kwonlyargs}
            accepted = set(positional) | keyword_only

            if len(node.args) > len(positional) and args.vararg is None:
                problems.append(
                    f"{path}:{node.lineno}: {module}.{func.attr} takes "
                    f"{len(positional)} positional arguments, "
                    f"{len(node.args)} given"
                )

            if args.kwarg is not None:
                continue

            for keyword in node.keywords:
                if keyword.arg is None:
                    continue

                if keyword.arg not in accepted:
                    problems.append(
                        f"{path}:{node.lineno}: {module}.{func.attr} has no "
                        f"parameter {keyword.arg!r}"
                    )

            required = {
                arg.arg
                for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
                if default is None
            }
            supplied = {keyword.arg for keyword in node.keywords}

            missing = required - supplied
            if missing and not any(keyword.arg is None for keyword in node.keywords):
                problems.append(
                    f"{path}:{node.lineno}: {module}.{func.attr} is missing "
                    f"required keyword argument(s) {sorted(missing)}"
                )

    return problems


def check_migrations() -> list[str]:
    """The migration chain is linear and has exactly one head."""

    versions = ROOT / "alembic" / "versions"

    if not versions.exists():
        return []

    revisions: dict[str, str | None] = {}

    for path in sorted(versions.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        found: dict[str, str | None] = {}

        for node in tree.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(
                node.target, ast.Name
            ):
                continue

            if node.target.id in ("revision", "down_revision"):
                value = node.value
                found[node.target.id] = (
                    value.value if isinstance(value, ast.Constant) else None
                )

        if "revision" in found and found["revision"]:
            revisions[str(found["revision"])] = found.get("down_revision")

    parents = {parent for parent in revisions.values() if parent}
    heads = [revision for revision in revisions if revision not in parents]
    problems = []

    if len(heads) != 1:
        problems.append(f"expected exactly one migration head, found {sorted(heads)}")

    for revision, parent in revisions.items():
        if parent is not None and parent not in revisions:
            problems.append(f"migration {revision} points at unknown parent {parent}")

    return problems


def main() -> int:
    parsed = _parse()
    failed = False

    for label, findings in (
        ("imports", check_imports(parsed)),
        ("call sites", check_calls(parsed)),
        ("migrations", check_migrations()),
    ):
        if findings:
            failed = True
            print(f"\n{label}: {len(findings)} problem(s)")
            for finding in findings:
                print(f"  {finding}")
        else:
            print(f"{label}: OK")

    print(f"\n{len(parsed)} modules checked.")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
