"""Planner titles reject PostgreSQL TEXT's forbidden NUL before persistence."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from project.miniapp.planner_api import CreateGoal, CreateTask


@pytest.mark.parametrize('model', [CreateGoal, CreateTask], ids=['goal', 'task'])
def test_planner_title_rejects_embedded_nul(model):
    with pytest.raises(ValidationError):
        model(id=uuid4(), title='Позвонить\x00специалисту')


@pytest.mark.parametrize('model', [CreateGoal, CreateTask], ids=['goal', 'task'])
def test_planner_title_preserves_russian_and_internal_spaces(model):
    result = model(id=uuid4(), title='  Собрать  документы для жилья  ')
    assert result.title == 'Собрать  документы для жилья'
