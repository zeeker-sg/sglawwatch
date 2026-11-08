"""Every resource module imports, and zeeker.toml agrees with resources/."""

import importlib
import pkgutil

import pytest

import resources

MODULES = sorted(m.name for m in pkgutil.iter_modules(resources.__path__))


def test_module_list_is_not_empty():
    assert {"headlines", "commentaries", "about_singapore_law"} <= set(MODULES)


@pytest.mark.parametrize("name", MODULES)
def test_resource_module_imports(name):
    # Importing exercises the third-party imports at module top level
    # (feedparser, httpx, tenacity, bs4, click, sqlite_utils, zeeker).
    module = importlib.import_module(f"resources.{name}")
    assert module is not None


def test_every_toml_resource_has_a_module_with_fetch_data(zeeker_toml):
    for name in zeeker_toml["resource"]:
        module = importlib.import_module(f"resources.{name}")
        assert callable(getattr(module, "fetch_data", None)), name


def test_fragment_enabled_resources_define_fetch_fragments_data(zeeker_toml):
    for name, cfg in zeeker_toml["resource"].items():
        if cfg.get("fragments"):
            module = importlib.import_module(f"resources.{name}")
            assert callable(getattr(module, "fetch_fragments_data", None)), name


@pytest.mark.parametrize("name", ["commentaries_fragments", "about_singapore_law_fragments"])
def test_fragment_stub_modules_return_no_rows(name):
    module = importlib.import_module(f"resources.{name}")
    assert module.fetch_data(None) == []
