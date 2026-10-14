"""AoE2 DE unit duel calculator - Streamlit entry point."""
from __future__ import annotations

import math

import plotly.graph_objects as go
import streamlit as st
from streamlit_searchbox import st_searchbox

from aoe.combat import (ALWAYS_HIT, EXPECTED, RANDOM, SimSettings, build_fighter, damage_breakdown, duel,
                        gauntlet, lanchester_ratio, monte_carlo, solo_kill, total_cost)
from aoe.constants import AGES, ARMOR_CLASS_NAMES, BASE_CLASSES, DISMOUNT, MELEE, PIERCE, UNIT_CLASS_NAMES
from aoe.data import GameData
from aoe.search import rank_matches
from aoe.stats import compute_stats, max_techs
from aoe.ui_text import HELP, MECHANICS_MD
from aoe import updater

st.set_page_config(page_title="AoE2 Duel Calculator", page_icon="⚔️", layout="wide")

SIDES = ("L", "R")
SIDE_NAME = {"L": "Left", "R": "Right"}
SIDE_COLOR = {"L": "#3b82f6", "R": "#ef4444"}
DEFAULTS = {"L": ("Franks", 569), "R": ("Byzantines", 359)}

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.6rem; padding-bottom: 3rem;}
    .side-head {border-radius: 10px; padding: .45rem .8rem; margin-bottom: .6rem; color: white;
                font-weight: 700; font-size: 1.05rem; letter-spacing: .02em;}
    .verdict {border-radius: 14px; padding: 1rem 1.3rem; margin: .4rem 0 1rem 0;
              border: 1px solid rgba(128,128,128,.25);}
    .verdict .vtitle {margin: 0 0 .2rem 0; font-size: 1.6rem; font-weight: 700;}
    .verdict .sub {opacity: .85; font-size: 1rem;}
    .hpbar {height: 12px; border-radius: 6px; background: rgba(128,128,128,.25); overflow: hidden; margin-top:.5rem;}
    .hpbar > div {height: 100%;}
    .chip {display:inline-block; border-radius: 999px; padding: .15rem .65rem; margin: .15rem .3rem .15rem 0;
           font-size: .85rem; border: 1px solid rgba(128,128,128,.35);}
    .muted {opacity: .7; font-size: .85rem;}
    div[data-testid="stMetricValue"] {font-size: 1.45rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# =============================================================================== data

@st.cache_resource(show_spinner="Loading game data…")
def load_data(version: str) -> GameData:  # version busts the cache after an update
    return GameData.load()


def data_version() -> str:
    from aoe.data import DATA_FILE
    return str(DATA_FILE.stat().st_mtime)


DATA = load_data(data_version())


# =============================================================================== state

def init_state() -> None:
    if st.session_state.get("_init"):
        return
    qp = st.query_params
    for s in SIDES:
        civ_key, uid = DEFAULTS[s]
        civ = DATA.civ_by_key(civ_key)
        st.session_state[f"{s}_civ"] = civ.id
        st.session_state[f"{s}_full"] = False
        st.session_state[f"{s}_unit"] = uid
        st.session_state[f"{s}_age"] = 4
        st.session_state[f"{s}_max"] = True
        st.session_state[f"{s}_techs"] = set()
        st.session_state[f"{s}_sbv"] = 0
        if s in qp:
            _load_side_param(s, qp[s])
    st.session_state["distance_auto"] = qp.get("dist", "auto") == "auto"
    try:
        st.session_state["distance"] = float(qp.get("dist", "0")) if qp.get("dist", "auto") != "auto" else 0.0
    except ValueError:
        st.session_state["distance"] = 0.0
    st.session_state["charged"] = qp.get("charged", "1") == "1"
    st.session_state["elev"] = qp.get("elev", "level") if qp.get("elev") in ("left", "level", "right") else "level"
    st.session_state["runs"] = int(qp.get("runs", "1000")) if qp.get("runs", "1000").isdigit() else 1000
    st.session_state["_init"] = True


def _load_side_param(s: str, raw: str) -> None:
    try:
        civ_key, uid, age, full, mx, techs = (raw.split("~") + [""] * 6)[:6]
        civ = DATA.civ_by_key(civ_key)
        if civ is None or int(uid) not in DATA.units:
            return
        st.session_state[f"{s}_civ"] = civ.id
        st.session_state[f"{s}_unit"] = int(uid)
        st.session_state[f"{s}_age"] = min(4, max(1, int(age)))
        st.session_state[f"{s}_full"] = full == "1"
        st.session_state[f"{s}_max"] = mx == "1"
        st.session_state[f"{s}_techs"] = {int(t) for t in techs.split("-") if t.isdigit()}
    except (ValueError, TypeError):
        pass


def sync_query_params() -> None:
    params = {}
    for s in SIDES:
        civ = DATA.civs[st.session_state[f"{s}_civ"]]
        techs = "-".join(str(t) for t in sorted(st.session_state[f"{s}_techs"])) if not st.session_state[f"{s}_max"] else ""
        params[s] = "~".join([civ.key, str(st.session_state[f"{s}_unit"]), str(st.session_state[f"{s}_age"]),
                              "1" if st.session_state[f"{s}_full"] else "0",
                              "1" if st.session_state[f"{s}_max"] else "0", techs])
    params["dist"] = "auto" if st.session_state["distance_auto"] else f"{st.session_state['distance']:g}"
    params["charged"] = "1" if st.session_state["charged"] else "0"
    params["elev"] = st.session_state["elev"]
    params["runs"] = str(st.session_state["runs"])
    if dict(st.query_params) != params:
        st.query_params.from_dict(params)


def set_unit(s: str, uid: int) -> None:
    st.session_state[f"{s}_unit"] = uid
    min_age = DATA.unit_min_age(st.session_state[f"{s}_civ"], uid)
    if st.session_state[f"{s}_age"] < min_age:
        st.session_state[f"{s}_age"] = min_age
    st.session_state[f"{s}_sbv"] += 1  # remount the search box so it shows the new unit


def ensure_valid_unit(s: str) -> None:
    civ_id = st.session_state[f"{s}_civ"]
    full = st.session_state[f"{s}_full"]
    uid = st.session_state[f"{s}_unit"]
    available = DATA.unit_ids_for_civ(civ_id, full)
    if uid in available:
        return
    name = DATA.units[uid]["name"] if uid in DATA.units else ""
    same_name = [u for u in available if DATA.units[u]["name"] == name]
    if same_name:
        set_unit(s, same_name[0])
        return
    cls = DATA.units[uid]["class"] if uid in DATA.units else None
    same_class = [u for u in available if DATA.units[u]["class"] == cls]
    set_unit(s, (same_class or available)[0])


def swap_sides() -> None:
    keys = ["civ", "full", "unit", "age", "max", "techs"]
    for k in keys:
        st.session_state[f"L_{k}"], st.session_state[f"R_{k}"] = st.session_state[f"R_{k}"], st.session_state[f"L_{k}"]
    for s in SIDES:
        st.session_state[f"{s}_sbv"] += 1
    elev = st.session_state["elev"]
    st.session_state["elev"] = {"left": "right", "right": "left"}.get(elev, elev)


# =============================================================================== side panel

def tech_checkbox(s: str, entry: dict, selected: set[int]) -> None:
    tid = entry["id"]
    key = f"{s}_t_{tid}"
    enabled = entry["applies"] and entry["available"]
    st.session_state[key] = enabled and tid in selected
    label = entry["name"]
    if not entry["available"]:
        label += f"  ·  {AGES.get(entry['age'], '')}"
    help_text = entry["help"] or entry["name"]
    if not entry["applies"]:
        help_text = "Does not affect this unit.\n\n" + help_text
    elif not entry["available"]:
        help_text = f"Not available yet (needs the {AGES.get(entry['age'], '?')}).\n\n" + help_text
    st.checkbox(label, key=key, disabled=not enabled, help=help_text, on_change=_on_tech_toggle, args=(s, tid))


def _on_tech_toggle(s: str, tid: int) -> None:
    techs = set(st.session_state[f"{s}_techs"])
    if st.session_state[f"{s}_t_{tid}"]:
        techs.add(tid)
    else:
        techs.discard(tid)
        st.session_state[f"{s}_max"] = False
    st.session_state[f"{s}_techs"] = techs


def _on_max_toggle(s: str) -> None:
    on = st.session_state[f"{s}_max_w"]
    st.session_state[f"{s}_max"] = on
    if not on:
        st.session_state[f"{s}_techs"] = set()


def _on_civ_change(s: str) -> None:
    st.session_state[f"{s}_civ"] = st.session_state[f"{s}_civ_w"]
    ensure_valid_unit(s)
    st.session_state[f"{s}_sbv"] += 1


def _on_full_change(s: str) -> None:
    st.session_state[f"{s}_full"] = st.session_state[f"{s}_full_w"]
    ensure_valid_unit(s)


def _on_age_change(s: str, key: str) -> None:
    if st.session_state.get(key) is not None:
        st.session_state[f"{s}_age"] = st.session_state[key]


def _on_jump(s: str, key: str) -> None:
    uid = st.session_state.get(key)
    if uid is not None and uid != st.session_state[f"{s}_unit"]:
        set_unit(s, uid)


def side_panel(s: str) -> dict:
    civ_id = st.session_state[f"{s}_civ"]
    color = SIDE_COLOR[s]
    st.markdown(f"<div class='side-head' style='background:{color}'>{SIDE_NAME[s]} side</div>",
                unsafe_allow_html=True)

    civ_ids = [c.id for c in DATA.civ_list]
    st.session_state[f"{s}_civ_w"] = civ_id
    st.selectbox("Civilization", civ_ids, key=f"{s}_civ_w", format_func=lambda c: DATA.civs[c].name,
                 help=HELP["civ"], on_change=_on_civ_change, args=(s,), filter_mode="contains")
    with st.expander("Civilization bonuses", expanded=False):
        st.caption(DATA.civs[civ_id].help or "—")

    st.session_state[f"{s}_full_w"] = st.session_state[f"{s}_full"]
    st.toggle("Full tech tree", key=f"{s}_full_w", help=HELP["full_tree"], on_change=_on_full_change, args=(s,))
    full = st.session_state[f"{s}_full"]
    ensure_valid_unit(s)

    # ---- unit search box
    available = DATA.unit_ids_for_civ(civ_id, full)
    label_to_uid = {DATA.label(u): u for u in available}
    labels = list(label_to_uid)
    uid = st.session_state[f"{s}_unit"]

    def search(term: str) -> list[str]:
        return rank_matches(term, labels)

    picked = st_searchbox(
        search,
        label="Unit",
        placeholder=DATA.label(uid) + "  (type to search)",
        default=None,
        default_options=labels,
        key=f"{s}_unit_sb_{civ_id}_{int(full)}_{st.session_state[f'{s}_sbv']}",
        help=HELP["unit"],
        edit_after_submit="option",
        style_absolute=True,
        rerun_scope="app",
    )
    if picked and picked in label_to_uid and label_to_uid[picked] != uid:
        st.session_state[f"{s}_unit"] = label_to_uid[picked]
        min_age = DATA.unit_min_age(civ_id, label_to_uid[picked])
        if st.session_state[f"{s}_age"] < min_age:
            st.session_state[f"{s}_age"] = min_age
        st.rerun()
    st.markdown("<div style='height:.35rem'></div>", unsafe_allow_html=True)

    # ---- age
    min_age = DATA.unit_min_age(civ_id, uid)
    ages = [a for a in AGES if a >= min_age]
    if st.session_state[f"{s}_age"] not in ages:
        st.session_state[f"{s}_age"] = ages[0]
    age_key = f"{s}_age_w_{min_age}"
    st.session_state[age_key] = st.session_state[f"{s}_age"]
    st.segmented_control("Age", ages, key=age_key, format_func=lambda a: AGES[a].replace(" Age", ""),
                         help=HELP["age"], on_change=_on_age_change, args=(s, age_key), width="stretch")
    age = st.session_state[f"{s}_age"]

    # ---- techs
    groups = DATA.techs_for_unit(civ_id, uid, age, full)
    if st.session_state[f"{s}_max"]:
        st.session_state[f"{s}_techs"] = max_techs(DATA, civ_id, uid, age, full)
    selected = st.session_state[f"{s}_techs"]
    st.session_state[f"{s}_max_w"] = st.session_state[f"{s}_max"]
    st.checkbox("Max upgrades at this age", key=f"{s}_max_w", help=HELP["max"], on_change=_on_max_toggle,
                args=(s,))

    tech_cols = st.columns(2)
    with tech_cols[0]:
        st.markdown("**Blacksmith**", help=HELP["blacksmith"])
        if groups["blacksmith"]:
            for e in groups["blacksmith"]:
                tech_checkbox(s, e, selected)
        else:
            st.caption("No Blacksmith upgrades for this unit.")
    with tech_cols[1]:
        st.markdown("**University**", help=HELP["university"])
        if groups["university"]:
            for e in groups["university"]:
                tech_checkbox(s, e, selected)
        else:
            st.caption("None that apply.")
        st.markdown("**Unique techs**", help=HELP["unique"])
        for e in groups["unique"]:
            tech_checkbox(s, e, selected)
        if groups["other"]:
            st.markdown("**Other upgrades**", help=HELP["other"])
            for e in groups["other"]:
                tech_checkbox(s, e, selected)

    # ---- go to
    chain = [u for u in DATA.upgrade_chain(civ_id, uid) if u in available or u == uid]
    if len(chain) > 1:
        chain_key = f"{s}_chain_{uid}"
        st.session_state[chain_key] = uid
        st.pills("Upgrade line", chain, key=chain_key, format_func=DATA.label, help=HELP["chain"],
                 on_change=_on_jump, args=(s, chain_key))
    similar = DATA.similar_units(civ_id, uid, full)
    if similar:
        sim_key = f"{s}_sim_{uid}"
        st.selectbox("Similar units", similar, index=None, key=sim_key, format_func=DATA.label,
                     placeholder="Jump to a similar unit…", help=HELP["similar"], on_change=_on_jump,
                     args=(s, sim_key), filter_mode="contains")

    return {"civ": civ_id, "unit": uid, "age": age, "full": full, "techs": frozenset(selected)}


# =============================================================================== simulation (cached)

def _fighter(cfg: dict):
    stats = compute_stats(DATA, cfg["civ"], cfg["unit"], cfg["age"], set(cfg["techs"]), cfg["full"])
    label = f"{DATA.civs[cfg['civ']].name} {DATA.label(cfg['unit'])}"
    fighter = build_fighter(stats, label)
    second = DISMOUNT.get(cfg["unit"])
    if second in DATA.units:
        s2 = compute_stats(DATA, cfg["civ"], second, cfg["age"], set(cfg["techs"]), cfg["full"])
        fighter.respawn = build_fighter(s2, f"{DATA.civs[cfg['civ']].name} {DATA.label(second)}")
        fighter.notes.append(f"When killed it dismounts and keeps fighting as {DATA.label(second)} "
                             f"({fmt_num(s2.unit['hp'])} HP) - simulated.")
    return stats, fighter


@st.cache_data(max_entries=256, show_spinner=False)
def run_battle(cfg_l: tuple, cfg_r: tuple, settings: tuple, data_ver: str) -> dict:
    cl, cr = dict(cfg_l), dict(cfg_r)
    stats_l, fl = _fighter(cl)
    stats_r, fr = _fighter(cr)
    distance, charged, elev, runs = settings
    elevation = {"left": ("higher", "lower"), "right": ("lower", "higher")}.get(elev, ("level", "level"))
    out = {"fighters": (fl, fr), "stats": (stats_l, stats_r)}
    for mode in (EXPECTED, ALWAYS_HIT):
        cfg = SimSettings(mode=mode, distance=distance, start_charged=charged, elevation=elevation)
        out[mode] = {
            "duel": duel(fl, fr, cfg),
            "solo": (solo_kill(fl, fr, cfg, 0), solo_kill(fr, fl, cfg, 1)),
        }
    rcfg = SimSettings(mode=RANDOM, distance=distance, start_charged=charged, elevation=elevation, runs=runs)
    out[RANDOM] = {"mc": monte_carlo(fl, fr, rcfg), "solo": (solo_kill(fl, fr, rcfg, 0), solo_kill(fr, fl, rcfg, 1))}
    ecfg = SimSettings(mode=EXPECTED, distance=distance, start_charged=charged, elevation=elevation)
    out["lanchester"] = lanchester_ratio(fl, fr, ecfg)
    return out


@st.cache_data(max_entries=64, show_spinner=False)
def run_gauntlet(cfg_l: tuple, cfg_r: tuple, settings: tuple, budget: int, data_ver: str) -> dict:
    _, fl = _fighter(dict(cfg_l))
    _, fr = _fighter(dict(cfg_r))
    distance, charged, elev, _runs = settings
    elevation = {"left": ("higher", "lower"), "right": ("lower", "higher")}.get(elev, ("level", "level"))
    cfg = SimSettings(mode=EXPECTED, distance=distance, start_charged=charged, elevation=elevation)
    n_l = int(budget // total_cost(fl))
    n_r = int(budget // total_cost(fr))
    res = gauntlet(fl, fr, n_l, n_r, cfg)
    res["n"] = (n_l, n_r)
    return res


# =============================================================================== result rendering

def fmt_time(t: float) -> str:
    return "never" if (t is None or math.isinf(t)) else f"{t:.1f} s"


def fmt_num(x: float, digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and math.isinf(x)):
        return "∞"
    if abs(x - round(x)) < 1e-6:
        return str(int(round(x)))
    return f"{x:.{digits}f}"


def verdict_card(fl, fr, d) -> None:
    names = (fl.label, fr.label)
    maxhp = (fl.hp, fr.hp)
    if d.winner in (0, 1):
        w = d.winner
        color = SIDE_COLOR[SIDES[w]]
        left = d.hp_left[w]
        pct = 100 * left / maxhp[w] if maxhp[w] else 0
        st.markdown(
            f"""<div class='verdict' style='border-left: 8px solid {color}'>
            <div class='vtitle'>🏆 {names[w]} wins</div>
            <div class='sub'>after <b>{d.duration:.1f} s</b> with <b>{fmt_num(left)}</b> / {fmt_num(maxhp[w])} HP left
            ({pct:.0f}%) · {names[1 - w]} landed {d.volleys_landed[1 - w]} attack(s), {names[w]} landed {d.volleys_landed[w]}</div>
            <div class='hpbar'><div style='width:{pct:.1f}%; background:{color}'></div></div>
            </div>""",
            unsafe_allow_html=True,
        )
    elif d.winner == -1:
        st.markdown(f"<div class='verdict'><div class='vtitle'>☠️ Both units die</div><div class='sub'>Mutual kill at "
                    f"{d.duration:.1f} s.</div></div>", unsafe_allow_html=True)
    else:
        why = "Neither unit can attack the other at this distance." if not any(d.can_attack) else \
            "Nobody died within the time limit."
        st.markdown(f"<div class='verdict'><div class='vtitle'>🤝 No winner</div><div class='sub'>{why}</div></div>",
                    unsafe_allow_html=True)


def side_metrics(res_mode: dict, fl, fr, mode: str) -> None:
    solo = res_mode["solo"]
    cols = st.columns(2)
    for i, (f, o) in enumerate(((fl, fr), (fr, fl))):
        k = solo[i]
        with cols[i]:
            st.markdown(f"<span style='color:{SIDE_COLOR[SIDES[i]]};font-weight:700'>{f.label}</span>",
                        unsafe_allow_html=True)
            m = st.columns(4)
            p = f.accuracy if (f.ranged and mode == EXPECTED) else 1.0
            per_volley = k.first_hit_damage
            m[0].metric("Damage / attack", fmt_num(per_volley), help="Damage of one normal attack (all projectiles) "
                        "against the opponent's armor, if it hits. Charge bonuses not included.")
            m[1].metric("Attacks to kill", fmt_num(k.volleys) if k.possible else "—",
                        help="Attacks needed to kill the opponent if the opponent never fought back.")
            m[2].metric("Time to kill", fmt_time(k.time) if k.possible else "never",
                        help="Time this unit needs to kill the opponent on its own - for the loser, this is "
                             "when the winner would have died had the fight continued.")
            dps = per_volley * p / f.reload if f.reload else 0
            m[3].metric("DPS", f"{dps:.2f}", help="Average damage per second against this opponent "
                        "(attack × hit chance ÷ reload time).")


def hp_chart(fl, fr, d, key: str) -> None:
    fig = go.Figure()
    ts = [p[0] for p in d.timeline]
    for i, f in enumerate((fl, fr)):
        fig.add_trace(go.Scatter(x=ts, y=[max(0, p[1 + i]) for p in d.timeline], name=f.label, mode="lines",
                                 line=dict(color=SIDE_COLOR[SIDES[i]], width=3, shape="hv"),
                                 hovertemplate="%{y:.1f} HP at %{x:.2f}s<extra>" + f.label + "</extra>"))
    hit_x = [h.t for h in d.hits]
    hit_y = [max(0, h.hp_after) for h in d.hits]
    hit_c = [SIDE_COLOR[SIDES[1 - h.attacker]] for h in d.hits]
    hit_t = [f"{(fl, fr)[h.attacker].label} hits for {h.damage:.2f}" for h in d.hits]
    fig.add_trace(go.Scatter(x=hit_x, y=hit_y, mode="markers", marker=dict(size=7, color=hit_c, symbol="x"),
                             name="hits", text=hit_t, hovertemplate="%{text}<br>%{x:.2f}s<extra></extra>",
                             showlegend=False))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), xaxis_title="time (s)",
                      yaxis_title="HP", legend=dict(orientation="h", y=1.12), hovermode="x unified")
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=key)


