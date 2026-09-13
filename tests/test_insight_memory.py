"""Regression tests for measurement-driven diagnostic memory, independent of SQLite."""
from copy import deepcopy
import json

import pytest

from app.services.insight_memory import reconcile, transition


def card(day=1, finding="anomaly", *, grade="high", deviation=10, **overrides):
    """Create an explicitly supported diagnostic observation at one real instant."""
    result = {
        "ruleId": "fuel-peer-deficit",
        "finding": finding,
        "observedAt": f"2026-09-{day:02d}T12:00:00",
        "level": "warning" if finding == "anomaly" else "info",
        "confidence": {"grade": grade, "reasons": [], "tripCount": 3},
        "evidence": {"tripIds": [f"trip-{day}"]},
        "deviation": ({"metric": "fuel_peer_deficit_pct", "value": deviation, "unit": "%"}
                      if isinstance(deviation, (int, float)) else deviation),
        "title": "Riscontro diagnostico",
    }
    result.update(overrides)
    return result


def evolve(*cards):
    """Apply diagnostic observations in the supplied order."""
    state = None
    for observation in cards:
        state = transition(state, observation)
    return state


def test_new_persistent_improving_and_resolved_use_real_observations():
    """Only distinct supported measurements advance a complete adverse episode."""
    first = card(1)
    state = transition(None, first)
    assert (state["state"], state["observations"], state["adverseCount"]) == ("new", 1, 1)
    assert state["firstSeen"] == state["lastSeen"] == state["episodeStartedAt"] == first["observedAt"]
    assert state["unconfirmed"] is False

    state = transition(state, card(2, deviation=9))
    assert (state["state"], state["adverseCount"]) == ("persistent", 2)
    state = transition(state, card(3, deviation=7))
    assert (state["state"], state["adverseCount"]) == ("improving", 3)
    state = transition(state, card(4, "normal", deviation=0))
    assert (state["state"], state["normalStreak"]) == ("improving", 1)
    state = transition(state, card(5, "normal", deviation=0))
    assert (state["state"], state["normalStreak"], state["observations"]) == ("resolved", 2, 5)
    assert state["firstSeen"] == first["observedAt"]
    assert state["lastSeen"] == state["lastObservationAt"] == card(5)["observedAt"]


@pytest.mark.parametrize("value, expected", [(8, "improving"), (8.001, "persistent"), (10, "persistent"), (12, "persistent")])
def test_comparable_deviation_requires_significant_decrease(value, expected):
    """A same-unit deviation must fall at least twenty percent to show improvement."""
    state = evolve(card(1), card(2, deviation=value))
    assert state["state"] == expected


@pytest.mark.parametrize("deviation", [None, {}, {"metric": "another_metric", "value": 1, "unit": "%"},
                                      {"metric": "fuel_peer_deficit_pct", "value": 1, "unit": "g/km"},
                                      {"metric": "fuel_peer_deficit_pct", "value": -1, "unit": "%"},
                                      {"metric": "fuel_peer_deficit_pct", "value": True, "unit": "%"}])
def test_noncomparable_deviations_cannot_establish_improvement(deviation):
    """Unrelated metrics, different units and invalid measurements are not a trend."""
    state = evolve(card(1), card(2, deviation=deviation))
    assert state["state"] == "persistent"


def test_supported_severity_reduction_can_establish_improvement():
    """Qualitative severity may decrease without a numeric comparable deviation."""
    state = evolve(card(1, deviation=None, level="critical"), card(2, deviation=None, level="warning"))
    assert state["state"] == "improving"


@pytest.mark.parametrize("grade", ["low", "unknown", None])
def test_weak_anomaly_cannot_confirm_persistence_or_improvement(grade):
    """A lower value with inadequate evidence preserves the existing warning."""
    state = evolve(card(1), card(2, grade=grade, deviation=1, level="info"))
    assert state["state"] == "new"
    assert state["adverseCount"] == 1
    assert state["unconfirmed"] is True


def test_first_supported_anomaly_after_weak_evidence_is_still_new():
    """A weak initial warning is not a prior confirmed adverse observation."""
    state = evolve(card(1, grade="low"), card(2, deviation=1))
    assert (state["state"], state["adverseCount"]) == ("new", 1)
    state = transition(state, card(3, deviation=1))
    assert (state["state"], state["adverseCount"]) == ("persistent", 2)


