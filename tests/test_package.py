from importlib.metadata import version

import heliograph


def test_version_comes_from_installed_metadata() -> None:
    assert heliograph.__version__ == version("heliograph")


def test_package_ships_type_information() -> None:
    from importlib.resources import files

    assert files("heliograph").joinpath("py.typed").is_file()
