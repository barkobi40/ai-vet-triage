import pytest

from app.models.triage import Priority
from app.routers.triage import _classify_mock_priority


@pytest.mark.parametrize(
    "description",
    [
        "My dog is choking on something",
        "Pet is choking and can't breathe",
        "Cat is unresponsive and not breathing",
        "Severe bleeding from a wound, won't stop",
        "Dog collapsed suddenly",
        "Having a seizure right now",
    ],
)
def test_life_threatening_symptoms_classified_critical(description):
    assert _classify_mock_priority(description) == Priority.RED


@pytest.mark.parametrize(
    "description",
    [
        "Deep cut on the leg, bleeding a bit",
        "Persistent vomiting since yesterday",
        "Was hit by a car this morning",
        "Dog seems to be in severe pain",
        "Possible broken bone after a fall",
    ],
)
def test_urgent_symptoms_classified_urgent(description):
    assert _classify_mock_priority(description) == Priority.YELLOW


@pytest.mark.parametrize(
    "description",
    [
        "Mild diarrhea since this morning, otherwise normal",
        "Diarrhea but eating and playing normally",
        "Mild limping on the back leg",
        "Scratching and itchy skin for a few days",
        "Routine follow-up after last week's visit",
    ],
)
def test_mild_symptoms_classified_non_urgent(description):
    assert _classify_mock_priority(description) == Priority.GREEN


def test_severe_bleeding_is_critical_not_just_urgent():
    """"severe bleeding" contains the substring "bleeding", which alone is
    an URGENT keyword — CRITICAL must still win since it's checked first."""
    assert _classify_mock_priority("Severe bleeding after an accident") == Priority.RED


def test_plain_bleeding_without_severity_is_urgent_not_critical():
    assert _classify_mock_priority("Small amount of bleeding from a scratch") == Priority.YELLOW


def test_unrecognized_description_falls_back_to_a_valid_priority():
    result = _classify_mock_priority("Something is a bit off with my pet today")
    assert result in (Priority.RED, Priority.YELLOW, Priority.GREEN)


def test_empty_description_does_not_crash():
    result = _classify_mock_priority(None)
    assert result in (Priority.RED, Priority.YELLOW, Priority.GREEN)
