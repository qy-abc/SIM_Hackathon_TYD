"""Versioned commission plans. The plan in force is chosen by the order's date."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

PLANS_DIR = Path(__file__).resolve().parent.parent / "data" / "plans"


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    effective_from: date
    base_rate_bp: int
    tier_threshold_cents: int
    tier_rate_bp: int
    discount_threshold_pct: int
    discount_penalty_bp: int
    promo_brand: str
    promo_category: str
    promo_bonus_cents: int
    return_window_days: int
    gst_rate_bp: int = 0  # e.g. 900 for 9% GST; 0 means no GST adjustment


def load_plans(directory: Path = PLANS_DIR) -> tuple[Plan, ...]:
    plans = [Plan(**yaml.safe_load(p.read_text())) for p in sorted(directory.glob("plan_v*.yaml"))]
    if not plans:
        raise FileNotFoundError(f"no plan_v*.yaml in {directory}")
    return tuple(sorted(plans, key=lambda p: p.effective_from))


def plan_for(plans: tuple[Plan, ...], day: date) -> Plan:
    in_force = [p for p in plans if p.effective_from <= day]
    if not in_force:
        raise ValueError(f"no commission plan in force on {day}")
    return in_force[-1]
