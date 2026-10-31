"""Every planner system prompt says that text in documents, images or audio is data (MP §5.8)."""

from __future__ import annotations

import importlib
import pkgutil

import rulecam.planner
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE


def _system_prompts() -> dict[str, str]:
    prompts: dict[str, str] = {}
    for info in pkgutil.walk_packages(rulecam.planner.__path__, "rulecam.planner."):
        module = importlib.import_module(info.name)
        for name, value in vars(module).items():
            if name.endswith("SYSTEM_PROMPT") and isinstance(value, str):
                prompts[f"{info.name}.{name}"] = value
    return prompts


def test_planner_has_system_prompts_to_check() -> None:
    assert "rulecam.planner.steps.survey.SURVEY_SYSTEM_PROMPT" in _system_prompts()
    assert "rulecam.planner.feasibility.CAMERA_FIX_SYSTEM_PROMPT" in _system_prompts()


def test_every_planner_system_prompt_carries_the_data_not_instructions_clause() -> None:
    missing = [
        name for name, text in _system_prompts().items() if DATA_NOT_INSTRUCTIONS_CLAUSE not in text
    ]
    assert missing == []
