"""The CoreAdapter contract, run against the stub and (optionally) any other adapter.

    PULSE_CONFORMANCE_TARGET=package.module:factory python -m pytest tests/test_adapter_conformance.py

See pulse/core_adapter/conformance.py for what `factory` returns.
"""
import importlib
import os

import pytest

from pulse.core_adapter import conformance
from pulse.core_adapter.base import Area


def _targets():
    out = [("stub", conformance.stub_target)]
    spec = os.environ.get("PULSE_CONFORMANCE_TARGET")
    if spec:
        mod, _, fn = spec.partition(":")
        out.append((spec, lambda: getattr(importlib.import_module(mod), fn)()))
    return out


@pytest.mark.parametrize("check", conformance.CHECKS, ids=lambda c: c.__name__.removeprefix("check_"))
@pytest.mark.parametrize("name,factory", _targets(), ids=[n for n, _ in _targets()])
def test_conformance(name, factory, check):
    try:
        check(factory())
    except conformance.Skip as e:
        pytest.skip(str(e))


def test_checks_catch_a_broken_adapter():
    """The suite is only useful if it fails: an adapter whose areas name a block that does not exist."""
    t = conformance.stub_target()
    real = t.adapter.list_areas

    def broken(ac_id, block_id=None):
        return [Area(a.area_id, a.ac_id, "blk-missing", a.kind, a.name_en, a.name_hi) for a in real(ac_id, block_id)]

    t.adapter.list_areas = broken
    with pytest.raises(AssertionError, match="not in list_blocks"):
        conformance.check_areas(t)
