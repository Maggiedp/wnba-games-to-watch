# WNBA Games to Watch

A WNBA game recommendation tool that combines game quality, playoff importance, and streaming availability to help fans decide what to watch.

## Concept

Fills the gap left by FiveThirtyEight's defunct "Games to Watch" feature (538 shut down March 2025). Combines two pre-game metrics:

- **Quality**: Harmonic mean of the two teams' Elo ratings (penalizes lopsided matchups)
- **Importance**: Total playoff/title odds swing from Monte Carlo simulation of remaining schedule

Plus a **where to watch** filter by streaming service (ESPN/ABC, NBC/Peacock, Prime Video, CBS/Paramount+, ION, USA Network, League Pass, NBA TV).

### Phase 2 (future)
In-game excitement tracking via win probability swings.

## Data sources
- ESPN API (via `wehoop` package)
- balldontlie free API
- WNBA.com schedule (broadcaster info)

## Tech stack
Python
