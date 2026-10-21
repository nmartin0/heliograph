import importlib
import importlib.metadata
from importlib.metadata import PackageNotFoundError, version

import pytest

import heliograph


def test_version_comes_from_installed_metadata() -> None:
    assert heliograph.__version__ == version("heliograph")


def test_package_ships_type_information() -> None:
    from importlib.resources import files

    assert files("heliograph").joinpath("py.typed").is_file()


def test_an_uninstalled_source_tree_still_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    try:
        assert importlib.reload(heliograph).__version__ == "0+unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(heliograph)
