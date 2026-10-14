"""Event-driven 1v1 combat simulation following AoE2's damage mechanics.

Per hit:   damage = max(1, sum_c max(0, attack_c - armor_c) for c in attacker classes present
                    in the target's armor classes)   (bonus classes scaled by the target's
                    bonus damage resistance) x elevation multiplier
Timing:    an attack starts every `reload` seconds (the first one at t=0); damage is dealt
           `attack_delay` seconds after the attack starts (projectiles are released then and
           need distance / projectile speed seconds to land).  If the attacker dies before the
           release frame, the attack is cancelled; projectiles already in flight still land.
Specials:  multiple projectiles (secondary projectiles), charge attacks, charged projectile
           volleys, projectile dodging, melee-blocking charge, armor ignoring, armor shredding,
           damage reflection, regeneration.
"""
from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field

from .constants import (ABILITY_IGNORE_ARMOR, ABILITY_RESIST_IGNORE, ABILITY_SHRED_ARMOR, BASE_CLASSES,
                        CHARGE_AREA_ATTACK, CHARGE_ATTACK, CHARGE_DODGE, CHARGE_IGNORE_MELEE,
                        CHARGE_MIXED_VOLLEY, CHARGE_PROJECTILES, MELEE, PIERCE, UNSIMULATED_ABILITIES)
from .stats import UnitStats

EXPECTED, RANDOM, ALWAYS_HIT = "expected", "random", "always_hit"
MAX_TIME = 1800.0
EPS = 1e-9
ELEVATION = {"higher": 1.25, "level": 1.0, "lower": 0.75}


# --------------------------------------------------------------------------- damage

def hit_damage(attacks: dict[int, float], armours: dict[int, float], *, bonus_resist: float = 0.0,
               ignore_armor: bool = False, shred: float = 0.0, multiplier: float = 1.0) -> float:
    base = bonus = 0.0
    for cls, atk in attacks.items():
        if cls not in armours:
            continue
        arm = armours[cls]
        if cls in BASE_CLASSES:
            if ignore_armor:
                arm = 0
            elif shred:
                arm = max(0.0, arm - shred) if arm > 0 else arm
            base += max(0.0, atk - arm)
        else:
            bonus += max(0.0, atk - arm)
    total = base + bonus * (1.0 - bonus_resist)
    return max(1.0, total) * multiplier


def damage_breakdown(attacks: dict[int, float], armours: dict[int, float], bonus_resist: float = 0.0,
                     ignore_armor: bool = False) -> list[dict]:
    rows = []
    for cls, atk in sorted(attacks.items(), key=lambda kv: (kv[0] not in BASE_CLASSES, kv[0])):
        if cls not in armours:
            continue
        arm = 0 if (ignore_armor and cls in BASE_CLASSES) else armours[cls]
        net = max(0.0, atk - arm)
        if cls not in BASE_CLASSES:
            net *= (1.0 - bonus_resist)
        if atk == 0 and net == 0:
            continue
        rows.append({"class": cls, "attack": atk, "armor": arm, "net": net})
    return rows


# --------------------------------------------------------------------------- fighters

@dataclass
class Volley:
    attacks: dict[int, float]
    speed: float          # projectile speed (tiles/s); 0 = melee / instant
    is_projectile: bool
    count: float = 1.0    # number of identical projectiles