def test_polling_identical_measurement_cannot_resolve_or_change_counts():
    """Repeated API snapshots never manufacture a second recovery observation."""
    initial = evolve(card(1), card(2, "normal", deviation=0))
    state = deepcopy(initial)
    for _ in range(10):
        state = transition(state, card(2, "normal", deviation=0))
    assert state == initial
    assert state["state"] != "resolved"
    result = reconcile([state], [card(2, "normal", deviation=0)])
    assert result == {"states": [state], "changes": [], "history": [], "changed": False}


@pytest.mark.parametrize("intervening", [card(3, "insufficient"), card(3, "information"), card(3, "normal", grade="low")])
def test_new_inadequate_observation_interrupts_recovery_streak(intervening):
    """Two normals separated by insufficient evidence do not confirm recovery."""
    state = evolve(card(1), card(2, "normal", deviation=0), intervening)
    assert state["normalStreak"] == 0
    assert state["state"] != "resolved"
    assert state["unconfirmed"] is True
    state = transition(state, card(4, "normal", deviation=0))
    assert (state["normalStreak"], state["unconfirmed"]) == (1, False)
    assert state["state"] != "resolved"
    assert transition(state, card(5, "normal", deviation=0))["state"] == "resolved"


def test_contrary_adverse_measurement_restarts_normal_streak():
    """A new anomaly between normal measurements prevents premature resolution."""
    state = evolve(card(1), card(2, "normal", deviation=0), card(3), card(4, "normal", deviation=0))
    assert state["normalStreak"] == 1
    assert state["state"] != "resolved"
    assert state["adverseCount"] == 2


def test_new_anomaly_after_resolution_opens_a_new_episode():
    """Lifetime observation dates remain intact when a later warning returns."""
    state = evolve(card(1), card(2, "normal", deviation=0), card(3, "normal", deviation=0), card(4))
    assert (state["state"], state["adverseCount"], state["normalStreak"], state["observations"]) == ("new", 1, 0, 4)
    assert state["episodeStartedAt"] == card(4)["observedAt"]
    assert state["firstSeen"] == card(1)["observedAt"]


@pytest.mark.parametrize("finding", ["normal", "information", "insufficient"])
def test_initial_nonadverse_findings_do_not_fill_alarm_history(finding):
    """Baseline results remain observable without populating an all-clear journal."""
    result = reconcile([], [card(1, finding), card(2, finding)])
    state = result["states"][0]
    assert (state["state"], state["hadAnomaly"], state["adverseCount"]) == ("observing", False, 0)
    assert state["normalStreak"] == 0
    assert result["history"] == []


@pytest.mark.parametrize("overrides", [{"stale": True}, {"isStale": True}, {"evidence": {"tripIds": []}},
                                       {"evidence": {"eventIds": ["event-1"]}}, {"confidence": {}}])
def test_stale_or_unsupported_normals_cannot_resolve(overrides):
    """Stale snapshots or administrative evidence cannot substitute diagnostic trips."""
    state = evolve(card(1), card(2, "normal", **overrides), card(3, "normal", **overrides))
    assert state["state"] == "new"
    assert state["normalStreak"] == 0
    assert state["unconfirmed"] is True


@pytest.mark.parametrize("kind", ["manual", "poll", "configuration"])
def test_nondiagnostic_timestamps_never_move_measurement_cursor(kind):
    """Manual edits and polling dates cannot confirm recovery or block later real trips."""
    first = transition(None, card(1))
    result = reconcile([first], [card(20, "normal", observationKind=kind)])
    state = result["states"][0]
    assert state["state"] == first["state"]
    assert state["observations"] == 1
    assert state["lastObservationAt"] == first["lastObservationAt"]
    assert state["lastCard"] == first["lastCard"]
    assert result["history"] == []
    assert transition(state, card(2))["observations"] == 2
    baseline = transition(None, card(20, observationKind=kind))
    assert (baseline["state"], baseline["observations"], baseline["hadAnomaly"]) == ("observing", 0, False)


@pytest.mark.parametrize("date", [None, "invalid", "2026-09-01", "2026-99-01T12:00:00"])
def test_missing_or_invalid_measurement_dates_cannot_replace_diagnostic_snapshot(date):
    """Undated evaluations mark evidence unconfirmed without fabricating time."""
    initial = transition(None, card(1))
    state = transition(initial, card(2, "normal", observedAt=date))
    assert state["lastCard"] == initial["lastCard"]
    assert state["lastSeen"] == initial["lastSeen"]
    assert state["observations"] == 1
    assert state["unconfirmedReason"] == "no_measurement"
    undated = transition(None, card(1, observedAt=date))
    assert (undated["state"], undated["observations"], undated["lastObservationAt"]) == ("new", 0, None)
    supported = transition(undated, card(2))
    assert (supported["state"], supported["adverseCount"]) == ("new", 1)


