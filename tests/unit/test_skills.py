"""FUND skills mechanism: scan/resolve/catalog over the shipped SKILL.md files."""

from pathlib import Path

import pytest

from fund_models.skills import scan_skills
from src import config
from src.agents import skills

# The five Phase 1 agent skills, one per node (tailoring composes two), plus
# the Phase 3 cover-letter skill (which also composes anti-fabrication).
EXPECTED_SKILLS = {
    "source-extraction",
    "profile-synthesis",
    "job-analysis",
    "cv-tailoring",
    "anti-fabrication",
    "cover-letter",
}


def test_scan_skills_finds_all_shipped():
    registry = scan_skills(config.SKILLS_DIR)
    names = {s["name"] for s in registry}
    assert names == EXPECTED_SKILLS
    for s in registry:
        assert s["description"]  # frontmatter description present
        assert Path(s["path"]).name == "SKILL.md"
        assert Path(s["path"]).exists()


def test_resolve_skill_strips_frontmatter():
    body = skills.resolve_skill("cv-tailoring")
    assert "HARD RULES" in body
    # Frontmatter keys must not leak into the resolved body.
    assert "name:" not in body
    assert "description:" not in body
    assert not body.startswith("---")


def test_resolve_skill_raises_on_unknown_name():
    with pytest.raises(KeyError):
        skills.resolve_skill("no-such-skill")


def test_resolve_skill_degrades_when_dir_missing(monkeypatch, tmp_path):
    # Missing SKILLS_DIR → empty registry → "" (graceful), not a KeyError.
    monkeypatch.setattr(config, "SKILLS_DIR", tmp_path / "absent")
    assert skills.resolve_skill("cv-tailoring") == ""
    assert skills.skills_catalog() == ""


def test_skills_catalog_lists_every_skill():
    catalog = skills.skills_catalog()
    for name in EXPECTED_SKILLS:
        assert name in catalog
    assert catalog.startswith("## Available Skills")