def deterministic_tab(res: dict, fl, fr, mode: str) -> None:
    d = res[mode]["duel"]
    verdict_card(fl, fr, d)
    side_metrics(res[mode], fl, fr, mode)
    hp_chart(fl, fr, d, key=f"hp_{mode}")


def random_tab(res: dict, fl, fr) -> None:
    mc = res[RANDOM]["mc"]
    wl, wr = mc.win_rate
    c = st.columns([3, 2])
    with c[0]:
        fig = go.Figure(go.Bar(
            x=[wl * 100, mc.draw_rate * 100, wr * 100], y=[fl.label, "Draw / mutual kill", fr.label],
            orientation="h", marker_color=[SIDE_COLOR["L"], "#9ca3af", SIDE_COLOR["R"]],
            text=[f"{wl:.1%}", f"{mc.draw_rate:.1%}", f"{wr:.1%}"], textposition="auto"))
        fig.update_layout(height=220, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="% of battles won",
                          xaxis_range=[0, 100])
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    with c[1]:
        st.metric(f"{fl.label} wins", f"{wl:.1%}")
        st.metric(f"{fr.label} wins", f"{wr:.1%}")
        st.caption(f"{mc.runs} randomized battles · avg HP left when winning: "
                   f"{mc.avg_hp_left[0]:.1f} / {mc.avg_hp_left[1]:.1f}")
    side_metrics(res[RANDOM], fl, fr, RANDOM)
    samples_l, samples_r = mc.hp_left_samples
    if samples_l or samples_r:
        fig = go.Figure()
        if samples_l:
            fig.add_trace(go.Histogram(x=samples_l, name=fl.label, marker_color=SIDE_COLOR["L"], opacity=.75))
        if samples_r:
            fig.add_trace(go.Histogram(x=samples_r, name=fr.label, marker_color=SIDE_COLOR["R"], opacity=.75))
        fig.update_layout(barmode="overlay", height=260, margin=dict(l=10, r=10, t=30, b=10),
                          title="Winner's remaining HP across runs", xaxis_title="HP left",
                          yaxis_title="battles", legend=dict(orientation="h", y=1.15))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def stat_rows(stats, f) -> dict:
    u = stats.unit

    def bonus_list(table):
        items = [f"{'+' if v > 0 else ''}{fmt_num(v)} vs {ARMOR_CLASS_NAMES.get(c, f'class {c}')}"
                 for c, v in sorted(table.items()) if c not in BASE_CLASSES and v != 0]
        return ", ".join(items) or "—"

    def armor_classes(table):
        items = [f"{ARMOR_CLASS_NAMES.get(c, f'class {c}')} ({fmt_num(v)})"
                 for c, v in sorted(table.items()) if c not in BASE_CLASSES and c != 31]
        return ", ".join(items) or "—"

    cost = ", ".join(f"{fmt_num(v, 0)} {k}" for k, v in f.cost.items()) or "—"
    charge = "—"
    if f.charge_type:
        charge = f"type {f.charge_type}: max {fmt_num(f.charge_max)}, +{f.charge_rate:.3g}/s"
    return {
        "Hit points": fmt_num(u["hp"]),
        "Melee attack": fmt_num(u["attacks"].get(MELEE, 0)),
        "Pierce attack": fmt_num(u["attacks"].get(PIERCE, 0)),
        "Attack bonuses": bonus_list(u["attacks"]),
        "Melee armor": fmt_num(u["armours"].get(MELEE, 0)),
        "Pierce armor": fmt_num(u["armours"].get(PIERCE, 0)),
        "Armor classes": armor_classes(u["armours"]),
        "Reload time (s)": f"{u['reload']:.3g}",
        "Attack delay (s)": f"{u['attack_delay']:.3g}",
        "Range": fmt_num(u.get("range", 0)) + (f" (min {fmt_num(u['min_range'])})" if u.get("min_range") else ""),
        "Accuracy": f"{fmt_num(u.get('accuracy', 100))}%" if f.ranged else "melee",
        "Projectiles": fmt_num(f.n_projectiles) if f.ranged else "—",
        "Bonus damage resistance": f"{u.get('bonus_resist', 0):.0%}" if u.get("bonus_resist") else "—",
        "Charge": charge,
        "Regeneration (HP/min)": fmt_num(f.regen_per_s * 60) if f.regen_per_s else "—",
        "Speed": f"{u.get('speed', 0):.3g}",
        "Line of sight": fmt_num(u.get("los", 0)),
        "Cost": cost,
        "Train time (s)": fmt_num(u.get("train_time", 0)),
        "Class": UNIT_CLASS_NAMES.get(u["class"], f"class {u['class']}"),
    }


