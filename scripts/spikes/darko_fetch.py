"""Cached loader for sportsdataverse/wehoop WNBA parquet (pbp + player box).
Static GitHub files — no headers, rate limits, or bot-blocking. The stats.wnba.com
API was rejected: it black-holes datacenter IPs (incl. Cloud Run)."""

import os
import requests
import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
BASE = "https://raw.githubusercontent.com/sportsdataverse/wehoop-wnba-data/main/wnba"


def _download(kind: str, season: int) -> str:
    """Download one season's parquet to the cache (if absent). kind in
    {'play_by_play', 'player_box'}. Returns local path."""
    fname = f"{kind}_{season}.parquet"
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, fname)
    if os.path.exists(path):
        return path
    subdir = "pbp" if kind == "play_by_play" else "player_box"
    url = f"{BASE}/{subdir}/parquet/{fname}"
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    with open(path, "wb") as f:
        f.write(resp.content)
    return path


def load_pbp(season: int) -> pd.DataFrame:
    return pd.read_parquet(_download("play_by_play", season))


def load_player_box(season: int) -> pd.DataFrame:
    return pd.read_parquet(_download("player_box", season))


def game_ids(season: int, season_type: int | None = None) -> list[int]:
    """Distinct game_ids for a season, in schedule order. season_type filters on
    the pbp 'season_type' column when provided (2=regular, 3=playoffs typical)."""
    df = load_pbp(season)
    if season_type is not None and "season_type" in df.columns:
        df = df[df["season_type"] == season_type]
    return list(df.sort_values("sequence_number")["game_id"].drop_duplicates())
