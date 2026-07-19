"""GitHub client with a mocked httpx transport — no network."""

import base64
import json

import httpx

from src.tools.github_client import API_BASE, fetch_github_profile, free_text_source


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/users/alice/repos":
        return httpx.Response(
            200,
            json=[
                {
                    "name": "backtester",
                    "description": "Distributed backtesting engine",
                    "language": "Python",
                    "stargazers_count": 42,
                    "fork": False,
                },
                {"name": "forked-repo", "fork": True},
            ],
        )
    if path == "/repos/alice/backtester/languages":
        return httpx.Response(200, json={"Python": 12345, "Dockerfile": 200})
    if path == "/repos/alice/backtester/readme":
        content = base64.b64encode(b"# Backtester\nA distributed engine.").decode()
        return httpx.Response(200, json={"content": content})
    return httpx.Response(404, json={"message": "not found"})


def test_fetch_github_profile_mocked():
    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url=API_BASE)
    doc = fetch_github_profile("alice", client=client)
    assert doc.id == "github:alice"
    assert doc.source_type == "github"
    assert "Repository: backtester" in doc.raw_text
    assert "Languages: Python, Dockerfile" in doc.raw_text
    assert "A distributed engine." in doc.raw_text
    # forks are excluded
    assert "forked-repo" not in doc.raw_text


def test_free_text_source():
    doc = free_text_source("  I also mentor junior devs.  ")
    assert doc.id == "free_text"
    assert doc.raw_text == "I also mentor junior devs."
