import os
import time
from unittest.mock import patch

import pytest

from app import analyzer


def test_rate_limiter_allows_calls_under_limit():
    # configure small limit for test
    analyzer.openai_rate_limiter = analyzer.RateLimiter(max_calls=5, period=1.0)
    start = time.time()
    # Should not sleep significantly for 5 quick acquires
    for _ in range(5):
        analyzer.openai_rate_limiter.acquire()
    elapsed = time.time() - start
    assert elapsed < 0.5


def test_rate_limiter_blocks_when_over_limit():
    # limit 2 calls per 1 second
    analyzer.openai_rate_limiter = analyzer.RateLimiter(max_calls=2, period=1.0)
    analyzer.openai_rate_limiter.acquire()
    analyzer.openai_rate_limiter.acquire()
    start = time.time()
    # third acquire should block ~1 second
    analyzer.openai_rate_limiter.acquire()
    elapsed = time.time() - start
    assert elapsed >= 0.9


@patch("openai.ChatCompletion.create")
def test_call_openai_respects_rate_limit(mock_create):
    # mock response
    mock_create.return_value = {"choices": [{"message": {"content": "OK"}}]}
    analyzer.openai_rate_limiter = analyzer.RateLimiter(max_calls=1, period=1.0)

    # first call
    analyzer.openai_rate_limiter.acquire()

    start = time.time()
    # second call should wait ~1s before allowing real call
    res = analyzer._call_openai("sys", "user")
    elapsed = time.time() - start
    assert res == "OK"
    assert elapsed >= 0.9
