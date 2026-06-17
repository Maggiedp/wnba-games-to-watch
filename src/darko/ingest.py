"""Load sportsdataverse/wehoop WNBA parquet (pbp + player_box), cache-backed.
Static GitHub files; stats.wnba.com is rejected (black-holes datacenter IPs).
Adapted from scripts/spikes/darko_fetch.py with an injectable cache_dir for tests."""

import os
import requests
import pandas as pd

_DEFAULT_CACHE = os.path.join(os.path.dirname(__file__), "_cache")
BASE = "https://raw.githubusercontent.com/sportsdataverse/wehoop-wnba-data/main/wnba"


def _path(kind: str, season: int, cache_dir: str) -> str:
    fname = f"{kind}_{season}.parquet"
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, fname)
    if os.path.exists(path):
        return path
    subdir = "pbp" if kind == "play_by_play" else "player_box"
    url = f"{BASE}/{subdir}/parquet/{fname}"
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    with open(path, "wb") as f:
        f.write(resp.content)
    return path


def load_pbp(season: int, cache_dir: str = _DEFAULT_CACHE) -> pd.DataFrame:
    df = pd.read_parquet(_path("play_by_play", season, cache_dir))
    df.attrs["season"] = season
    return df


def load_player_box(season: int, cache_dir: str = _DEFAULT_CACHE) -> pd.DataFrame:
    df = pd.read_parquet(_path("player_box", season, cache_dir))
    df.attrs["season"] = season
    return df
