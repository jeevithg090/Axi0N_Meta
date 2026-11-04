"""Guardrail (rule R13): the staff app times roles and shifts, never people.

No staff model has a field for a person, a name, a face, a track or a device, so nothing per
person can be stored, served or counted. Anyone may add checks here; nobody may weaken them.
"""

from __future__ import annotations

import inspect
import re

from pydantic import BaseModel

import rulecam.staff.models as staff_models
import rulecam.staff.store as staff_store

PERSON_FIELD = re.compile(
    r"track|person|people|worker|employee|face|identity|name|user|device|phone_id|email",
    re.IGNORECASE,
)
# `camera_name` names a camera, never a person.
ALLOWED = frozenset({"camera_name"})


def _models() -> list[type[BaseModel]]:
    found = []
    for module in (staff_models, staff_store):
        found += [
            obj
            for _, obj in inspect.getmembers(module, inspect.isclass)
            if issubclass(obj, BaseModel) and obj.__module__ == module.__name__
        ]
    return found


def test_no_staff_model_has_a_field_for_a_person() -> None:
    assert _models()
    for model in _models():
        for name in model.model_fields:
            assert name in ALLOWED or not PERSON_FIELD.search(name), f"{model.__name__}.{name}"