@dataclass
class Fighter:
    label: str
    hp: float
    attacks: dict[int, float]
    armours: dict[int, float]
    reload: float
    delay: float
    accuracy: float               # 0..1
    range: float
    min_range: float
    ranged: bool
    proj_speed: float
    n_projectiles: int
    secondary_attacks: dict[int, float] | None
    secondary_speed: float
    charge_type: int
    charge_max: float
    charge_rate: float
    charge_attacks: dict[int, float] | None
    charge_speed: float
    charge_count: int
    ability: int
    bonus_resist: float
    reflection: float
    regen_per_s: float
    cost: dict[str, float]
    notes: list[str] = field(default_factory=list)
    respawn: "Fighter | None" = None   # unit that takes over when this one dies (Konnik)

    @property
    def total_hp(self) -> float:
        return self.hp + (self.respawn.total_hp if self.respawn else 0.0)

    @property
    def can_ignore_armor(self) -> bool:
        return bool(self.ability & ABILITY_IGNORE_ARMOR)

    def normal_volleys(self, charge_bonus: float = 0.0) -> list[Volley]:
        atk = dict(self.attacks)
        if charge_bonus:
            atk[MELEE] = atk.get(MELEE, 0) + charge_bonus
        if not self.ranged:
            return [Volley(atk, 0.0, False)]
        out = [Volley(atk, self.proj_speed, True)]
        extra = self.n_projectiles - 1
        if extra > 0:
            sec = self.secondary_attacks if self.secondary_attacks is not None else self.attacks
            out.append(Volley(sec, self.secondary_speed or self.proj_speed, True, extra))
        return out

    def charged_volleys(self) -> list[Volley]:
        if self.charge_type == CHARGE_PROJECTILES and self.charge_attacks is not None:
            return [Volley(self.charge_attacks, self.charge_speed, True, max(1, self.charge_count))]
        if self.charge_type == CHARGE_MIXED_VOLLEY and self.charge_attacks is not None:
            out = [Volley(self.charge_attacks, self.charge_speed, True)]
            extra = max(0, self.charge_count - 1)
            if extra:
                sec = self.secondary_attacks if self.secondary_attacks is not None else self.attacks
                out.append(Volley(sec, self.secondary_speed or self.proj_speed, True, extra))
            return out
        return self.normal_volleys()


def build_fighter(stats: UnitStats, label: str) -> Fighter:
    u = stats.unit
    notes: list[str] = []
    proj = stats.projectile("projectile")
    sec = stats.projectile("secondary_projectile")
    chg = stats.projectile("charge_projectile")
    ranged = proj is not None and u.get("range", 0) > 0 and (u.get("total_projectiles", 1) or 0) > 0
    n_proj = int(round(u.get("total_projectiles", 1) or 1)) if ranged else 1
    charge_type = int(u.get("charge_type", 0) or 0)
    charge_max = float(u.get("charge_max", 0) or 0)
    charge_rate = float(u.get("charge_recharge", 0) or 0)
    if charge_type < 0:
        notes.append("Has an activated ability (transform/aura/conversion) that is not simulated.")
        charge_type = 0
    if charge_type in (CHARGE_PROJECTILES, CHARGE_MIXED_VOLLEY) and chg is None:
        charge_type = 0
    ability = int(u.get("combat_ability", 0) or 0)
    for bit, text in UNSIMULATED_ABILITIES.items():
        if ability & bit:
            notes.append(f"Has a {text}, which needs other units nearby - not simulated in a 1v1.")
    if u.get("blast_width", 0) and u.get("blast_width", 0) > 0:
        notes.append("Deals area damage; only the primary target matters in a 1v1.")
    regen = float(u.get("regen", 0) or 0) / 60.0 + float(u.get("regen_pct", 0) or 0) * u["hp"] / 100.0 / 60.0
    reflection = float(u.get("reflection", 0) or 0)
    if reflection > 1:
        reflection /= 100.0
    return Fighter(
        label=label,
        hp=float(u["hp"]),
        attacks={k: float(v) for k, v in u["attacks"].items()},
        armours={k: float(v) for k, v in u["armours"].items()},
        reload=max(0.05, float(u.get("reload", 2.0) or 2.0)),
        delay=float(u.get("attack_delay", 0) or 0),
        accuracy=max(0.0, min(1.0, float(u.get("accuracy", 100)) / 100.0)) if ranged else 1.0,
        range=float(u.get("range", 0) or 0),
        min_range=float(u.get("min_range", 0) or 0),
        ranged=ranged,
        proj_speed=float(proj["speed"]) if proj else 0.0,
        n_projectiles=max(1, n_proj),
        secondary_attacks={k: float(v) for k, v in sec["attacks"].items()} if sec else None,
        secondary_speed=float(sec["speed"]) if sec else 0.0,
        charge_type=charge_type,
        charge_max=charge_max,
        charge_rate=charge_rate,
        charge_attacks={k: float(v) for k, v in chg["attacks"].items()} if chg else None,
        charge_speed=float(chg["speed"]) if chg else 0.0,
        charge_count=int(u.get("max_total_projectiles", 1) or 1),
        ability=ability,
        bonus_resist=float(u.get("bonus_resist", 0) or 0),
        reflection=reflection,
        regen_per_s=regen,
        cost={k: round(v) for k, v in u.get("cost", {}).items()},
        notes=notes,
    )


