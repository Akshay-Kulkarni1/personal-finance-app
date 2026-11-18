"""Freeze the deterministic legacy Maya demo to its reference date.

Otherwise demo forecast assertions change with the calendar, unrelated to code changes.
Real user auth and banking tests use actual Unix time and user-scoped stores.
"""
from datetime import date
import pytest


class DemoDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 8)


@pytest.fixture(autouse=True)
def stable_demo_date(monkeypatch):
    import ledger
    import persona
    monkeypatch.setattr(ledger, "date", DemoDate)
    monkeypatch.setattr(persona, "date", DemoDate)
