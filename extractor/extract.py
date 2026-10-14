"""Extract combat-relevant data from a local Age of Empires II: DE installation.

Reads the game's own data file (empires2_x2_p1.dat), the in-game tech tree
definitions (CivTechTrees/*.json) and the English string table, and writes a
compact JSON file (data/game_data.json) that the Streamlit app loads.

Usage:
    python -m extractor.extract                      # auto-detect the game folder
    python -m extractor.extract --game "D:/SteamLibrary/steamapps/common/AoE2DE"
    python -m extractor.extract --commit             # also git-commit + push the result

Requires `genieutils-py` (see requirements-extract.txt). The deployed app does NOT
need it: it only reads the generated JSON.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = ROOT / "data" / "game_data.json"

DEFAULT_GAME_DIRS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\AoE2DE",
    r"D:\SteamLibrary\steamapps\common\AoE2DE",
    r"E:\SteamLibrary\steamapps\common\AoE2DE",
    r"C:\SteamLibrary\steamapps\common\AoE2DE",
    r"C:\XboxGames\Age of Empires II- Definitive Edition\Content",
]

AGE_TECHS = {1: 104, 2: 101, 3: 102, 4: 103}  # Dark, Feudal, Castle, Imperial

# Effect attributes that matter for combat or for the stat comparison table.
# See https://ugc.aoe2.rocks/general/attributes/attributes/
KEPT_ATTRIBUTES = {
    0,    # hit points
    1,    # line of sight
    5,    # movement speed
    8,    # armor (class << 8 | amount)
    9,    # attack (class << 8 | amount)
    10,   # attack reload time
    11,   # accuracy percent
    12,   # max range
    16,   # projectile unit
    20,   # min range
    22,   # blast width
    24,   # bonus damage resistance
    41,   # frame delay
    59, 60, 61, 62,  # charge: max, recharge rate, event, type
    63,   # combat ability bit field
    64,   # attack dispersion
    65,   # secondary projectile unit
    100, 101, 102, 103, 104, 105, 106, 107,  # costs, train time, missiles
    109,  # regeneration rate (HP/min)
    118,  # damage reflection
    120,  # regeneration HP percent
    125,  # charge projectile unit
    160, 161, 164, 165,  # add/remove armor/attack class
}
# Effect command types: 0 set, 4 add, 5 multiply (10/14/15 are the team variants).
ATTR_COMMAND_TYPES = {0: 0, 4: 4, 5: 5, 10: 0, 14: 4, 15: 5}

# Combat forms of units that the tech tree lists under another id:
#   extra unit id -> tech-tree unit id whose availability it shares.
EXTRA_UNITS = {
    42: 331,                  # Trebuchet (unpacked) <- Trebuchet (packed)
    1738: 1759, 1740: 1761,   # Ratha melee mode <- Ratha ranged mode
    1980: 1962,               # War Chariot (Barrage) <- War Chariot (Focus Fire)
    1252: 1225, 1253: 1227,   # dismounted Konnik (spawned when a Konnik dies)
}

# Auto-researched techs that only fire in special game modes / scenarios.
AUTO_TECH_DENYLIST = re.compile(r"^\[|Post-Imperial|Regicide|KotH|Empire Wars|\bEW\b", re.I)


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def find_game_dir(explicit: str | None) -> Path:
    candidates = [explicit] if explicit else DEFAULT_GAME_DIRS
    for c in candidates:
        if c and (Path(c) / "resources" / "_common" / "dat" / "empires2_x2_p1.dat").exists():
            return Path(c)
    sys.exit("Could not find the AoE2 DE folder. Pass it with --game <path to AoE2DE>.")


def load_strings(game_dir: Path) -> dict[int, str]:
    path = game_dir / "resources" / "en" / "strings" / "key-value" / "key-value-strings-utf8.txt"
    strings: dict[int, str] = {}
    pattern = re.compile(r'^(\d+)\s+"(.*)"\s*$')
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = pattern.match(line)
        if m:
            strings[int(m.group(1))] = m.group(2).replace('\\"', '"').replace("\\n", "\n")
    return strings


def clean_help(text: str) -> str:
    """Strip the in-game markup from help strings."""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\(\s*\W?cost\W?\s*\)", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def game_build_id(game_dir: Path) -> str | None:
    manifest = game_dir.parent.parent / "appmanifest_813780.acf"
    if manifest.exists():
        m = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text(errors="ignore"))
        if m:
            return m.group(1)
    return None


def r6(x: float) -> float:
    return round(float(x), 6)


# ----------------------------------------------------------------------------
# extraction
# ----------------------------------------------------------------------------

def unit_record(u, graphics, units) -> dict:
    t50 = u.type_50
    cre = u.creatable
    rec = {
        "internal": u.name,
        "class": u.class_,
        "name_id": u.language_dll_name,
        "help_id": u.language_dll_help,
        "hp": u.hit_points,
        "los": r6(u.line_of_sight),
        "speed": r6(u.speed),
    }
    if t50 is not None:
        delay = 0.0
        if t50.frame_delay and t50.attack_graphic >= 0 and t50.attack_graphic < len(graphics):
            g = graphics[t50.attack_graphic]
            if g is not None and g.frame_count:
                delay = g.frame_duration * g.frame_count * t50.frame_delay / g.frame_count
        rec.update({
            "attacks": {str(a.class_): a.amount for a in t50.attacks},
            "armours": {str(a.class_): a.amount for a in t50.armours},
            "reload": r6(t50.reload_time),
            "frame_delay": t50.frame_delay,
            "attack_delay": r6(delay),
            "range": r6(t50.max_range),
            "min_range": r6(t50.min_range),
            "accuracy": t50.accuracy_percent,
            "dispersion": r6(t50.accuracy_dispersion),
            "projectile": t50.projectile_unit_id,
            "blast_width": r6(t50.blast_width),
            "blast_damage": r6(t50.blast_damage),
            "bonus_resist": r6(t50.bonus_damage_resistance),
            # genieutils-py names this field after the old "break off combat" byte;
            # in DE it holds the Combat Ability bit field (attribute 63).
            "combat_ability": t50.break_off_combat,
            "reflection": r6(t50.damage_reflection),
            "displayed_attack": t50.displayed_attack,
            "displayed_melee_armour": t50.displayed_melee_armour,
            "displayed_range": r6(t50.displayed_range),
        })
    if cre is not None:
        costs = {}
        for c in cre.resource_costs:
            if c.flag and c.type in (0, 1, 2, 3) and c.amount:
                costs[("food", "wood", "stone", "gold")[c.type]] = c.amount
        rec.update({
            "cost": costs,
            "train_time": cre.train_locations[0].train_time if cre.train_locations else 0,
            "train_location": cre.train_locations[0].unit_id if cre.train_locations else -1,
            "displayed_pierce_armour": cre.displayed_pierce_armour,
            "total_projectiles": r6(cre.total_projectiles),
            "max_total_projectiles": cre.max_total_projectiles,
            "secondary_projectile": cre.secondary_projectile_unit,
            "charge_type": cre.charge_type,
            "charge_max": r6(cre.max_charge),
            "charge_recharge": r6(cre.recharge_rate),
            "charge_event": cre.charge_event,
            "charge_target": cre.charge_target,
            "charge_projectile": cre.charge_projectile_unit,
            # genieutils-py legacy names: in DE these hold regeneration (HP/min)
            "regen": r6(cre.rear_attack_modifier),
            "hero_mode": cre.hero_mode,
        })
    return rec


def projectile_record(p) -> dict:
    t50 = p.type_50
    return {
        "internal": p.name,
        "class": p.class_,
        "speed": r6(p.speed),
        "attacks": {str(a.class_): a.amount for a in t50.attacks} if t50 else {},
        "armours": {},
        "blast_width": r6(t50.blast_width) if t50 else 0.0,
    }


def extract(game_dir: Path) -> dict:
    try:
        from genieutils.datfile import DatFile
    except ImportError:
        sys.exit("genieutils-py is not installed: pip install -r requirements-extract.txt")

    dat_path = game_dir / "resources" / "_common" / "dat" / "empires2_x2_p1.dat"
    dat_dir = dat_path.parent
    print(f"Parsing {dat_path} ...")
    d = DatFile.parse(str(dat_path))
    strings = load_strings(game_dir)
    civ_list = json.loads((dat_dir / "civilizations.json").read_text(encoding="utf-8"))["civilization_list"]

    # ---- civs + tech tree availability ---------------------------------------
    civs = []
    unit_ids: set[int] = set()
    tech_ids: set[int] = set()
    for idx, c in enumerate(civ_list):
        if idx == 0 or c.get("era", "base") != "base":
            continue
        tree_file = dat_dir / "CivTechTrees" / f"{c['tech_tree_name']}.json"
        if not tree_file.exists():
            print(f"  ! no tech tree file for {c['internal_name']}, skipped")
            continue
        tree = json.loads(tree_file.read_text(encoding="utf-8"))
        t_units, t_techs = {}, {}
        for node in tree["civ_techs_units"]:
            if "Node Type" not in node:
                node["Node Type"] = "UnitUpgrade" if node.get("Link ID", -1) >= 0 else "Unit"
            entry = {
                "age": node["Age ID"],
                "building": node["Building ID"],
                "status": node["Node Status"],
                "type": node["Node Type"],
            }
            if node["Use Type"] == "Unit":
                entry["link"] = node.get("Link ID", -1)
                entry["trigger_tech"] = node.get("Trigger Tech ID", -1)
                t_units[str(node["Node ID"])] = entry
                unit_ids.add(node["Node ID"])
            elif node["Use Type"] == "Tech":
                t_techs[str(node["Node ID"])] = entry
                tech_ids.add(node["Node ID"])
        source_to_extra = {src: extra for extra, src in EXTRA_UNITS.items()}
        for extra, src in EXTRA_UNITS.items():
            if str(src) in t_units:
                node = dict(t_units[str(src)])
                if node.get("link", -1) in source_to_extra:
                    node["link"] = source_to_extra[node["link"]]
                node["variant_of"] = src
                t_units[str(extra)] = node
                unit_ids.add(extra)
        civ = d.civs[idx]
        tree_eff = d.effects[civ.tech_tree_id] if civ.tech_tree_id >= 0 else None
        disabled = sorted({int(cmd.d) for cmd in tree_eff.effect_commands if cmd.type == 102}) if tree_eff else []
        civs.append({
            "id": idx,
            "key": c["internal_name"],
            "name": strings.get(c["name_string_id"], c["internal_name"]),
            "help": clean_help(strings.get(c.get("tech_tree_description_string_id", -1), "")),
            "tree_effect": civ.tech_tree_id,
            "team_effect": civ.team_bonus_id,
            "disabled_techs": disabled,
            "unique_techs": [c.get("unique_tech_id_1", -1), c.get("unique_tech_id_2", -1)],
            "units": t_units,
            "techs": t_techs,
        })
    civ_ids = [c["id"] for c in civs]

    # ---- units ---------------------------------------------------------------
    ref_units = d.civs[civ_ids[0]].units
    units = {}
    for uid in sorted(unit_ids):
        u = ref_units[uid] if uid < len(ref_units) else None
        if u is None or u.type_50 is None or u.creatable is None:
            continue
        units[str(uid)] = unit_record(u, d.graphics, ref_units)

    # Some units have civ-specific base data; store only the differing fields.
    overrides: dict[str, dict] = {}
    for cid in civ_ids:
        cunits = d.civs[cid].units
        for uid, base in units.items():
            cu = cunits[int(uid)] if int(uid) < len(cunits) else None
            if cu is None:
                continue
            rec = unit_record(cu, d.graphics, cunits)
            diff = {k: v for k, v in rec.items() if base.get(k) != v}
            if diff:
                overrides.setdefault(str(cid), {})[uid] = diff

    # ---- projectiles referenced by units -------------------------------------
    projectiles = {}
    for uid, rec in units.items():
        for key in ("projectile", "secondary_projectile", "charge_projectile"):
            pid = rec.get(key, -1)
            if pid is not None and pid >= 0 and str(pid) not in projectiles:
                p = ref_units[pid] if pid < len(ref_units) else None
                if p is not None:
                    projectiles[str(pid)] = projectile_record(p)

    # ---- techs + effects -----------------------------------------------------
    def is_auto(t) -> bool:
        return all(loc.location_id <= 0 for loc in t.research_locations)

    auto_ids = set()
    for i, t in enumerate(d.techs):
        if t.effect_id < 0 or not is_auto(t) or AUTO_TECH_DENYLIST.search(t.name or ""):
            continue
        if t.civ == -1 and t.required_tech_count == 0:
            continue  # generic zero-requirement auto techs are scenario/mode leftovers
        if t.civ != -1 and t.civ not in civ_ids:
            continue
        auto_ids.add(i)

    effect_ids = {c["tree_effect"] for c in civs} | {c["team_effect"] for c in civs}
    keep_techs = set(tech_ids) | auto_ids | set(AGE_TECHS.values())
    for c in civs:
        keep_techs.update(t for t in c["unique_techs"] if t >= 0)

    techs = {}
    for i in sorted(keep_techs):
        if i >= len(d.techs):
            continue
        t = d.techs[i]
        techs[str(i)] = {
            "name": strings.get(t.language_dll_name, t.name) or t.name,
            "internal": t.name,
            "help": clean_help(strings.get(t.language_dll_help, "") or strings.get(t.language_dll_description, "")),
            "civ": t.civ,
            "effect": t.effect_id,
            "requires": [r for r in t.required_techs if r >= 0],
            "requires_count": t.required_tech_count,
            "auto": i in auto_ids,
            "location": t.research_locations[0].location_id if t.research_locations else -1,
            "cost": {("food", "wood", "stone", "gold")[c.type]: c.amount
                     for c in t.resource_costs if c.flag and c.type in (0, 1, 2, 3) and c.amount},
        }
        if t.effect_id >= 0:
            effect_ids.add(t.effect_id)

    effects = {}
    skipped_types: dict[int, int] = {}
    for eid in sorted(e for e in effect_ids if e is not None and e >= 0):
        e = d.effects[eid]
        cmds = []
        for cmd in e.effect_commands:
            if cmd.type in ATTR_COMMAND_TYPES and cmd.c in KEPT_ATTRIBUTES:
                cmds.append([ATTR_COMMAND_TYPES[cmd.type], cmd.a, cmd.b, cmd.c, r6(cmd.d)])
            elif cmd.type not in (1, 2, 3, 6, 7, 101, 102, 103):
                skipped_types[cmd.type] = skipped_types.get(cmd.type, 0) + 1
        effects[str(eid)] = {"name": e.name, "cmds": cmds}

    # ---- names ---------------------------------------------------------------
    for uid, rec in units.items():
        rec["name"] = strings.get(rec["name_id"], rec["internal"])
        rec["help"] = clean_help(strings.get(rec["help_id"], ""))

    building_names = {}
    for c in civs:
        for e in list(c["units"].values()) + list(c["techs"].values()):
            b = e["building"]
            if str(b) not in building_names and 0 <= b < len(ref_units) and ref_units[b] is not None:
                building_names[str(b)] = strings.get(ref_units[b].language_dll_name, ref_units[b].name)

    dat_bytes = dat_path.read_bytes()
    meta = {
        "source": "Age of Empires II: Definitive Edition game files",
        "extracted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "dat_modified": dt.datetime.fromtimestamp(dat_path.stat().st_mtime, dt.timezone.utc).isoformat(timespec="seconds"),
        "dat_sha1": hashlib.sha1(dat_bytes).hexdigest()[:12],
        "game_build": game_build_id(game_dir),
        "skipped_effect_types": skipped_types,
    }
    print(f"  {len(civs)} civs, {len(units)} units, {len(projectiles)} projectiles, "
          f"{len(techs)} techs ({len(auto_ids)} auto), {len(effects)} effects")
    return {
        "meta": meta,
        "civs": civs,
        "units": units,
        "unit_overrides": overrides,
        "projectiles": projectiles,
        "techs": techs,
        "effects": effects,
        "buildings": building_names,
    }


def git_commit_and_push(message: str) -> None:
    def run(*args):
        print("  $ git", " ".join(args))
        subprocess.run(["git", *args], cwd=ROOT, check=True)

    run("add", str(OUT_FILE.relative_to(ROOT)))
    status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if status.returncode == 0:
        print("No data changes to commit.")
        return
    run("commit", "-m", message)
    run("push")


def _patch_label(dat_date: str) -> int | None:
    """Best-effort: the wiki's latest AoE2 DE update number, if it is not newer than the game data."""
    try:
        sys.path.insert(0, str(ROOT))
        from aoe.updater import latest_fandom_patch
        patch = latest_fandom_patch()
    except Exception as e:  # noqa: BLE001 - offline extraction must still work
        print(f"  (could not look up the patch number on the wiki: {e})")
        return None
    if patch and patch.get("date") and patch["date"] <= dat_date:
        print(f"  Game data corresponds to Update {patch['update']} ({patch['date']})")
        return patch["update"]
    return None


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", help="Path to the AoE2DE install folder")
    ap.add_argument("--out", default=str(OUT_FILE), help="Output JSON path")
    ap.add_argument("--commit", action="store_true", help="git commit + push the new data file")
    args = ap.parse_args(argv)

    game_dir = find_game_dir(args.game)
    data = extract(game_dir)
    data["meta"]["game_update"] = _patch_label(data["meta"]["dat_modified"][:10])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    old_hash = None
    if out.exists():
        try:
            old_hash = json.loads(out.read_text(encoding="utf-8"))["meta"].get("dat_sha1")
        except (ValueError, KeyError):
            pass
    out.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.2f} MB)")
    if old_hash == data["meta"]["dat_sha1"]:
        print("Game data file unchanged since the last extraction.")
    if args.commit:
        git_commit_and_push(f"Update game data (build {data['meta']['game_build']}, dat {data['meta']['dat_sha1']})")


if __name__ == "__main__":
    main()
