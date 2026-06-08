import os
import json
from fastapi.testclient import TestClient

import pytest

from app.main import app


class DummyScraper:
    @staticmethod
    def scrape_url(url: str, timeout: int = 30000):
        return {"html": "<html></html>", "screenshot_desktop": "data/screenshots/dummy_desktop.png", "screenshot_mobile": "data/screenshots/dummy_mobile.png"}


class DummyAnalyzer:
    @staticmethod
    def analyze_images(desktop, mobile, html=""):
        return {"score": 7, "reason": "Looks modern enough (dummy)."}


@pytest.fixture(autouse=True)
def patch_dependencies(monkeypatch):
    # Patch scraper and analyzer to avoid real network/browser calls
    monkeypatch.setattr("app.main.scraper", DummyScraper)
    monkeypatch.setattr("app.main.analyzer", DummyAnalyzer)
    yield


def test_scan_url_creates_lead(tmp_path):
    client = TestClient(app)
    payload = {"website_url": "https://example.com", "business_name": "Example Co"}
    r = client.post("/scan-url", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["website_url"] == payload["website_url"]
    assert data["score"] == 7


def test_batch_scan_accepts_and_enqueues(monkeypatch):
    client = TestClient(app)
    payload = {"website_urls": ["https://a.co", "https://b.co"], "business_name": "Batch"}
    r = client.post("/batch-scan", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data.get("accepted") == 2
