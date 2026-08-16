from shepherd_rm import __version__


def test_package_has_version() -> None:
    """The package exposes the expected public version string."""
    assert __version__ == "0.1.0"
