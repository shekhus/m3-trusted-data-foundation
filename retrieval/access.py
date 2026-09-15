"""Pre-model access filter (docs/addendum1.md A16; CLAUDE.md principle 9).

`AccessContext(role, plant)` decides which chunks may be retrieved at all. It is applied in SQL when
candidates are selected, so a chunk above the asker's level never reaches ranking, the prompt, or the model,
not even to be filtered out afterwards. The rules follow POL-ACC-001 (data/kb/docs):

    all         every role
    plant       leadership, and a plant user assigned to that plant
    restricted  leadership and data owners

Over-restriction is a failure too: leadership sees plant material for every plant, and a plant user sees
their own plant's profile (golden questions Q030-Q035, Q070-Q073). Prior resolutions are open to every role:
analysts are expected to find Sheldon's resolutions (Q100-Q104).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal["analyst", "leadership", "plant_user", "data_owner"]
ROLES: tuple[str, ...] = ("analyst", "leadership", "plant_user", "data_owner")


@dataclass(frozen=True)
class AccessContext:
    role: Role
    plant: str | None = None

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}; expected one of {', '.join(ROLES)}")
        if self.role == "plant_user" and not self.plant:
            raise ValueError("a plant_user must be assigned to a plant")

    @property
    def sees_restricted(self) -> bool:
        return self.role in ("leadership", "data_owner")

    @property
    def sees_all_plants(self) -> bool:
        return self.role == "leadership"

    def sql(self, alias: str = "c") -> tuple[str, dict[str, object]]:
        """A WHERE fragment and its parameters, over retrieval.chunks aliased as `alias`."""
        clauses = [f"{alias}.access_level = 'all'"]
        if self.sees_restricted:
            clauses.append(f"{alias}.access_level = 'restricted'")
        if self.sees_all_plants:
            clauses.append(f"{alias}.access_level = 'plant'")
        elif self.role == "plant_user":
            clauses.append(f"({alias}.access_level = 'plant' AND {alias}.plant = :access_plant)")
        return "(" + " OR ".join(clauses) + ")", {"access_plant": self.plant}

    def permits(self, access_level: str, plant: str | None) -> bool:
        """The same rule in Python, for tests and for explaining a refusal; retrieval uses `sql`."""
        if access_level == "all":
            return True
        if access_level == "restricted":
            return self.sees_restricted
        if access_level == "plant":
            return self.sees_all_plants or (self.role == "plant_user" and plant == self.plant)
        return False