# --------------------------------------------------------------------------- simulation

@dataclass
class SimSettings:
    mode: str = EXPECTED
    distance: float | None = None        # None = auto
    start_charged: bool = True
    elevation: tuple[str, str] = ("level", "level")
    runs: int = 1000
    seed: int = 12345


@dataclass
class HitRecord:
    t: float
    attacker: int
    damage: float
    hp_after: float
    volley: int


@dataclass
class DuelResult:
    winner: int | None          # 0 / 1, None = nobody died, -1 = both died
    duration: float
    hp_left: tuple[float, float]
    volleys_landed: tuple[int, int]
    hits: list[HitRecord]
    timeline: list[tuple[float, float, float]]
    can_attack: tuple[bool, bool]
    distance: float
    end_fighters: tuple["Fighter", "Fighter"] | None = None   # form each side ended in (dismounts)


def auto_distance(a: Fighter, b: Fighter) -> float:
    if not a.ranged or not b.ranged:
        return 0.0
    return min(a.range, b.range)


def can_reach(f: Fighter, distance: float) -> bool:
    if f.min_range > 0 and distance < f.min_range - EPS:
        return False
    reach = f.range if f.ranged else max(f.range, 1.0)
    return distance <= reach + EPS


class _State:
    __slots__ = ("f", "hp", "charge", "charge_t", "shred", "alive", "death_t", "volleys", "volley_hit",
                 "last_regen", "gen", "lives")

    def __init__(self, f: Fighter, start_charged: bool):
        self.f = f
        self.hp = f.hp
        self.charge = f.charge_max if start_charged else 0.0
        self.charge_t = 0.0
        self.shred = 0.0
        self.alive = True
        self.death_t = None
        self.volleys = 0
        self.volley_hit: set[int] = set()
        self.last_regen = 0.0
        self.gen = 0      # bumps when the unit is replaced (dismount); stale attack events are dropped
        self.lives = 1

    def update_charge(self, t: float) -> None:
        if self.f.charge_max > 0 and self.f.charge_rate > 0:
            self.charge = min(self.f.charge_max, self.charge + self.f.charge_rate * (t - self.charge_t))
        self.charge_t = t

    def regen_to(self, t: float) -> None:
        if self.alive and self.f.regen_per_s > 0 and self.hp < self.f.hp:
            self.hp = min(self.f.hp, self.hp + self.f.regen_per_s * (t - self.last_regen))
        self.last_regen = t


