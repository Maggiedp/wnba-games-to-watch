# WNBA Games to Watch

A nightly ranking of the best WNBA matchups, weighing team quality and playoff stakes. Filter by where you can watch.

**Live site:** https://wumbers.com

## How it works

Every upcoming game gets a score out of 100:

- **Quality (60%)** — harmonic mean of both teams' [ESPN BPI](https://www.espn.com/wnba/bpi) ratings. Penalizes lopsided matchups: a +5 vs +5 game scores much higher than +5 vs -5.
- **Importance (40%)** — a single 10,000-run Monte Carlo simulation of the rest of the season, partitioned by tonight's outcome, summing how much every team's playoff odds swing on the result — including bubble teams not on the court whose fate hinges on it. Per-game win probabilities use Elo ratings with home-court advantage and a margin-of-victory multiplier, replayed chronologically from 2024 with a 0.5 regression toward the mean at each season boundary.

The **60/40 weighting is an editorial choice, not a fitted parameter** — there's no ground truth for how "watchable" a game is. For what's empirically validated (Elo calibration) versus what's a design decision, and the model's known limitations, see [METHODOLOGY.md](METHODOLOGY.md).

## Tech stack

- **Backend:** Python, FastAPI, PostgreSQL (Cloud SQL)
- **Data:** ESPN public APIs (BPI + scoreboard)
- **Deployment:** GCP Cloud Run (API) + Cloud Scheduler (daily update at 6 AM ET)

## Local development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python main.py          # API at http://localhost:8000
python -m scripts.daily_update   # populate the database
pytest tests/ -v
```
