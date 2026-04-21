# WNBA Games to Watch

A nightly ranking of the best WNBA matchups, weighing team quality and playoff stakes. Filter by where you can watch.

**Live site:** https://wnba-games-to-watch-1068218371131.us-central1.run.app

## How it works

Every upcoming game gets a score out of 100:

- **Quality (60%)** — harmonic mean of both teams' [ESPN BPI](https://www.espn.com/wnba/bpi) ratings. Penalizes lopsided matchups: a +5 vs +5 game scores much higher than +5 vs -5.
- **Importance (40%)** — Monte Carlo simulation (10,000 runs) of the remaining schedule, measuring how much each team's playoff odds swing on tonight's result.

## Tech stack

- **Backend:** Python, FastAPI, SQLite
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
