"""Update checks: is the extracted game data still current, and does anything need manual work?

Sources:
  * Fandom wiki (ageofempires.fandom.com) - latest AoE2 DE patch page + unit infobox stats.
  * SiegeEngineers/aoe2techtree - community dataset extracted from the game files
    (independent cross-check of base unit stats).

The authoritative data comes from the user's own game files (see extractor/extract.py), which
can only be re-extracted on a machine with the game installed. The cloud app therefore *checks*
and *reports*; it can save the report to GitHub when a token is configured in st.secrets.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
from pathlib import Path

import requests

from .constants import (CHARGE_AREA_ATTACK, CHARGE_ATTACK, CHARGE_DODGE, CHARGE_IGNORE_MELEE,
                        CHARGE_MIXED_VOLLEY, CHARGE_PROJECTILES, UNSIMULATED_ABILITIES)
from .data import GameData

FANDOM_API = "https://ageofempires.fandom.com/api.php"
TECHTREE_DATA = "https://raw.githubusercontent.com/SiegeEngineers/aoe2techtree/master/data/data.json"
TECHTREE_COMMITS = "https://api.github.com/repos/SiegeEngineers/aoe2techtree/commits?path=data/data.json&per_page=1"
UA = {"User-Agent": "aoe2-duel-calculator/1.0 (streamlit app; update checker)"}
REPORT_FILE = Path(__file__).resolve().parent.parent / "data" / "update_report.json"

HANDLED_CHARGE_TYPES = {0, CHARGE_ATTACK, CHARGE_AREA_ATTACK, CHARGE_DODGE, CHARGE_IGNORE_MELEE,
                        CHARGE_PROJECTILES, CHARGE_MIXED_VOLLEY}
HANDLED_ABILITY_BITS = 1 | 2 | 4 | 8 | 16 | sum(UNSIMULATED_ABILITIES)
TIMEOUT = 20


# =============================================================================== patch detection

def latest_fandom_patch() -> dict | None:
    """Newest 'Update NNNNNN' page on the wiki that belongs to Age of Empires II: DE."""
    r = requests.get(FANDOM_API, params={"action": "query", "list": "allpages", "apprefix": "Update ",
                                         "aplimit": 500, "format": "json"}, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    nums = []
    for p in r.json()["query"]["allpages"]:
        m = re.fullmatch(r"Update (\d{5,})", p["title"])
        if m:
            nums.append(int(m.group(1)))
    nums.sort(reverse=True)
    titles = [f"Update {n}" for n in nums[:8]]
    if not titles:
        return None
    r = requests.get(FANDOM_API, params={"action": "query", "titles": "|".join(titles), "prop": "revisions|categories",
                                         "rvprop": "content", "rvslots": "main", "cllimit": 50, "format": "json"},
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    best = None
    for page in r.json()["query"]["pages"].values():
        cats = {c["title"] for c in page.get("categories", [])}
        if "Category:Age of Empires II" not in cats:
            continue
        text = page["revisions"][0]["slots"]["main"]["*"]
        if "Definitive Edition" not in text.split("}}")[0]:
            continue
        num = int(page["title"].split()[-1])
        date = None
        m = re.search(r"\|\s*Date\s*=\s*([^\n|]+)", text)
        if m:
            try:
                date = dt.datetime.strptime(re.sub(r"\[\[|\]\]", "", m.group(1)).strip(), "%B %d, %Y").date()
            except ValueError:
                pass
        if best is None or num > best["update"]:
            best = {"update": num, "date": date.isoformat() if date else None,
                    "url": f"https://ageofempires.fandom.com/wiki/Update_{num}"}
    return best


def latest_techtree_commit() -> dict | None:
    r = requests.get(TECHTREE_COMMITS, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    items = r.json()
    if not items:
        return None
    c = items[0]["commit"]
    return {"date": c["committer"]["date"][:10], "message": c["message"].splitlines()[0],
            "url": items[0]["html_url"]}


# =============================================================================== cross-checks

def compare_with_techtree(data: GameData) -> list[dict]:
    """Compare base stats with SiegeEngineers' independent extraction."""
    r = requests.get(TECHTREE_DATA, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    up = r.json()["data"]["Unit"]
    diffs = []
    for uid, u in data.units.items():
        x = up.get(str(uid))
        if x is None:
            continue
        checks = [("HP", u["hp"], x.get("HP")), ("Reload", u.get("reload"), x.get("ReloadTime")),
                  ("Attack delay", u.get("attack_delay"), x.get("AttackDelaySeconds")),
                  ("Accuracy", u.get("accuracy"), x.get("AccuracyPercent")),
                  ("Speed", u.get("speed"), x.get("Speed"))]
        for name, ours, theirs in checks:
            if ours is None or theirs is None:
                continue
            if abs(float(ours) - float(theirs)) > 0.011:
                diffs.append({"unit": data.label(uid), "stat": name, "ours": ours, "upstream": theirs})
        ar = {int(a["Class"]): a["Amount"] for a in x.get("Armours", [])}
        at = {int(a["Class"]): a["Amount"] for a in x.get("Attacks", [])}
        if ar != {int(k): v for k, v in u.get("armours", {}).items()}:
            diffs.append({"unit": data.label(uid), "stat": "Armor classes", "ours": u.get("armours"), "upstream": ar})
        if at != {int(k): v for k, v in u.get("attacks", {}).items()}:
            diffs.append({"unit": data.label(uid), "stat": "Attack classes", "ours": u.get("attacks"), "upstream": at})
    new_units = [{"id": k, "internal": v.get("internal_name")} for k, v in up.items()
                 if int(k) not in data.units and v.get("Attack", 0) > 0]
    return diffs + [{"unit": n["internal"], "stat": "new unit upstream", "ours": None, "upstream": n["id"]}
                    for n in new_units]


_TT = re.compile(r"\{\{tt\|([^|{}]*)(?:\|[^{}]*)?\}\}")


def _clean_wiki(v: str) -> str:
    v = _TT.sub(r"\1", v)
    v = re.sub(r"<[^>]+>", " ", v)
    v = re.sub(r"\{\{[^{}]*\}\}", " ", v)
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)
    return v


