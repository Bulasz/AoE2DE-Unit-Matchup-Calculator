"""Help texts shown in the (?) tooltips."""

HELP = {
    "civ": "The civilization fielding this unit. Its civ bonuses, team bonus and unique technologies "
           "are applied automatically, exactly as the game applies them.",
    "full_tree": "Full Tech Tree: the civ gets every generic unit and technology (like the in-game "
                 "'Full Tech Tree' setting), plus its own unique units and techs. Civ bonuses still apply.",
    "unit": "Type to search. Units whose name starts with what you typed are listed first, then names "
            "with a word starting with it, then any name containing it. The list only shows units this "
            "civ can train (or all generic units with Full Tech Tree).",
    "age": "The age the fight takes place in. Determines which age-based civ bonuses are active and which "
           "technologies can be researched. Ages before the unit becomes available are hidden.",
    "max": "Tick every technology available in this age that improves this unit (Blacksmith, University, "
           "unique techs and others like Bloodlines or Thumb Ring). Un-ticking any tech turns this off.",
    "blacksmith": "Blacksmith upgrades that affect this unit: attack, armor and (for archers) range.",
    "university": "University technologies that affect this unit, e.g. Chemistry (+1 attack for ranged "
                  "units) and other ballistics-type upgrades when they change combat stats.",
    "unique": "The civ's two unique technologies. Greyed out ones do not affect this unit or are not "
              "available yet in the selected age.",
    "other": "Other technologies that change this unit's combat stats (e.g. Bloodlines, Thumb Ring, "
             "regional techs).",
    "chain": "Jump to another unit of the same upgrade line (e.g. Archer → Crossbowman → Arbalester).",
    "similar": "Jump to a unit with a similar role: same unit class and/or trained in the same building.",
    "distance": "Distance between the two units, in tiles. 'Auto' puts melee fights at point-blank range "
                "and ranged duels at the shorter of the two ranges. Affects projectile flight time and "
                "whether units with a minimum range can shoot at all.",
    "charged": "Whether units with a charge mechanic (Coustillier, Urumi, Fire Lancer, Shrivamsha dodge, "
               "Iron Pagoda block...) start the fight fully charged.",
    "elevation": "Attacking from higher ground deals +25% damage; from lower ground -25%.",
    "runs": "How many randomized battles to simulate for the win-probability estimate.",
    "mode_expected": "Every projectile deals its damage multiplied by the chance to hit. Gives the average "
                     "outcome as one deterministic fight.",
    "mode_random": "Each projectile hits or misses at random (accuracy %). Repeated many times to estimate "
                   "win chances and the spread of outcomes.",
    "mode_always": "Every projectile hits. The best case for ranged units.",
    "budget": "Resources each side gets for the cost comparison. Each side buys as many units as it can "
              "afford; they then fight one at a time with survivors keeping their HP.",
    "share": "The page address always contains the current setup. Copy it from the browser's address bar "
             "to share this exact matchup.",
}

MECHANICS_MD = """
**How a hit is calculated**

* For every attack class of the attacker that the target has an armor class for:
  `max(0, attack − armor)`. Melee and pierce are just two of those classes.
* Bonus classes (everything except melee/pierce) are reduced by the target's *bonus damage resistance*
  (e.g. Sicilians).
* The sum is at least **1** per hit, then multiplied by the elevation bonus (±25%).

**Timing**

* Both units start attacking at t = 0. A new attack starts every *reload time* (rate of fire).
* Damage happens after the *attack delay* (the attack animation's release frame); projectiles then fly
  `distance / projectile speed` seconds. A unit killed before its release frame deals no damage; projectiles
  already in the air still land.
* Units die when their HP drops to 0 or below; hit points are fractional internally, just like in the game.

**Specials simulated**

* Multiple projectiles (Chu Ko Nu, Kipchak, Organ Gun...): the first uses the unit's attack, the extra
  ones use the secondary projectile's attack.
* Charge attacks (Coustillier, Samurai, Urumi): the charge is added as bonus melee damage to the next hit.
* Charged projectile volleys (Fire Lancer, Hearth Troop, Jomsviking, Bolas Rider, Xianbei Raider...).
* Projectile dodging (Shrivamsha Rider), melee-hit blocking (Iron Pagoda).
* Armor-ignoring attacks (Leitis, Composite Bowman), armor shredding (Obuch).
* Damage reflection and regeneration (Berserk, heroes, civ bonuses).

**Not simulated**

* Movement, pathing, micro, blast damage on *other* units, auras and other abilities that need more
  than two units, conversions, terrain other than elevation.
* Missed projectiles never hit (in the real game they can still occasionally land on the target).
"""
