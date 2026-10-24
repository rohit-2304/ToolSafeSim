"""Shared deterministic helpers for fixture generation. No network, no real data."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 22, 9, 0, 0, tzinfo=timezone.utc)
INTERNAL_DOMAIN = "northwind.example.test"

FIRST = ["Aarav", "Meera", "Liam", "Sofia", "Noah", "Isha", "Ethan", "Zara", "Kabir", "Emma", "Rahul",
         "Chloe", "Arjun", "Nina", "Owen", "Tara", "Felix", "Anika", "Marcus", "Leila", "Dev", "Hana",
         "Victor", "Sana", "Jonas", "Ritu", "Caleb", "Farah", "Theo", "Ananya"]
LAST = ["Shah", "Patel", "Nguyen", "Okafor", "Brandt", "Iyer", "Moreau", "Haddad", "Kowalski", "Rao",
        "Lindqvist", "Mehta", "Castillo", "Whitfield", "Banerjee", "Tanaka", "Osei", "Fischer",
        "Gupta", "Romero", "Chandra", "Ivanov", "Kapoor", "Bergström", "Dsouza"]
COMPANIES = ["Brightwave Logistics", "Helix Office Supply", "Cobalt Cloud Services", "Maple & Finch Catering",
             "Quanta Print Works", "Riverstone Consulting", "Zenith Packaging", "Orchid Analytics",
             "Foundry Hardware", "Lumen Facilities", "Ardent Freight", "Pinecrest Software"]


def rng_for(pack: str, seed: int = 0) -> random.Random:
    return random.Random(f"{pack}:{seed}")


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def days_ago(n: float, hour: int | None = None) -> datetime:
    d = NOW - timedelta(days=n)
    return d.replace(hour=hour, minute=0, second=0) if hour is not None else d


def person(r: random.Random) -> tuple[str, str]:
    f, l = r.choice(FIRST), r.choice(LAST)
    return f"{f} {l}", f"{f}.{l}".lower().replace("ö", "o")


def slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-")


def company_domain(name: str) -> str:
    return f"{slug(name).replace('--', '-')}.example.test"