def duel(a: Fighter, b: Fighter, settings: SimSettings, *, rng: random.Random | None = None,
         passive: tuple[bool, bool] = (False, False), record: bool = True,
         start_hp: tuple[float | None, float | None] = (None, None)) -> DuelResult:
    mode = settings.mode
    rng = rng or random.Random(settings.seed)
    distance = settings.distance if settings.distance is not None else auto_distance(a, b)
    fighters = (a, b)
    states = [_State(a, settings.start_charged), _State(b, settings.start_charged)]
    for i, hp in enumerate(start_hp):
        if hp is not None:
            states[i].hp = hp
    mult = (ELEVATION[settings.elevation[0]], ELEVATION[settings.elevation[1]])
    reach = (can_reach(a, distance) and not passive[0], can_reach(b, distance) and not passive[1])

    seq = 0
    heap: list[tuple[float, int, int, str, int, object]] = []

    order = {"attack": 0, "release": 1, "impact": 2}  # same-time events: both sides act symmetrically

    def push(t, kind, who, payload):
        nonlocal seq
        heapq.heappush(heap, (t, order[kind], seq, kind, who, payload))
        seq += 1

    for i in (0, 1):
        if reach[i]:
            push(0.0, "attack", i, 0)

    def lethal(idx: int, when: float) -> None:
        """HP dropped to 0: either the unit dies or (Konnik) its next form takes over."""
        s = states[idx]
        if s.f.respawn is not None:
            s.f = s.f.respawn
            s.hp = s.f.hp
            s.charge, s.charge_t, s.shred = 0.0, when, 0.0
            s.gen += 1
            s.lives += 1
            if can_reach(s.f, distance) and not passive[idx]:
                push(when, "attack", idx, s.gen)
            return
        s.alive, s.death_t = False, when

    hits: list[HitRecord] = []
    timeline = [(0.0, states[0].hp, states[1].hp)]
    t = 0.0

    while heap:
        t, _, _, kind, who, payload = heapq.heappop(heap)
        if t > MAX_TIME:
            break
        me, other = states[who], states[1 - who]
        for s in states:
            s.regen_to(t)

        if kind == "attack":
            if not me.alive or not other.alive or payload != me.gen:
                continue
            f = me.f
            me.update_charge(t)
            charged = f.charge_max > 0 and me.charge >= f.charge_max - EPS
            if charged and f.charge_type in (CHARGE_ATTACK, CHARGE_AREA_ATTACK):
                volleys = f.normal_volleys(charge_bonus=me.charge)
                me.charge = 0.0
            elif charged and f.charge_type in (CHARGE_PROJECTILES, CHARGE_MIXED_VOLLEY):
                volleys = f.charged_volleys()
                me.charge = 0.0
            else:
                volleys = f.normal_volleys()
            me.volleys += 1
            push(t + f.delay, "release", who, (me.volleys, volleys, me.gen))
            push(t + f.reload, "attack", who, me.gen)

        elif kind == "release":
            vidx, volleys, gen = payload
            if not me.alive or gen != me.gen:
                continue  # killed before the attack frame
            for v in volleys:
                flight = distance / v.speed if (v.is_projectile and v.speed > 0) else 0.0
                push(t + flight, "impact", who, (vidx, v))

        elif kind == "impact":
            if not other.alive:
                continue
            vidx, v = payload
            f, tgt = me.f, other.f
            # chance to hit
            p = f.accuracy if v.is_projectile else 1.0
            count = v.count
            if mode == RANDOM and v.is_projectile:
                n_hit = sum(1 for _ in range(int(round(count))) if rng.random() < p)
                eff_hits = float(n_hit)
            elif mode == ALWAYS_HIT:
                eff_hits = count
            else:
                eff_hits = count * p
            if eff_hits <= 0:
                continue
            # target's defensive charges
            other.update_charge(t)
            if tgt.charge_type == CHARGE_DODGE and v.is_projectile and other.charge > EPS:
                dodged = min(other.charge, eff_hits)
                other.charge -= dodged
                eff_hits -= dodged
                if eff_hits <= EPS:
                    continue
            if tgt.charge_type == CHARGE_IGNORE_MELEE and not v.is_projectile \
                    and other.charge >= tgt.charge_max - EPS and tgt.charge_max > 0:
                other.charge = 0.0
                continue
            ignore = f.can_ignore_armor and not (tgt.ability & ABILITY_RESIST_IGNORE)
            dmg_each = hit_damage(v.attacks, tgt.armours, bonus_resist=tgt.bonus_resist,
                                  ignore_armor=ignore, shred=other.shred, multiplier=mult[who])
            dmg = dmg_each * eff_hits
            other.hp -= dmg
            if f.ability & ABILITY_SHRED_ARMOR:
                other.shred += min(1.0, eff_hits)
            if not v.is_projectile and tgt.reflection > 0:
                me.hp -= dmg * tgt.reflection
                if me.hp <= EPS and me.alive:
                    lethal(who, t)
            me.volley_hit.add(vidx)
            if record:
                hits.append(HitRecord(t, who, dmg, other.hp, vidx))
                timeline.append((t, states[0].hp, states[1].hp))
            if other.hp <= EPS:
                # same-timestamp impacts from the dying unit are still processed
                lethal(1 - who, t)
            if not me.alive and not other.alive:
                break
        # stop early once someone is dead and nothing from the survivor's victim is in flight
        if not states[0].alive or not states[1].alive:
            dead = 0 if not states[0].alive else 1
            alive = 1 - dead
            pending = any(e[4] == dead and e[3] in ("impact",) for e in heap)
            if not pending or not states[alive].alive:
                break

    dead = [not s.alive for s in states]
    if dead[0] and dead[1]:
        winner = -1
    elif dead[0]:
        winner = 1
    elif dead[1]:
        winner = 0
    else:
        winner = None
    end_t = max((s.death_t or 0.0) for s in states) if any(dead) else t
    for s in states:
        s.regen_to(end_t)
    timeline.append((end_t, max(0.0, states[0].hp), max(0.0, states[1].hp)))
    return DuelResult(
        winner=winner, duration=end_t,
        hp_left=(max(0.0, states[0].hp), max(0.0, states[1].hp)),
        volleys_landed=(len(states[0].volley_hit), len(states[1].volley_hit)),
        hits=hits, timeline=timeline, can_attack=reach, distance=distance,
        end_fighters=(states[0].f, states[1].f),
    )


