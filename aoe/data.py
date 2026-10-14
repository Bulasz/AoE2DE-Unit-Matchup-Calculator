"""Loading and querying the extracted game data (data/game_data.json)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from .constants import EXCLUDED_UNITS, MELEE, PIERCE

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "game_data.json"

BLACKSMITH, UNIVERSITY, CASTLE = 103, 209, 82
UNAVAILABLE = "NotAvailable"
# Attack/armor classes that only matter against buildings - techs touching only these
# do not change the outcome of a unit-vs-unit fight.
BUILDING_CLASSES = {11, 13, 21, 22, 26, 40}
LABEL_OVERRIDES = {1260: "Cuman Mercenaries"}


@dataclass
class Civ:
    id: int
    key: str
    name: str
    help: str
    tree_effect: int
    team_effect: int
    disabled_techs: set[int]
    unique_techs: list[int]
    units: dict[int, dict]
    techs: dict[int, dict]

    def has_unit(self, uid: int, full_tree: bool) -> bool:
        node = self.units.get(uid)
        if node is None:
            return False
        if node["status"] != UNAVAILABLE:
            return True
        return full_tree and node["type"] not in ("UniqueUnit",)

    def has_tech(self, tid: int, full_tree: bool) -> bool:
        node = self.techs.get(tid)
        if node is None:
            return False
        if node["status"] != UNAVAILABLE:
            return True
        return full_tree and node["type"] not in ("UniqueTech",)


@dataclass
class GameData:
    meta: dict
    civs: dict[int, Civ]
    units: dict[int, dict]
    projectiles: dict[int, dict]
    techs: dict[int, dict]
    effects: dict[int, dict]
    buildings: dict[int, str]
    _labels: dict[int, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: Path | str = DATA_FILE) -> "GameData":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        civs = {}
        for c in raw["civs"]:
            civs[c["id"]] = Civ(
                id=c["id"], key=c["key"], name=c["name"], help=c.get("help", ""),
                tree_effect=c["tree_effect"], team_effect=c["team_effect"],
                disabled_techs=set(c["disabled_techs"]), unique_techs=c["unique_techs"],
                units={int(k): v for k, v in c["units"].items()},
                techs={int(k): v for k, v in c["techs"].items()},
            )
        units = {int(k): v for k, v in raw["units"].items()}
        for uid, u in units.items():
            u["id"] = uid
        projectiles = {int(k): v for k, v in raw["projectiles"].items()}
        for pid, p in projectiles.items():
            p["id"] = pid
        gd = cls(
            meta=raw["meta"], civs=civs, units=units, projectiles=projectiles,
            techs={int(k): v for k, v in raw["techs"].items()},
            effects={int(k): v for k, v in raw["effects"].items()},
            buildings={int(k): v for k, v in raw["buildings"].items()},
        )
        gd._build_labels()
        return gd

    # --------------------------------------------------------------- helpers
    def _build_labels(self) -> None:
        """Unit names are not unique (e.g. Donjon Spearmen, Barracks Huskarls). The variant most
        civs can train keeps the plain name; the others get their building appended."""
        civ_count: dict[int, int] = {}
        tree_building: dict[int, int] = {}
        for c in self.civs.values():
            for uid, node in c.units.items():
                tree_building.setdefault(uid, node["building"])
                if node["status"] != UNAVAILABLE:
                    civ_count[uid] = civ_count.get(uid, 0) + 1
        by_name: dict[str, list[int]] = {}
        for uid, u in self.units.items():
            by_name.setdefault(u["name"], []).append(uid)
        for name, ids in by_name.items():
            ids = sorted(ids, key=lambda u: (-civ_count.get(u, 0), u))
            for i, uid in enumerate(ids):
                label = name
                if i > 0:
                    b = tree_building.get(uid, self.units[uid].get("train_location", -1))
                    label = f"{name} ({LABEL_OVERRIDES.get(uid) or self.buildings.get(b, f'#{uid}')})"
                self._labels[uid] = label
        # still ambiguous (same name + building): append the id
        seen: dict[str, int] = {}
        for uid in sorted(self._labels):
            seen[self._labels[uid]] = seen.get(self._labels[uid], 0) + 1
        for uid in sorted(self._labels):
            if seen[self._labels[uid]] > 1:
                self._labels[uid] = f"{self._labels[uid]} #{uid}"

    def label(self, uid: int) -> str:
        return self._labels.get(uid, self.units.get(uid, {}).get("name", f"#{uid}"))

    @cached_property
    def civ_list(self) -> list[Civ]:
        return sorted(self.civs.values(), key=lambda c: c.name)

    def civ_by_key(self, key: str) -> Civ | None:
        return next((c for c in self.civs.values() if c.key == key), None)

    @staticmethod
    def is_combat_unit(u: dict) -> bool:
        if u["id"] in EXCLUDED_UNITS:
            return False
        attacks = u.get("attacks", {})
        return any(v > 0 for v in attacks.values()) or u.get("charge_projectile", -1) >= 0

    def unit_ids_for_civ(self, civ_id: int, full_tree: bool) -> list[int]:
        civ = self.civs[civ_id]
        out = [uid for uid in civ.units
               if uid in self.units and civ.has_unit(uid, full_tree) and self.is_combat_unit(self.units[uid])]
        return sorted(out, key=self.label)

    def unit_min_age(self, civ_id: int, uid: int) -> int:
        node = self.civs[civ_id].units.get(uid)
        return max(1, node["age"]) if node else 1

    # ------------------------------------------------------- upgrade chains
    def upgrade_chain(self, civ_id: int, uid: int) -> list[int]:
        """Return the unit line containing uid (from base unit to final upgrade)."""
        civ = self.civs[civ_id]
        nodes = civ.units

        def parent(u):
            n = nodes.get(u)
            if n and n["type"] in ("UnitUpgrade", "UniqueUnit", "RegionalUnit") and n.get("link", -1) in nodes:
                return n["link"]
            return None

        chain = [uid]
        seen = {uid}
        p = parent(uid)
        while p is not None and p not in seen:
            chain.insert(0, p)
            seen.add(p)
            p = parent(p)
        cur = uid
        while True:
            children = [k for k in nodes if k not in seen and parent(k) == cur]
            if not children:
                break
            cur = children[0]
            chain.append(cur)
            seen.add(cur)
        return [u for u in chain if u in self.units]

    def similar_units(self, civ_id: int, uid: int, full_tree: bool, limit: int = 30) -> list[int]:
        u = self.units[uid]
        chain = set(self.upgrade_chain(civ_id, uid))
        pool = [x for x in self.unit_ids_for_civ(civ_id, full_tree) if x not in chain]

        def score(x):
            o = self.units[x]
            s = 0
            if o["class"] == u["class"]:
                s += 4
            if o.get("train_location") == u.get("train_location"):
                s += 2
            if (o.get("range", 0) > 1) == (u.get("range", 0) > 1):
                s += 1
            return s

        ranked = sorted((x for x in pool if score(x) >= 3), key=lambda x: (-score(x), self.label(x)))
        return ranked[:limit]

    # ----------------------------------------------------------------- techs
    def tech_age(self, civ_id: int, tid: int) -> int:
        node = self.civs[civ_id].techs.get(tid)
        return node["age"] if node else 4

    def tech_affects(self, tid: int, obj_ids: set[int], obj_classes: set[int]) -> bool:
        from .stats import COMBAT_ATTRS
        t = self.techs.get(tid)
        if not t or t["effect"] < 0:
            return False
        from .stats import decode_class_amount
        for _, a, b, attr, value in self.effects.get(t["effect"], {}).get("cmds", []):
            if attr not in COMBAT_ATTRS or (a == -1 and b == -1):
                continue
            if attr in (8, 9) and decode_class_amount(value)[0] in BUILDING_CLASSES:
                continue
            if (a == -1 or a in obj_ids) and (b == -1 or b in obj_classes):
                return True
        return False

    def related_objects(self, uid: int) -> tuple[set[int], set[int]]:
        """Unit + projectile ids/classes whose stats feed into this unit's combat."""
        u = self.units[uid]
        ids, classes = {uid}, {u["class"]}
        for key in ("projectile", "secondary_projectile", "charge_projectile"):
            pid = u.get(key, -1)
            if pid in self.projectiles:
                ids.add(pid)
                classes.add(self.projectiles[pid]["class"])
        return ids, classes

    def techs_for_unit(self, civ_id: int, uid: int, age: int, full_tree: bool) -> dict[str, list[dict]]:
        """Group the techs a civ can research up to `age` that are relevant to the unit.

        Returns {"blacksmith": [...], "university": [...], "unique": [...], "other": [...]}
        Each entry: {"id", "name", "help", "age", "applies", "available"}.
        Unique techs are always listed (greyed out when they do not apply).
        """
        civ = self.civs[civ_id]
        ids, classes = self.related_objects(uid)
        groups: dict[str, list[dict]] = {"blacksmith": [], "university": [], "unique": [], "other": []}
        for tid, node in civ.techs.items():
            t = self.techs.get(tid)
            if t is None:
                continue
            is_unique = node["type"] == "UniqueTech"
            available = civ.has_tech(tid, full_tree)
            if not available and not is_unique:
                continue
            if not is_unique and node["age"] > age:
                continue
            applies = self.tech_affects(tid, ids, classes)
            entry = {"id": tid, "name": t["name"], "help": t["help"], "age": node["age"],
                     "applies": applies, "available": available and node["age"] <= age}
            if is_unique:
                groups["unique"].append(entry)
            elif not applies:
                continue
            elif node["building"] == BLACKSMITH:
                groups["blacksmith"].append(entry)
            elif node["building"] == UNIVERSITY:
                groups["university"].append(entry)
            else:
                groups["other"].append(entry)
        for g in groups.values():
            g.sort(key=lambda e: (e["age"], e["id"]))
        return groups


def damage_classes(u: dict) -> str:
    atk = u.get("attacks", {})
    return "pierce" if atk.get(str(PIERCE), 0) > atk.get(str(MELEE), 0) else "melee"
