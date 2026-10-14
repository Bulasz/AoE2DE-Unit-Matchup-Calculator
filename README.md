# ⚔️ AoE2 Duel Calculator

A Streamlit app that pits two Age of Empires II: Definitive Edition units against each other and simulates a
straight 1v1 fight with the game's own combat mechanics.

* Pick a civilization and unit per side (prefix-first search), the age, and Blacksmith / University /
  unique / other techs - or tick **Max upgrades at this age**. Unique techs that don't affect the unit stay
  visible but greyed out.
* **Full tech tree** toggle, **upgrade-line** and **similar-unit** jumps, **swap sides**, and a shareable URL.
* Results in three accuracy modes - *expected*, *randomized* (Monte Carlo win %), *every shot hits* - with
  hits/time to kill for both sides (incl. when the winner would have died), remaining HP, an HP-over-time
  chart, side-by-side stats, a per-hit damage breakdown and cost efficiency (Lanchester ratio + an
  equal-resources gauntlet).
* Battle conditions: distance, starting charge, high ground.

## How the data works

All numbers come from the **game's own data files**, not from hand-typed tables:

| What | Source |
|---|---|
| Unit stats, armor classes, attack delay, projectiles, charges | `empires2_x2_p1.dat` (parsed with [genieutils-py](https://github.com/SiegeEngineers/genieutils-py)) |
| Tech, civ-bonus and team-bonus effects | effect commands in the same `.dat` - replayed in order, exactly like the game applies them |
| What each civ can build, and in which age | the game's `CivTechTrees/*.json` |
| Names and descriptions | the game's English string table |

`extractor/extract.py` turns those into `data/game_data.json` (~1.3 MB), which is committed to the repo. The
deployed app only reads that JSON.

### Updating after a game patch

Streamlit Cloud cannot see your game install, so updates are a two-step process:

1. In the app, **🔄 Check for updates** (sidebar) compares the data with
   * the newest AoE2 DE patch page on the [Fandom wiki](https://ageofempires.fandom.com/wiki/Age_of_Empires_II:Portal),
   * the independent [aoe2techtree](https://github.com/SiegeEngineers/aoe2techtree) extraction (stat-by-stat),
   * the wiki unit infoboxes (informational - the wiki often lags behind or lists civ-specific values),

   and sorts findings into *outdated data* (fixed by re-extracting), *wiki disagreements* (informational) and
   *needs manual implementation* (a mechanic the simulator does not know yet, e.g. a new charge type).
   With `GITHUB_TOKEN` / `GITHUB_REPO` in the app's secrets, the report can be saved to the repo.
2. On a PC with the updated game:

   ```bash
   pip install -r requirements-extract.txt
   python -m extractor.extract --commit
   ```

   This re-reads the game files, writes `data/game_data.json`, commits and pushes; Streamlit Cloud redeploys
   automatically. (When you run the app locally on that PC, a **⛏️ Re-extract from game files** button
   appears too.)

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate elsewhere)
pip install -r requirements.txt
streamlit run app.py
```

Tests: `pip install pytest && python -m pytest tests`

## Deploying to Streamlit Community Cloud

1. Push this folder to a GitHub repository.
2. On [share.streamlit.io](https://share.streamlit.io) → *Create app* → pick the repo, branch `main`,
   main file `app.py`.
3. Optional, to let the app save update reports to the repo - *Settings → Secrets*:

   ```toml
   GITHUB_TOKEN = "github_pat_..."   # fine-grained token: Contents read/write on this repo only
   GITHUB_REPO = "your-name/your-repo"
   GITHUB_BRANCH = "main"
   ```

## Simulation model (summary)

* Damage per hit = `max(1, Σ max(0, attack_c − armor_c))` over the attacker's classes present in the target's
  armor classes; bonus classes are scaled by the target's bonus damage resistance; ×1.25 / ×0.75 for
  high / low ground.
* An attack starts every *reload time* from t = 0; damage lands after the *attack delay* (+ projectile flight
  time). Units killed before their release frame deal no damage; projectiles in flight still land.
* Simulated specials: multiple / secondary projectiles, charge attacks, charged volleys, projectile dodging,
  melee blocking, armor ignoring & shredding, damage reflection, regeneration, Konnik dismounting.
* Not simulated: movement, auras and other multi-unit effects, conversion, splash on other units, missed
  projectiles accidentally hitting.

The full list is in the app under *Mechanics & assumptions*.

Age of Empires II © Microsoft Corporation. This is a fan-made tool, created under Microsoft's
[Game Content Usage Rules](https://www.xbox.com/en-US/developers/rules) using assets from Age of Empires II.
It is not endorsed by or affiliated with Microsoft.