def _wiki_number(raw: str, elite: bool) -> float | None:
    nums = re.findall(r"-?\d+(?:\.\d+)?", _clean_wiki(raw))
    if not nums:
        return None
    return float(nums[1] if elite and len(nums) > 1 else nums[0])


def _infobox(text: str) -> dict[str, str]:
    m = re.search(r"\{\{Infobox AoE2 unit(.*?)\n\}\}", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).split("\n|"):
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def compare_with_fandom(data: GameData, unit_ids: list[int] | None = None) -> list[dict]:
    """Compare un-upgraded stats with the wiki infoboxes. Informational only: the wiki sometimes
    lags behind patches. Unique units are compared with their civ's bonuses applied (Imperial Age,
    no technologies), because that is how the wiki lists them."""
    from .stats import compute_stats

    ids = unit_ids or [u for u in data.units if GameData.is_combat_unit(data.units[u])]
    candidates: dict[int, list[str]] = {}
    for uid in ids:
        name = data.units[uid]["name"]
        cands = [f"{name} (Age of Empires II)", name]
        if name.startswith("Elite "):
            base = name[len("Elite "):]
            cands += [f"{base} (Age of Empires II)", base]
        candidates[uid] = cands
    titles = sorted({t for c in candidates.values() for t in c})
    resolved: dict[str, tuple[str, str]] = {}   # requested title -> (final title, wikitext)
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        r = requests.get(FANDOM_API, params={"action": "query", "titles": "|".join(batch), "redirects": 1,
                                             "prop": "revisions", "rvprop": "content", "rvslots": "main",
                                             "format": "json"}, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        q = r.json()["query"]
        redirect = {x["from"]: x["to"] for x in q.get("redirects", [])}
        normalized = {x["from"]: x["to"] for x in q.get("normalized", [])}
        content = {p["title"]: p["revisions"][0]["slots"]["main"]["*"]
                   for p in q["pages"].values() if "revisions" in p}
        for t in batch:
            n = normalized.get(t, t)
            final = redirect.get(n, n)
            if final in content and "Infobox AoE2 unit" in content[final]:
                resolved[t] = (final, content[final])

    owners: dict[int, list[int]] = {}
    for c in data.civs.values():
        for uid, node in c.units.items():
            if node["status"] != "NotAvailable":
                owners.setdefault(uid, []).append(c.id)

    diffs = []
    for uid, cands in candidates.items():
        hit = next((resolved[t] for t in cands if t in resolved), None)
        if hit is None:
            continue
        final, text = hit
        box = _infobox(text)
        name = data.units[uid]["name"]
        elite = name.startswith("Elite ") and not final.startswith("Elite ")
        # The wiki sometimes lists base values and sometimes values with the owner civ's bonuses
        # (at the age the unit appears). A stat is flagged only if it matches neither.
        variants = [data.units[uid]]
        civs = owners.get(uid, [])
        if len(civs) == 1:
            age = data.unit_min_age(civs[0], uid)
            variants.append(compute_stats(data, civs[0], uid, age, set(), False).unit)

        def values(u):
            arm = {int(k): v for k, v in u["armours"].items()}
            atk = {int(k): v for k, v in u["attacks"].items()}
            return {"HP": u["hp"], "Armor": arm.get(4), "PierceArmor": arm.get(3), "ROF": u.get("reload"),
                    "Range": u.get("range"), "MAttack": atk.get(4), "PAttack": atk.get(3)}

        vals = [values(v) for v in variants]
        labels = {"HP": "HP", "Armor": "Melee armor", "PierceArmor": "Pierce armor", "ROF": "Reload",
                  "Range": "Range", "MAttack": "Melee attack", "PAttack": "Pierce attack"}
        for field_name, stat in labels.items():
            if field_name not in box:
                continue
            wiki = _wiki_number(box[field_name], elite)
            ours = [v[field_name] for v in vals if v[field_name] is not None]
            if wiki is None or not ours:
                continue
            if all(abs(wiki - float(o)) > 0.051 for o in ours):
                diffs.append({"unit": data.label(uid), "stat": stat, "ours": round(float(ours[0]), 3),
                              "wiki": wiki,
                              "page": f"https://ageofempires.fandom.com/wiki/{final.replace(' ', '_')}"})
    return diffs


def unhandled_mechanics(data: GameData) -> list[dict]:
    """Things in the data that the combat engine does not know how to simulate."""
    out = []
    for uid, u in data.units.items():
        if not GameData.is_combat_unit(u):
            continue
        ct = u.get("charge_type", 0)
        if ct not in HANDLED_CHARGE_TYPES and ct > 0:
            out.append({"unit": data.label(uid), "issue": f"unknown charge type {ct}"})
        ab = u.get("combat_ability", 0) or 0
        if ab & ~HANDLED_ABILITY_BITS:
            out.append({"unit": data.label(uid), "issue": f"unknown combat ability bits {ab & ~HANDLED_ABILITY_BITS}"})
    skipped = data.meta.get("skipped_effect_types", {})
    for t in skipped:
        if int(t) not in (0, 4, 5, 8, 10, 11, 12, 15, 18, 21, 26, 31, 36, 40, 200, 201, 202, 204, 255):
            out.append({"unit": "—", "issue": f"new effect command type {t} in the game data"})
    return out


# =============================================================================== report

def build_report(data: GameData, wiki_check: bool = True) -> dict:
    report: dict = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                    "data": {k: data.meta.get(k) for k in ("game_build", "dat_modified", "dat_sha1", "game_update")},
                    "errors": []}
    dat_date = (data.meta.get("dat_modified") or "")[:10]
    try:
        report["fandom_patch"] = latest_fandom_patch()
    except Exception as e:  # noqa: BLE001 - network issues should not break the app
        report["errors"].append(f"Fandom patch check failed: {e}")
    try:
        report["techtree_commit"] = latest_techtree_commit()
    except Exception as e:  # noqa: BLE001
        report["errors"].append(f"aoe2techtree check failed: {e}")
    stale_reasons = []
    p = report.get("fandom_patch")
    if p and p.get("date") and dat_date and p["date"] > dat_date:
        stale_reasons.append(f"Fandom lists Update {p['update']} ({p['date']}), newer than the game data "
                             f"({dat_date}).")
    c = report.get("techtree_commit")
    if c and dat_date and c["date"] > dat_date:
        stale_reasons.append(f"aoe2techtree updated its data on {c['date']} (\"{c['message']}\").")
    report["stale_reasons"] = stale_reasons
    try:
        report["techtree_diffs"] = compare_with_techtree(data)
    except Exception as e:  # noqa: BLE001
        report["errors"].append(f"aoe2techtree comparison failed: {e}")
        report["techtree_diffs"] = []
    if wiki_check:
        try:
            report["wiki_diffs"] = compare_with_fandom(data)
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"Fandom comparison failed: {e}")
            report["wiki_diffs"] = []
    report["unhandled"] = unhandled_mechanics(data)
    return report


