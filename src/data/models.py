"""Data models for WNBA teams and games."""

from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class Team:
    id: Optional[int] = None
    name: str = ""
    bpi_rating: float = 0.0
    last_updated: Optional[datetime] = None


@dataclass
class Game:
    id: Optional[int] = None
    team_a_id: int = 0
    team_b_id: int = 0
    date: str = ""
    time: str = ""
    winner_id: Optional[int] = None
    final_score_a: Optional[int] = None
    final_score_b: Optional[int] = None
    broadcaster: str = ""
    quality_score: Optional[float] = None
    importance_score: Optional[float] = None
    overall_score: Optional[float] = None
    ranking_date: Optional[datetime] = None


@dataclass
class DailyRanking:
    date: str = ""
    team_a_id: int = 0
    team_b_id: int = 0
    quality_score: float = 0.0
    importance_score: float = 0.0
    overall_score: float = 0.0
    broadcaster: str = ""
