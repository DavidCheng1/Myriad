"""Phase 2: the package is importable and the test setup works."""

import myriad


def test_package_imports() -> None:
    assert myriad.__version__ == "0.1.0"
