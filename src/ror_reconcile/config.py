from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"

DEFAULT_DB_PATH = PROCESSED_DIR / "ror.sqlite"


@dataclass(frozen=True)
class MatchConfig:
    """Configuration for candidate retrieval, scoring and automatic acceptance."""

    shortlist_limit: int = 50
    return_limit: int = 5
    # Exact lookups use a higher limit than fuzzy retrieval to retain shared names.
    exact_limit: int = 500
    # ROR marks records inactive or withdrawn; exclude them as link targets.
    # Records without a status stay matchable.
    active_only: bool = True
    # Decision gates (precision-first).
    # Frozen from the seed-42 Horizon development grid. Among the configurations
    # with identical best dev precision/recall, these are the strictest gates.
    lexical_gate: float = 0.80
    auto_match_threshold: float = 0.86
    min_margin: float = 0.03
    # Property boosts / penalties.
    country_boost: float = 0.04
    country_penalty: float = 0.03
    city_boost: float = 0.03
    city_penalty: float = 0.02
    # Coverage-aware name scoring: discriminating tokens must be covered, and
    # candidates carrying many extra tokens are mildly down-weighted (compactness).
    min_scoring_token_len: int = 3
    token_coverage_threshold: float = 0.84
    compactness_exponent: float = 0.35
    # All stages are enabled by default. The ablations add them progressively.
    use_fts: bool = True
    use_trigram: bool = True
    use_coverage: bool = True
    use_country: bool = True
    use_city: bool = True
    use_margin: bool = True
    require_multi_token_fuzzy_signal: bool = True