HIGHER_BETTER = {"Hit points", "Melee attack", "Pierce attack", "Melee armor", "Pierce armor", "Accuracy",
                 "Projectiles", "Speed", "Line of sight", "Regeneration (HP/min)"}
LOWER_BETTER = {"Reload time (s)", "Attack delay (s)", "Train time (s)"}


def comparison_table(res: dict) -> None:
    (sl, sr), (fl, fr) = res["stats"], res["fighters"]
    rl, rr = stat_rows(sl, fl), stat_rows(sr, fr)

    def num(x):
        try:
            return float(str(x).rstrip("%").split(" ")[0])
        except ValueError:
            return None

    rows = []
    for k in rl:
        a, b = rl[k], rr[k]
        na, nb = num(a), num(b)
        style_a = style_b = ""
        if na is not None and nb is not None and na != nb and (k in HIGHER_BETTER or k in LOWER_BETTER):
            better_a = (na > nb) if k in HIGHER_BETTER else (na < nb)
            good = "background: rgba(34,197,94,.18); font-weight:600"
            style_a, style_b = (good, "") if better_a else ("", good)
        rows.append(f"<tr><td class='muted'>{k}</td><td style='{style_a}'>{a}</td><td style='{style_b}'>{b}</td></tr>")
    st.markdown(
        f"""<table style='width:100%; border-collapse: collapse;'>
        <thead><tr><th style='text-align:left'></th>
        <th style='text-align:left; color:{SIDE_COLOR["L"]}'>{fl.label}</th>
        <th style='text-align:left; color:{SIDE_COLOR["R"]}'>{fr.label}</th></tr></thead>
        <tbody>{''.join(rows)}</tbody></table>""",
        unsafe_allow_html=True,
    )


