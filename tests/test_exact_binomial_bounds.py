from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "compute_exact_binomial_bounds.py"
_SPEC = importlib.util.spec_from_file_location("compute_exact_binomial_bounds", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
clopper_pearson_interval = _MODULE.clopper_pearson_interval


def test_all_success_exact_bounds_match_closed_form() -> None:
    lower, upper = clopper_pearson_interval(28, 28)

    assert lower == pytest.approx(0.025 ** (1 / 28), abs=1e-12)
    assert upper == 1.0


def test_zero_success_exact_bounds_match_closed_form() -> None:
    lower, upper = clopper_pearson_interval(0, 30)

    assert lower == 0.0
    assert upper == pytest.approx(1 - 0.025 ** (1 / 30), abs=1e-12)


def test_central_exact_interval_is_symmetric() -> None:
    lower, upper = clopper_pearson_interval(5, 10)

    assert lower == pytest.approx(0.187086, abs=1e-6)
    assert upper == pytest.approx(0.812914, abs=1e-6)


@pytest.mark.parametrize(
    ("successes", "trials", "confidence", "exception"),
    [
        (-1, 10, 0.95, ValueError),
        (11, 10, 0.95, ValueError),
        (0, 0, 0.95, ValueError),
        (1, 10, 1.0, ValueError),
        (True, 10, 0.95, TypeError),
    ],
)
def test_exact_interval_validates_inputs(
    successes: int,
    trials: int,
    confidence: float,
    exception: type[Exception],
) -> None:
    with pytest.raises(exception):
        clopper_pearson_interval(successes, trials, confidence=confidence)
