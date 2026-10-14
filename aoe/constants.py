"""Static lookup tables for the Genie engine (AoE2 DE)."""

AGES = {1: "Dark Age", 2: "Feudal Age", 3: "Castle Age", 4: "Imperial Age"}
AGE_TECHS = {1: 104, 2: 101, 3: 102, 4: 103}

MELEE, PIERCE = 4, 3
BASE_CLASSES = (MELEE, PIERCE)

# Armor classes (bonus damage classes). Source: Fandom "Armor class (Age of Empires II)".
ARMOR_CLASS_NAMES = {
    0: "Wonders", 1: "Infantry", 2: "Heavy Warships", 3: "Pierce", 4: "Melee",
    5: "Elephants", 8: "Cavalry", 11: "All Buildings", 13: "Stone Defense",
    14: "Predator Animals", 15: "Archers", 16: "Ships", 17: "Rams", 18: "Trees",
    19: "Unique Units", 20: "Siege Weapons", 21: "Standard Buildings", 22: "Walls & Gates",
    23: "Gunpowder Units", 24: "Boars", 25: "Monks", 26: "Castles", 27: "Spearmen",
    28: "Cavalry Archers", 29: "Eagle Warriors / Shock Infantry", 30: "Camels",
    31: "Armor-ignoring (legacy)", 32: "Condottieri", 33: "Unused", 34: "Fishing Ships",
    35: "Mamelukes", 36: "Heroes & Kings", 37: "Heavy Siege", 38: "Skirmishers",
    39: "Royal Heirs / Mounted", 40: "Houses", 41: "Fire Ships", 60: "Long-Range Warships",
}

# Unit classes (Genie "class" field), used by effects and for "similar units".
UNIT_CLASS_NAMES = {
    0: "Archer", 2: "Trade Boat", 4: "Civilian", 6: "Infantry", 12: "Cavalry",
    13: "Siege Weapon", 18: "Monk", 19: "Trade Cart", 20: "Transport Ship",
    21: "Fishing Boat", 22: "Warship", 23: "Conquistador / Gunpowder Cavalry",
    24: "War Elephant", 25: "Hero", 26: "Elephant Archer", 35: "Petard",
    36: "Cavalry Archer", 43: "Monk with Relic", 44: "Hand Cannoneer",
    45: "Two-Handed Swordsman", 46: "Pikeman", 47: "Scout Cavalry", 51: "Packed Siege",
    53: "Boarding Ship", 54: "Unpacked Siege", 55: "Scorpion", 56: "Raider",
    57: "Cavalry Raider", 59: "King",
}

# Units that cannot be meaningfully simulated in a 1v1 duel.
EXCLUDED_UNITS = {
    125, 775, 1811,          # monks / missionary / warrior priest (conversion & healing)
    83, 13, 17, 128, 545,    # villager, fishing ship, trade cog, trade cart, transport
    440, 1263, 1104, 527, 528,  # petard, flaming camel, demolition raft/ships (suicide)
    1105, 331,               # siege tower, packed trebuchet
}

# Charge types (attribute 62)
CHARGE_ATTACK = 1        # bonus damage on the next attack (Coustillier, Samurai)
CHARGE_AREA_ATTACK = 3   # same, dealt as area damage (Urumi) - single target here
CHARGE_DODGE = 4         # dodge incoming projectiles (Shrivamsha)
CHARGE_IGNORE_MELEE = 5  # block the next melee hit (Iron Pagoda)
CHARGE_PROJECTILES = 6   # fire charge projectiles instead of the normal attack
CHARGE_MIXED_VOLLEY = 7  # 1 charge projectile + secondary projectiles

# Combat ability bit field (attribute 63)
ABILITY_IGNORE_ARMOR = 1
ABILITY_RESIST_IGNORE = 2
ABILITY_SHRED_ARMOR = 4

# Abilities that exist in the game but are outside the scope of a 1v1 duel.
UNSIMULATED_ABILITIES = {
    32: "aura ability",
    64: "self-boosting aura (scales with nearby units)",
    128: "multi-target 'stringer' attack",
}

RESOURCES = ("food", "wood", "gold", "stone")

# Units that turn into another unit when killed (fight continues with the new unit at full HP).
DISMOUNT = {1225: 1252, 1254: 1252, 1227: 1253, 1255: 1253}  # Konnik -> dismounted Konnik