def test_out_of_order_data_never_regresses_current_lifecycle_or_snapshot():
    """Old contradictory observations are ignored, regardless of their severity."""
    current = evolve(card(2), card(3, "normal", deviation=0), card(4, "normal", deviation=0))
    for old in [card(1, level="critical"), card(2, "insufficient"), card(3, "normal", grade="low")]:
        assert transition(current, old) == current
    result = reconcile([current], [card(1)])
    assert result["changed"] is False
    assert result["history"] == []


def test_timezone_equivalent_measurements_are_the_same_observation():
    """Offsets and the application's naive Rome time compare as actual instants."""
    state = transition(None, card(1, observedAt="2026-09-01T12:00:00"))
    result = reconcile([state], [card(1, observedAt="2026-09-01T10:00:00Z")])
    revised = result["states"][0]
    assert revised["observations"] == 1
    assert revised["adverseCount"] == 1
    assert revised["state"] == "new"
    assert revised["lastObservationAt"] == "2026-09-01T12:00:00"
    assert result["history"] == []
    assert transition(revised, card(1, observedAt="2026-09-01T12:00:01+02:00"))["observations"] == 2


def test_same_date_changed_evidence_does_not_count_as_another_observation():
    """Additional source files for one instant can revise the snapshot, not progress."""
    state = transition(None, card(1))
    revised_card = card(1, deviation=1, evidence={"tripIds": ["trip-1", "trip-extra"], "eventIds": ["event-1"]})
    revised = transition(state, revised_card)
    assert (revised["state"], revised["observations"], revised["adverseCount"]) == ("new", 1, 1)
    assert revised["lastDeviation"]["value"] == 1
    assert revised["lastCard"] == revised_card
    assert transition(revised, revised_card) == revised


def test_same_date_correction_to_normal_cannot_establish_recovery():
    """A revised adverse result cannot count itself as a new normal measurement."""
    state = transition(None, card(1))
    revised = transition(state, card(1, "normal", deviation=0))
    assert (revised["state"], revised["normalStreak"], revised["observations"]) == ("new", 0, 1)
    assert revised["lastFinding"] == "normal"
    assert revised["unconfirmedReason"] == "revised_measurement"
    state = transition(revised, card(2, "normal", deviation=0))
    assert state["state"] != "resolved"
    assert transition(state, card(3, "normal", deviation=0))["state"] == "resolved"


def test_same_date_correction_from_normal_keeps_warning_without_fabricated_history():
    """A corrected baseline may surface an unconfirmed warning with no new count."""
    state = transition(None, card(1, "normal"))
    result = reconcile([state], [card(1)])
    revised = result["states"][0]
    assert (revised["state"], revised["observations"], revised["adverseCount"]) == ("new", 1, 0)
    assert revised["lastFinding"] == "anomaly"
    assert revised["unconfirmed"] is True
    assert result["history"] == []


@pytest.mark.parametrize("replacement", [card(4), card(4, "insufficient"), card(4, "normal", grade="low")])
def test_corrected_confirming_measurement_invalidates_its_resolution(replacement):
    """A retracted second normal must restore the same adverse episode."""
    state = evolve(card(1), card(2), card(3, "normal", deviation=0), card(4, "normal", deviation=0))
    assert state["state"] == "resolved"
    revised = transition(state, replacement)
    assert revised["state"] != "resolved"
    assert revised["episodeStartedAt"] == card(1)["observedAt"]
    assert revised["adverseCount"] == 2
    assert revised["normalStreak"] == 0
    assert revised["observations"] == 4
    assert revised["unconfirmed"] is True


def test_corrected_improvement_is_revoked_when_evidence_no_longer_improves():
    """Revised same-date deviation can invalidate an improvement, never invent one."""
    state = evolve(card(1), card(2, deviation=6))
    assert state["state"] == "improving"
    revised = transition(state, card(2, deviation=11))
    assert revised["state"] == "persistent"
    assert revised["adverseCount"] == state["adverseCount"]
    assert revised["observations"] == state["observations"]


def test_missing_rule_is_retained_unconfirmed_without_a_fake_event_date():
    """Omission can mark availability loss but never resolve or append a dated event."""
    state = evolve(card(1), card(2))
    result = reconcile([state], [])
    missing = result["states"][0]
    assert missing["state"] == "persistent"
    assert missing["unconfirmedReason"] == "missing"
    assert missing["lastObservationAt"] == state["lastObservationAt"]
    assert missing["observations"] == state["observations"]
    assert result["changed"] is True
    assert result["history"] == []
    assert reconcile(result["states"], [])["changed"] is False
    reappeared = reconcile(result["states"], [card(2)])
    assert reappeared["changed"] is False
    assert reappeared["states"][0]["unconfirmed"] is True
    fresh = reconcile(result["states"], [card(3)])
    assert fresh["states"][0]["unconfirmed"] is False