@dataclass
class SoloKill:
    possible: bool
    volleys: float
    time: float
    first_hit_damage: float


def solo_kill(attacker: Fighter, target: Fighter, settings: SimSettings, attacker_idx: int = 0) -> SoloKill:
    """How long would `attacker` need to kill an un-resisting `target` (target still uses
    its defensive abilities and regeneration)."""
    pair = (attacker, target) if attacker_idx == 0 else (target, attacker)
    passive = (False, True) if attacker_idx == 0 else (True, False)
    elev = settings.elevation if attacker_idx == 0 else settings.elevation
    s = SimSettings(mode=settings.mode, distance=settings.distance, start_charged=settings.start_charged,
                    elevation=elev, runs=settings.runs, seed=settings.seed)
    if settings.mode == RANDOM:
        rng = random.Random(settings.seed + 7)
        times, volleys, ok = [], [], True
        for _ in range(max(1, settings.runs // 4)):
            r = duel(*pair, s, rng=rng, passive=passive, record=False)
            if r.winner != attacker_idx:
                ok = False
                break
            times.append(r.duration)
            volleys.append(r.volleys_landed[attacker_idx])
        if not ok:
            return SoloKill(False, math.inf, math.inf, 0.0)
        return SoloKill(True, sum(volleys) / len(volleys), sum(times) / len(times),
                        _first_hit(attacker, target, settings, attacker_idx))
    r = duel(*pair, s, passive=passive, record=False)
    if r.winner != attacker_idx:
        return SoloKill(False, math.inf, math.inf, _first_hit(attacker, target, settings, attacker_idx))
    return SoloKill(True, r.volleys_landed[attacker_idx], r.duration,
                    _first_hit(attacker, target, settings, attacker_idx))


def _first_hit(attacker: Fighter, target: Fighter, settings: SimSettings, idx: int) -> float:
    ignore = attacker.can_ignore_armor and not (target.ability & ABILITY_RESIST_IGNORE)
    total = 0.0
    for v in attacker.normal_volleys():
        total += hit_damage(v.attacks, target.armours, bonus_resist=target.bonus_resist,
                            ignore_armor=ignore, multiplier=ELEVATION[settings.elevation[idx]]) * v.count
    return total


@dataclass
class MonteCarlo:
    runs: int
    win_rate: tuple[float, float]
    draw_rate: float
    avg_hp_left: tuple[float, float]
    durations: list[float]
    winners: list[int | None]
    hp_left_samples: tuple[list[float], list[float]]


def monte_carlo(a: Fighter, b: Fighter, settings: SimSettings) -> MonteCarlo:
    rng = random.Random(settings.seed)
    s = SimSettings(mode=RANDOM, distance=settings.distance, start_charged=settings.start_charged,
                    elevation=settings.elevation, runs=settings.runs, seed=settings.seed)
    wins = [0, 0]
    draws = 0
    hp = ([], [])
    durations, winners = [], []
    for _ in range(settings.runs):
        r = duel(a, b, s, rng=rng, record=False)
        winners.append(r.winner)
        durations.append(r.duration)
        if r.winner in (0, 1):
            wins[r.winner] += 1
            hp[r.winner].append(r.hp_left[r.winner])
        else:
            draws += 1
    n = max(1, settings.runs)
    return MonteCarlo(
        runs=settings.runs,
        win_rate=(wins[0] / n, wins[1] / n),
        draw_rate=draws / n,
        avg_hp_left=(sum(hp[0]) / len(hp[0]) if hp[0] else 0.0, sum(hp[1]) / len(hp[1]) if hp[1] else 0.0),
        durations=durations,
        winners=winners,
        hp_left_samples=hp,
    )


# --------------------------------------------------------------------------- cost efficiency

def total_cost(f: Fighter) -> float:
    return float(sum(f.cost.values())) or 1.0


def gauntlet(a: Fighter, b: Fighter, n_a: int, n_b: int, settings: SimSettings,
             max_fights: int = 400) -> dict:
    """Equal-resources gauntlet: units fight one at a time, survivors keep their HP."""
    hp_a: float | None = None
    hp_b: float | None = None
    cur_a, cur_b = a, b          # current form of the front unit (Konnik may have dismounted)
    left_a, left_b = n_a, n_b
    fights = 0
    rng = random.Random(settings.seed)
    while left_a > 0 and left_b > 0 and fights < max_fights:
        r = duel(cur_a, cur_b, settings, rng=rng, record=False, start_hp=(hp_a, hp_b))
        fights += 1
        if r.winner == 0:
            left_b -= 1
            hp_a, hp_b = r.hp_left[0], None
            cur_a, cur_b = r.end_fighters[0], b
        elif r.winner == 1:
            left_a -= 1
            hp_a, hp_b = None, r.hp_left[1]
            cur_a, cur_b = a, r.end_fighters[1]
        elif r.winner == -1:
            left_a -= 1
            left_b -= 1
            hp_a = hp_b = None
            cur_a, cur_b = a, b
        else:
            break
    return {"left": (left_a, left_b), "fights": fights,
            "hp_of_front": (hp_a if hp_a is not None else (a.hp if left_a else 0),
                            hp_b if hp_b is not None else (b.hp if left_b else 0))}


def lanchester_ratio(a: Fighter, b: Fighter, settings: SimSettings) -> float | None:
    """How many B units are worth one A unit in a mass fight (Lanchester's square law).

    Strength of a side ~ N^2 * (dps vs enemy) * (hp). Equal strength when
    N_b / N_a = sqrt((dps_a * hp_a) / (dps_b * hp_b)).
    """
    ka = solo_kill(a, b, settings, 0)
    kb = solo_kill(b, a, settings, 1)
    if not ka.possible and not kb.possible:
        return None
    if not kb.possible:
        return math.inf
    if not ka.possible:
        return 0.0
    dps_a = b.total_hp / ka.time if ka.time > 0 else math.inf
    dps_b = a.total_hp / kb.time if kb.time > 0 else math.inf
    return math.sqrt((dps_a * a.total_hp) / (dps_b * b.total_hp))
