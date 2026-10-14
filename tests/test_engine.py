"""Sanity checks against values verifiable in-game. Run: python -m pytest tests"""
import math

import pytest

from aoe.combat import EXPECTED, SimSettings, build_fighter, duel, hit_damage, solo_kill
from aoe.data import GameData
from aoe.stats import compute_stats, max_techs

DATA = GameData.load()


def stats(civ_key, uid, age=4, maxed=True):
    civ = DATA.civ_by_key(civ_key).id
    techs = max_techs(DATA, civ, uid, age, False) if maxed else set()
    return compute_stats(DATA, civ, uid, age, techs, False).unit


def fighter(civ_key, uid, age=4, maxed=True):
    civ = DATA.civ_by_key(civ_key).id
    techs = max_techs(DATA, civ, uid, age, False) if maxed else set()
    return build_fighter(compute_stats(DATA, civ, uid, age, techs, False), f"{civ_key} {uid}")


def test_britons_arbalester_fully_upgraded():
    u = stats("Britons", 492)
    assert u["attacks"][3] == 10          # 6 + 3 Blacksmith + 1 Chemistry
    assert u["range"] == 11               # 5 + 3 Blacksmith + 2 civ bonus + 1 Yeomen
    assert (u["armours"][4], u["armours"][3]) == (3, 4)
    assert u["accuracy"] == 90            # Britons lack Thumb Ring


def test_franks_paladin_hp_bonus():
    assert stats("Franks", 569)["hp"] == 192   # 160 x 1.2


def test_sicilian_bonus_damage_resistance():
    assert stats("Sicilians", 1659)["bonus_resist"] == pytest.approx(0.4)


def test_hp_rounded_like_the_game():
    assert stats("Vikings", 692, maxed=False)["hp"] == 65  # 54 x 1.2 = 64.8 -> 65


def test_minimum_damage_is_one():
    assert hit_damage({3: 1}, {3: 10, 4: 0}) == 1


def test_bonus_damage_and_resistance():
    # 10 melee vs 2 armor + 20 bonus vs class 8 at 0 armor, 40% resistance -> 8 + 12
    assert hit_damage({4: 10, 8: 20}, {4: 2, 3: 0, 8: 0}, bonus_resist=0.4) == pytest.approx(20)


def test_paladin_beats_halberdier_in_four_hits():
    pal, hal = fighter("Franks", 569), fighter("Byzantines", 359)
    s = SimSettings(mode=EXPECTED)
    r = duel(pal, hal, s)
    assert r.winner == 0
    k = solo_kill(pal, hal, s, 0)
    assert k.volleys == 4
    assert k.time == pytest.approx(3 * 1.9 + pal.delay)


def test_simultaneous_identical_units_mutually_kill():
    a, b = fighter("Franks", 77), fighter("Franks", 77)
    r = duel(a, b, SimSettings(mode=EXPECTED))
    assert r.winner == -1


def test_konnik_dismounts():
    konnik = fighter("Bulgarians", 1227)
    from aoe.constants import DISMOUNT
    civ = DATA.civ_by_key("Bulgarians").id
    konnik.respawn = build_fighter(compute_stats(DATA, civ, DISMOUNT[1227], 4, set(), False), "dismounted")
    pal = fighter("Franks", 569)
    k = solo_kill(pal, konnik, SimSettings(), 1)
    plain = solo_kill(pal, fighter("Bulgarians", 1227), SimSettings(), 1)
    assert k.time > plain.time and not math.isinf(k.time)