def breakdown(res: dict) -> None:
    fl, fr = res["fighters"]
    cols = st.columns(2)
    for i, (a, b) in enumerate(((fl, fr), (fr, fl))):
        with cols[i]:
            st.markdown(f"**{a.label} → {b.label}**")
            ignore = a.can_ignore_armor
            for vi, v in enumerate(a.normal_volleys()):
                rows = damage_breakdown(v.attacks, b.armours, b.bonus_resist, ignore)
                if v.count > 1 or vi > 0:
                    st.caption(f"{'Primary projectile' if vi == 0 else f'{int(v.count)} extra projectile(s), each'}")
                if not rows:
                    st.caption("No matching attack classes → minimum damage of 1.")
                    continue
                st.dataframe(
                    [{"Class": ARMOR_CLASS_NAMES.get(r["class"], r["class"]), "Attack": r["attack"],
                      "Target armor": r["armor"], "Damage": round(r["net"], 2)} for r in rows],
                    hide_index=True, width="stretch")
            if a.charge_type and a.charge_max:
                st.caption("Charged attacks add their charge on top of this (see Stat comparison → Charge).")


def cost_section(res: dict, cfg_l: tuple, cfg_r: tuple, settings: tuple) -> None:
    fl, fr = res["fighters"]
    cl, cr = total_cost(fl), total_cost(fr)
    ratio = res["lanchester"]
    c = st.columns(3)
    c[0].metric(f"{fl.label} cost", f"{fmt_num(cl, 0)}", help=", ".join(f"{v} {k}" for k, v in fl.cost.items()))
    c[1].metric(f"{fr.label} cost", f"{fmt_num(cr, 0)}", help=", ".join(f"{v} {k}" for k, v in fr.cost.items()))
    if ratio is None:
        c[2].metric("Mass-fight value", "—")
    elif math.isinf(ratio):
        c[2].metric("Mass-fight value", f"{fr.label} can't hurt {fl.label}")
    elif ratio == 0:
        c[2].metric("Mass-fight value", f"{fl.label} can't hurt {fr.label}")
    else:
        per_res = ratio * cr / cl
        c[2].metric(f"1 {fl.label} ≈", f"{ratio:.2f} × {fr.label}",
                    help="In a large battle (Lanchester's square law: strength ∝ count² × DPS × HP), this many "
                         "right-side units match one left-side unit.")
        better = fl.label if per_res > 1 else fr.label
        st.markdown(f"Per resource spent, **{better}** is stronger: resource-adjusted ratio "
                    f"**{max(per_res, 1 / per_res):.2f}×** (gold, food, wood and stone weighted equally).")
    budget_default = int(math.ceil(max(cl, cr) * 10 / 100.0) * 100)
    budget = st.slider("Resource budget per side", min_value=int(max(cl, cr)), max_value=int(max(cl, cr) * 40),
                       value=min(budget_default, int(max(cl, cr) * 40)), step=5, help=HELP["budget"])
    g = run_gauntlet(cfg_l, cfg_r, settings, budget, data_version())
    n_l, n_r = g["n"]
    left_l, left_r = g["left"]
    st.markdown(f"With **{budget}** resources: **{n_l}× {fl.label}** vs **{n_r}× {fr.label}** fighting one at a "
                f"time → survivors: **{left_l}** left vs **{left_r}** right.")