def test_reconcile_orders_new_observations_and_keeps_rules_independent():
    """Batch arrival order does not invert the actual measurement sequence."""
    result = reconcile([], [card(3, "normal", deviation=0), card(1), card(2, "normal", deviation=0),
                            card(1, ruleId="airflow", finding="information")])
    assert [state["ruleId"] for state in result["states"]] == ["airflow", "fuel-peer-deficit"]
    state = result["states"][1]
    assert (state["state"], state["observations"], state["normalStreak"]) == ("resolved", 3, 2)
    assert [item["state"] for item in result["history"]] == ["new", "improving", "resolved"]
    assert [item["observedAt"] for item in result["history"]] == [card(day)["observedAt"] for day in (1, 2, 3)]


def test_history_is_idempotent_and_does_not_fill_with_normals_after_resolution():
    """Replaying imports and polling snapshots produce no duplicate history."""
    cards = [card(1), card(2), card(3, "normal", deviation=0), card(4, "normal", deviation=0)]
    result = reconcile([], cards)
    assert len(result["history"]) == 4
    replay = reconcile(result["states"], cards)
    assert replay["changed"] is False
    assert replay["history"] == []
    next_normal = reconcile(result["states"], [card(5, "normal", deviation=0)])
    assert next_normal["changed"] is True
    assert next_normal["states"][0]["state"] == "resolved"
    assert next_normal["history"] == []


def test_reconcile_cutoff_is_only_an_upper_bound_not_a_freshness_policy():
    """A far-future evaluation clock does not age evidence into artificial recovery."""
    result = reconcile([], [card(1), card(2)], as_of=card(1)["observedAt"])
    assert result["states"][0]["observations"] == 1
    assert len(result["history"]) == 1
    current = transition(result["states"][0], card(2))
    old_query = reconcile([current], [], as_of=card(1)["observedAt"])
    assert old_query["changed"] is False
    assert old_query["history"] == []
    future_query = reconcile([current], [card(2)], as_of="2030-01-01T12:00:00")
    assert future_query["changed"] is False
    assert future_query["states"][0]["unconfirmed"] is False


def test_pure_functions_and_strict_json_round_trip_are_deterministic():
    """Evidence, previous memory and history snapshots do not alias caller objects."""
    observation = card(1)
    original_observation = deepcopy(observation)
    previous = transition(None, observation)
    original_previous = deepcopy(previous)
    observations = [card(2), card(3, "normal", deviation=0)]
    original_observations = deepcopy(observations)
    result = reconcile([previous], observations)
    assert previous == original_previous
    assert observation == original_observation
    assert observations == original_observations
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert reconcile([previous], observations) == result
    result["history"][-1]["card"]["evidence"]["tripIds"].append("changed")
    assert result["states"][0]["lastEvidenceTripIds"] == ["trip-3"]
    assert result["states"][0]["lastCard"]["evidence"]["tripIds"] == ["trip-3"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_deviations_are_serializable_and_never_improvement(value):
    """Non-finite measurements are retained as null and cannot supply trend proof."""
    state = evolve(card(1), card(2, deviation=value))
    assert state["state"] == "persistent"
    assert state["lastDeviation"] is None
    assert state["lastCard"]["deviation"]["value"] is None
    json.dumps(state, allow_nan=False)


@pytest.mark.parametrize("observation", [{}, {"ruleId": "", "finding": "normal"},
                                        {"ruleId": "rule", "finding": "invalid"}, None])
def test_invalid_cards_fail_explicitly(observation):
    """Programming errors must not silently create a lifecycle record."""
    with pytest.raises(ValueError):
        transition(None, observation)


def test_cannot_mix_rules_or_invalid_lifecycle_or_invalid_cutoff():
    """Stable identities and valid timestamps are persistence preconditions."""
    state = transition(None, card(1))
    with pytest.raises(ValueError, match="different diagnostic rules"):
        transition(state, card(2, ruleId="another-rule"))
    with pytest.raises(ValueError, match="lifecycle"):
        transition({**state, "state": "not-a-state"}, card(2))
    with pytest.raises(ValueError, match="as_of"):
        reconcile([state], [], as_of="today")
