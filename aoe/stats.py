"""Compute a unit's effective stats by replaying the game's own effect commands.

Order of application mirrors a typical game:
  1. civ tech tree effect (contains a few civ bonuses)
  2. civ team bonus (a team bonus always applies to its own player)
  3. for each age up to the selected one:
       age tech -> auto-researched techs that became available (civ bonuses, auto upgrades)
       -> techs the user selected for that age (each followed by any auto techs they trigger)
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .constants import AGE_TECHS
from .data import GameData

SET, ADD, MUL = 0, 4, 5

ATTR_KEYS = {
    0: "hp", 1: "los", 5: "speed", 10: "reload", 11: "accuracy", 12: "range",
    16: "projectile", 20: "min_range", 22: "blast_width", 24: "bonus_resist",
    41: "frame_delay", 59: "charge_max", 60: "charge_recharge", 61: "charge_event",
    62: "charge_type", 63: "combat_ability", 64: "dispersion", 65: "secondary_projectile",
    101: "train_time", 102: "total_projectiles", 107: "max_total_projectiles",
    109: "regen", 118: "reflection", 120: "regen_pct", 125: "charge_projectile",
}
COST_ATTRS = {103: "food", 104: "wood", 105: "gold", 106: "stone"}
# Stored as 16-bit integers in the game data, so modified values are rounded.
INTEGER_KEYS = {"hp", "accuracy", "frame_delay", "train_time", "max_total_projectiles"}
# Attributes that change how a unit fights (used to decide whether a tech "applies").
COMBAT_ATTRS = {0, 8, 9, 10, 11, 12, 16, 20, 24, 41, 59, 60, 62, 63, 65, 102, 107, 109, 118, 120, 125,
                160, 161, 164, 165}


def decode_class_amount(value: float) -> tuple[int, int]:
    """Armor/attack effect values encode `class << 8 | amount` (amount is a signed byte)."""
    v = int(round(value))
    cls, amt = v >> 8, v & 0xFF
    if amt > 127:
        amt -= 256
        cls += 1
    return cls, amt


@dataclass
class AppliedEffect:
    source: str      # display name of the tech / bonus
    tech_id: int | None
    kind: str        # "civ", "team", "age", "auto", "user"


@dataclass
class UnitStats:
    unit: dict                       # effective unit record (attacks/armours with int keys)
    projectiles: dict[int, dict]     # effective projectile records by id
    applied: list[AppliedEffect] = field(default_factory=list)

    def projectile(self, key: str) -> dict | None:
        pid = self.unit.get(key, -1)
        return self.projectiles.get(pid)


def _normalize(rec: dict) -> dict:
    r = copy.deepcopy(rec)
    r["attacks"] = {int(k): v for k, v in r.get("attacks", {}).items()}
    r["armours"] = {int(k): v for k, v in r.get("armours", {}).items()}
    r.setdefault("cost", {})
    return r


def _apply_number(rec: dict, key: str, op: int, value: float) -> None:
    cur = rec.get(key, 0) or 0
    if op == SET:
        rec[key] = value
    elif op == ADD:
        rec[key] = cur + value
    elif op == MUL:
        rec[key] = cur * value


def apply_command(rec: dict, op: int, attr: int, value: float) -> None:
    if attr in (8, 9):
        table = rec["armours"] if attr == 8 else rec["attacks"]
        cls, amt = decode_class_amount(value)
        if cls not in table:
            return  # effects only modify existing classes
        if op == SET:
            table[cls] = amt
        elif op == ADD:
            table[cls] = table[cls] + amt
        elif op == MUL:
            table[cls] = round(table[cls] * amt / 100.0)  # stored as integers in the game
        return
    if attr in (160, 161):  # add armor / attack class
        table = rec["armours"] if attr == 160 else rec["attacks"]
        cls, amt = decode_class_amount(value)
        table.setdefault(cls, amt)
        return
    if attr in (164, 165):  # remove armor / attack class
        table = rec["armours"] if attr == 164 else rec["attacks"]
        cls, _ = decode_class_amount(value)
        table.pop(cls, None)
        return
    if attr in COST_ATTRS:
        res = COST_ATTRS[attr]
        if res in rec["cost"] or op == SET:
            _apply_number(rec["cost"], res, op, value)
            rec["cost"][res] = round(rec["cost"][res])  # costs are integers in the game
        return
    if attr == 100:  # total cost: multiply every resource
        if op == MUL:
            for res in rec["cost"]:
                rec["cost"][res] = round(rec["cost"][res] * value)
        return
    key = ATTR_KEYS.get(attr)
    if key is None:
        return
    if key == "frame_delay":
        old = rec.get("frame_delay", 0) or 0
        _apply_number(rec, key, op, value)
        new = rec["frame_delay"]
        if old > 0 and rec.get("attack_delay"):
            rec["attack_delay"] = rec["attack_delay"] * new / old
        return
    _apply_number(rec, key, op, value)
    if key == "accuracy":
        rec[key] = max(0, min(100, rec[key]))
    elif key in INTEGER_KEYS:
        rec[key] = round(rec[key])


def effect_sequence(data: GameData, civ_id: int, age: int, user_techs: set[int],
                    full_tree: bool) -> list[tuple[AppliedEffect, int]]:
    civ = data.civs[civ_id]
    disabled = set() if full_tree else civ.disabled_techs
    seq: list[tuple[AppliedEffect, int]] = []
    if civ.tree_effect >= 0:
        seq.append((AppliedEffect(f"{civ.name} tech tree", None, "civ"), civ.tree_effect))
    if civ.team_effect >= 0:
        seq.append((AppliedEffect(f"{civ.name} team bonus", None, "team"), civ.team_effect))

    candidates = [tid for tid, t in data.techs.items()
                  if t["auto"] and t["civ"] in (-1, civ_id) and tid not in disabled and t["effect"] >= 0]
    researched: set[int] = set()

    def add(tid: int, kind: str) -> None:
        researched.add(tid)
        t = data.techs.get(tid)
        if t and t["effect"] >= 0:
            seq.append((AppliedEffect(t["name"], tid, kind), t["effect"]))

    def fire_auto() -> None:
        changed = True
        while changed:
            changed = False
            for tid in candidates:
                if tid in researched:
                    continue
                t = data.techs[tid]
                if sum(1 for r in t["requires"] if r in researched) >= t["requires_count"]:
                    add(tid, "auto")
                    changed = True

    by_age: dict[int, list[int]] = {}
    for tid in user_techs:
        by_age.setdefault(data.tech_age(civ_id, tid), []).append(tid)

    for a in range(1, age + 1):
        add(AGE_TECHS[a], "age")
        fire_auto()
        for tid in sorted(by_age.get(a, [])):
            add(tid, "user")
            fire_auto()
    return seq


def compute_stats(data: GameData, civ_id: int, unit_id: int, age: int,
                  user_techs: set[int] | None = None, full_tree: bool = False) -> UnitStats:
    user_techs = set(user_techs or ())
    seq = effect_sequence(data, civ_id, age, user_techs, full_tree)

    unit = _normalize(data.units[unit_id])
    objects: dict[int, dict] = {unit_id: unit}
    for pid, p in data.projectiles.items():
        objects.setdefault(pid, _normalize(p))

    applied: list[AppliedEffect] = []
    unit_ids_of_interest = set(objects)
    for info, eid in seq:
        eff = data.effects.get(eid)
        if not eff:
            continue
        touched = False
        for op, a, b, attr, value in eff["cmds"]:
            if a == -1 and b == -1:
                continue
            if a != -1 and a not in unit_ids_of_interest:
                continue
            for oid, rec in objects.items():
                if (a == -1 or a == oid) and (b == -1 or b == rec["class"]):
                    apply_command(rec, op, attr, value)
                    if oid == unit_id or oid in _linked_projectiles(unit):
                        touched = True
        if touched:
            applied.append(info)

    projectiles = {pid: objects[pid] for pid in _linked_projectiles(unit) if pid in objects}
    return UnitStats(unit=unit, projectiles=projectiles, applied=applied)


def _linked_projectiles(unit: dict) -> set[int]:
    return {unit.get(k, -1) for k in ("projectile", "secondary_projectile", "charge_projectile")} - {-1, None}


def max_techs(data: GameData, civ_id: int, unit_id: int, age: int, full_tree: bool) -> set[int]:
    """All techs (available to the civ by `age`) that improve this unit."""
    groups = data.techs_for_unit(civ_id, unit_id, age, full_tree)
    return {e["id"] for g in groups.values() for e in g if e["applies"] and e["available"]}