def applied_section(res: dict) -> None:
    (sl, sr), (fl, fr) = res["stats"], res["fighters"]
    cols = st.columns(2)
    for i, (s, f) in enumerate(((sl, fl), (sr, fr))):
        with cols[i]:
            st.markdown(f"**{f.label}**")
            kinds = {"civ": "civ bonus", "team": "team bonus", "age": "age", "auto": "civ bonus / auto",
                     "user": "researched"}
            if s.applied:
                for a in s.applied:
                    st.markdown(f"<span class='chip'>{a.source} · <span class='muted'>{kinds[a.kind]}</span></span>",
                                unsafe_allow_html=True)
            else:
                st.caption("No bonuses or technologies modify this unit.")


# =============================================================================== sidebar

def sidebar() -> None:
    meta = DATA.meta
    with st.sidebar:
        st.markdown("## ⚔️ AoE2 Duel Calculator")
        st.caption("Pick two units, upgrade them, and see who wins a straight fight — using the game's own data "
                   "and combat formulas.")
        st.markdown("#### Data")
        patch = f"Update {meta['game_update']}" if meta.get("game_update") else "patch unknown"
        st.markdown(f"<span class='chip'>{patch}</span>"
                    f"<span class='chip'>Game build {meta.get('game_build') or '?'}</span>"
                    f"<span class='chip'>dat {meta.get('dat_sha1', '?')}</span>", unsafe_allow_html=True)
        st.caption(f"Extracted from the game files on {meta.get('extracted_at', '?')[:10]} "
                   f"(game data file dated {meta.get('dat_modified', '?')[:10]}).")
        updater.render_update_panel(DATA)
        with st.expander("Mechanics & assumptions"):
            st.markdown(MECHANICS_MD)
        st.caption("Share: " + HELP["share"])