def save_report_to_github(report: dict, token: str, repo: str, branch: str = "main",
                          path: str = "data/update_report.json") -> str:
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", **UA}
    sha = None
    r = requests.get(api, headers=headers, params={"ref": branch}, timeout=TIMEOUT)
    if r.status_code == 200:
        sha = r.json().get("sha")
    body = {"message": f"Update check report {report['checked_at'][:10]}",
            "content": base64.b64encode(json.dumps(report, indent=1, default=str).encode()).decode(),
            "branch": branch}
    if sha:
        body["sha"] = sha
    r = requests.put(api, headers=headers, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["commit"]["html_url"]


# =============================================================================== Streamlit panel

def _local_extractor_available() -> bool:
    try:
        import genieutils  # noqa: F401
        from extractor.extract import DEFAULT_GAME_DIRS
    except ImportError:
        return False
    return any((Path(d) / "resources" / "_common" / "dat" / "empires2_x2_p1.dat").exists() for d in DEFAULT_GAME_DIRS)


def render_update_panel(data: GameData) -> None:
    import streamlit as st

    st.markdown("#### Updates", help="The game data is extracted from the game's own files. 'Check for updates' "
                "compares it against the wiki and an independent community extraction, and tells you whether "
                "the data is outdated or something needs manual work.")
    if st.button("🔄 Check for updates", width="stretch"):
        with st.spinner("Checking Fandom wiki and aoe2techtree…"):
            st.session_state["update_report"] = build_report(data)
    if _local_extractor_available():
        if st.button("⛏️ Re-extract from game files", width="stretch",
                     help="Your AoE2 DE installation was found on this machine: re-read its data files."):
            from extractor.extract import main as extract_main
            with st.spinner("Parsing the game data file…"):
                extract_main([])
            st.cache_resource.clear()
            st.cache_data.clear()
            st.success("Game data re-extracted. Commit data/game_data.json to publish it.")
            st.rerun()

    report = st.session_state.get("update_report")
    if report is None and REPORT_FILE.exists():
        try:
            report = json.loads(REPORT_FILE.read_text(encoding="utf-8"))
            st.caption(f"Last saved check: {report.get('checked_at', '?')[:16]}")
        except ValueError:
            report = None
    if not report:
        return

    if report.get("stale_reasons"):
        st.warning("**Game data may be outdated.**\n\n" + "\n\n".join(report["stale_reasons"]) +
                   "\n\nOn a PC with the updated game run:\n\n`python -m extractor.extract --commit`")
    else:
        st.success("Game data matches the latest known patch.")
    td = report.get("techtree_diffs", [])
    wd = report.get("wiki_diffs", [])
    un = report.get("unhandled", [])
    with st.expander(f"Stat changes vs aoe2techtree ({len(td)})"):
        st.caption("Numbers that differ from an independent extraction. These are fixed automatically by "
                   "re-extracting from an up-to-date game.")
        if td:
            st.dataframe(td, hide_index=True, width="stretch")
    with st.expander(f"Wiki disagreements ({len(wd)})"):
        st.caption("Base stats that differ from the Fandom infobox. Often the wiki lags behind or lists civ-"
                   "specific values; informational only.")
        if wd:
            st.dataframe(wd, hide_index=True, width="stretch",
                         column_config={"page": st.column_config.LinkColumn("page")})
    if un:
        st.error("**Needs manual implementation:** the data contains mechanics the simulator does not know "
                 "yet.\n\n" + "\n".join(f"* {x['unit']}: {x['issue']}" for x in un))
    for e in report.get("errors", []):
        st.caption(f"⚠️ {e}")

    try:
        token, repo = st.secrets.get("GITHUB_TOKEN"), st.secrets.get("GITHUB_REPO")
        branch = st.secrets.get("GITHUB_BRANCH", "main")
    except Exception:  # noqa: BLE001 - no secrets.toml configured
        token = repo = branch = None
    if token and repo and st.button("💾 Save report to GitHub", width="stretch"):
        try:
            url = save_report_to_github(report, token, repo, branch)
            st.success(f"Saved: {url}")
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not save: {e}")
