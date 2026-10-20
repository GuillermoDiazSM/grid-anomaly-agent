from datetime import date

import pytest

from grid_anomaly_agent import redata


def test_chunks_never_cross_a_month():
    chunks = list(redata._chunks(date(2024, 11, 15), date(2025, 2, 3), "month"))
    assert chunks == [(date(2024, 11, 15), date(2024, 11, 30)), (date(2024, 12, 1), date(2024, 12, 31)),
                      (date(2025, 1, 1), date(2025, 1, 31)), (date(2025, 2, 1), date(2025, 2, 3))]


def test_chunks_by_year():
    assert list(redata._chunks(date(2024, 10, 1), date(2025, 3, 1), "year")) == [
        (date(2024, 10, 1), date(2024, 12, 31)), (date(2025, 1, 1), date(2025, 3, 1))]


def test_parse_response():
    payload = {"included": [{"attributes": {"title": "Demand", "values": [
        {"value": 1.5, "datetime": "2025-01-01T00:00:00.000+01:00"},
        {"value": 2.5, "datetime": "2025-01-02T00:00:00.000+01:00"}]}}]}
    df = redata.parse_response(payload)
    assert list(df.columns) == ["series", "ts_utc", "value"]
    assert df["ts_utc"].iloc[0].isoformat() == "2024-12-31T23:00:00+00:00"


def test_api_errors_raise():
    with pytest.raises(RuntimeError):
        redata.parse_response({"errors": [{"detail": "unavailable"}]})