# =============================================================================== main

def main() -> None:
    init_state()
    sidebar()

    head = st.columns([6, 1])
    head[0].markdown("### Choose your fighters")
    head[1].button("⇄ Swap sides", on_click=swap_sides, width="stretch",
                   help="Swap the left and right setups.")

    cols = st.columns(2, gap="large")
    cfgs = {}
    for s, col in zip(SIDES, cols):
        with col:
            with st.container(border=True):
                cfgs[s] = side_panel(s)

    with st.expander("Battle conditions", expanded=False):
        c = st.columns(4)
        with c[0]:
            st.toggle("Auto distance", key="distance_auto", help=HELP["distance"])
            if not st.session_state["distance_auto"]:
                st.slider("Distance (tiles)", 0.0, 16.0, key="distance", step=0.5)
        c[1].toggle("Start fully charged", key="charged", help=HELP["charged"])
        c[2].segmented_control("High ground", ["left", "level", "right"], key="elev",
                               format_func={"left": "Left", "level": "Level", "right": "Right"}.get,
                               help=HELP["elevation"])
        c[3].select_slider("Randomized runs", [200, 500, 1000, 2000, 5000], key="runs", help=HELP["runs"])
    if st.session_state.get("elev") is None:
        st.session_state["elev"] = "level"

    sync_query_params()

    settings = (None if st.session_state["distance_auto"] else float(st.session_state["distance"]),
                bool(st.session_state["charged"]), st.session_state["elev"], int(st.session_state["runs"]))
    cfg_l = tuple(sorted(cfgs["L"].items()))
    cfg_r = tuple(sorted(cfgs["R"].items()))
    with st.spinner("Simulating…"):
        res = run_battle(cfg_l, cfg_r, settings, data_version())
    fl, fr = res["fighters"]

    st.markdown("### Battle result")
    dist = res[EXPECTED]["duel"].distance
    st.caption(f"Engagement distance: {fmt_num(dist)} tile(s)" +
               (" (melee range)" if dist == 0 else "") + " · no movement, units start attacking immediately.")
    tabs = st.tabs(["⚖️ Expected", "🎲 Randomized", "🎯 Every shot hits"])
    with tabs[0]:
        st.caption(HELP["mode_expected"])
        deterministic_tab(res, fl, fr, EXPECTED)
    with tabs[1]:
        st.caption(HELP["mode_random"])
        random_tab(res, fl, fr)
    with tabs[2]:
        st.caption(HELP["mode_always"])
        deterministic_tab(res, fl, fr, ALWAYS_HIT)

    notes = [f"**{f.label}:** {n}" for f in (fl, fr) for n in f.notes]
    if notes:
        st.info("\n\n".join(notes), icon="ℹ️")

    st.markdown("### Side-by-side stats")
    comparison_table(res)

    with st.expander("🔍 Damage per hit breakdown", expanded=False):
        breakdown(res)
    with st.expander("💰 Cost efficiency", expanded=False):
        cost_section(res, cfg_l, cfg_r, settings)
    with st.expander("🧪 Bonuses & technologies applied", expanded=False):
        applied_section(res)


main()
