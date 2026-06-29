from __future__ import annotations

import importlib

import pytest


def test_mem0_modules_are_gone():
    for mod in ("axon.memory.mem0_tool", "axon.memory.config"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)


def test_preserved_memory_modules_still_import():
    importlib.import_module("axon.memory.session_compressor")
    importlib.import_module("axon.memory.session_hook")
