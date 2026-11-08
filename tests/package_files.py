"""Which repository modules the package installs: every `ai_desktop/*.py` except the development-only ones that
`packaging/arch/PKGBUILD` skips. Acceptance checks compare exactly this list with the installed tree."""
from pathlib import Path

DEVELOPMENT_ONLY = frozenset({"e2_boundary.py", "e2_probe.py"})


def packaged_modules(root):
    return sorted(path for path in (Path(root) / "ai_desktop").glob("*.py") if path.name not in DEVELOPMENT_ONLY)


def development_only_absent(installed):
    """A development-only module left in the installed tree is an extra file, not a match."""
    if any((Path(installed) / name).exists() or (Path(installed) / name).is_symlink() for name in DEVELOPMENT_ONLY):
        raise ValueError("installed-extra")
