from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


FORBIDDEN_MODULE_PREFIXES = (
    "app.graph",
    "app.services.gateway",
    "app.workflows",
    "app.channels",
    "app.workers",
)


@dataclass(frozen=True)
class ImportViolation:
    path: Path
    line: int
    module: str


def scan_forbidden_imports(source_root: str | Path) -> list[ImportViolation]:
    """Return forbidden old-business imports found below a V2 source root."""

    root = Path(source_root)
    violations: list[ImportViolation] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for module in _imported_modules(node):
                if _is_forbidden(module):
                    violations.append(ImportViolation(path=path, line=node.lineno, module=module))
    return sorted(violations, key=lambda item: (str(item.path), item.line, item.module))


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [
            f"{node.module}.{alias.name}" if node.module == "app" or node.module == "app.services" else node.module
            for alias in node.names
        ]
    return []


def _is_forbidden(module: str) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in FORBIDDEN_MODULE_PREFIXES)
