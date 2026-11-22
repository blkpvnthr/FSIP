"""Extensible federal agency registry.

Milestone 1 hard-coded a single NASA entry. Milestone 2 generalizes this into a
small, extensible registry covering the target historical scope (NASA, DoD, DHS,
DOE, GSA) with:

* canonical identifiers (``key``) and human-readable names,
* authoritative USAspending **top-tier agency codes** (resolved from the live
  ``/api/v2/references/toptier_agencies/`` endpoint — never guessed),
* abbreviations and alias resolution (so ``"dod"``, ``"defense"``,
  ``"Department of Defense"`` all resolve to the same agency), and
* a backward-compatible ``AGENCIES`` mapping in the exact shape Milestone-1 code
  (``models.ExtractRequest.agency_meta``, the CLI) already consumes.

The awarding-vs-funding distinction is a *transaction attribute*, not an agency
property; it is handled at extraction/partition time, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Agency:
    """A top-tier federal agency in the registry."""

    key: str  # canonical identifier, e.g. "NASA"
    name: str  # official top-tier name
    abbreviation: str
    toptier_code: str  # authoritative USAspending top-tier code (zero-padded)
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def as_meta(self) -> dict[str, str]:
        """Backward-compatible dict shape used by Milestone-1 code."""
        return {
            "name": self.name,
            "toptier_code": self.toptier_code,
            "abbreviation": self.abbreviation,
        }


# Authoritative codes verified against the live USAspending toptier_agencies
# reference endpoint on 2025 data (NASA=080, DOD=097, DHS=070, DOE=089, GSA=047).
REGISTRY: dict[str, Agency] = {
    "NASA": Agency(
        key="NASA",
        name="National Aeronautics and Space Administration",
        abbreviation="NASA",
        toptier_code="080",
        aliases=("nasa",),
    ),
    "DOD": Agency(
        key="DOD",
        name="Department of Defense",
        abbreviation="DOD",
        toptier_code="097",
        aliases=("dod", "dept of defense", "defense", "department of defense"),
    ),
    "DHS": Agency(
        key="DHS",
        name="Department of Homeland Security",
        abbreviation="DHS",
        toptier_code="070",
        aliases=("dhs", "homeland security", "department of homeland security"),
    ),
    "DOE": Agency(
        key="DOE",
        name="Department of Energy",
        abbreviation="DOE",
        toptier_code="089",
        aliases=("doe", "energy", "department of energy"),
    ),
    "GSA": Agency(
        key="GSA",
        name="General Services Administration",
        abbreviation="GSA",
        toptier_code="047",
        aliases=("gsa", "general services administration"),
    ),
}

# Reverse index: lowercased key / abbreviation / official name / alias -> key.
_ALIAS_INDEX: dict[str, str] = {}
for _agency in REGISTRY.values():
    _ALIAS_INDEX[_agency.key.lower()] = _agency.key
    _ALIAS_INDEX[_agency.abbreviation.lower()] = _agency.key
    _ALIAS_INDEX[_agency.name.lower()] = _agency.key
    for _alias in _agency.aliases:
        _ALIAS_INDEX[_alias.lower()] = _agency.key

# Top-tier code -> canonical key (for mapping record-level agency codes back to a
# canonical partition value).
_CODE_INDEX: dict[str, str] = {a.toptier_code: a.key for a in REGISTRY.values()}

# Backward-compatible mapping consumed by Milestone-1 code (same shape as the old
# ``models.AGENCIES``).
AGENCIES: dict[str, dict[str, str]] = {k: v.as_meta() for k, v in REGISTRY.items()}


def resolve(identifier: str) -> Agency:
    """Resolve a key, abbreviation, official name, or alias to an :class:`Agency`.

    Raises:
        KeyError: the identifier matches no registered agency.
    """
    key = _ALIAS_INDEX.get(identifier.strip().lower())
    if key is None:
        raise KeyError(f"Unknown agency {identifier!r}. Known: {sorted(REGISTRY)}")
    return REGISTRY[key]


def agency_for_code(toptier_code: str) -> Agency | None:
    """Return the registered agency for a top-tier code, or ``None`` if unknown."""
    key = _CODE_INDEX.get(toptier_code)
    return REGISTRY[key] if key else None


def list_agencies() -> list[Agency]:
    """Return all registered agencies, sorted by canonical key."""
    return [REGISTRY[k] for k in sorted(REGISTRY)]
