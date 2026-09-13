"""Integration coverage for manual history, persisted diagnostics and causal period reads."""
import pytest
from test_operations import client
from test_period_cache_refuels import trip, seed
from app import main


def test_maintenance_crud_archive_and_restore_preserve_measurements(client):
    """Only explicit manual rows change; archiving remains reversible and date scoped."""
    seed(trip('original', '2026-09-01T10:00:00'))
    before = main.db.get_all_trips()
    payload = {'ts': '2026-09-03T12:30', 'type': 'oil_change', 'odometerKm': 12345, 'note': ''}
    response = client.post('/api/v1/maintenance', json=payload)
    assert response.status_code == 200, response.text
    record = response.json()
    assert record['ts'] == '2026-09-03T12:30:00'
    identifier = record['id']
    url = f'/api/v1/maintenance/{identifier}'
    assert client.get('/api/v1/events?to_date=2026-09-02').json()['maintenance'] == []
    timeline = client.get('/api/v1/events?from_date=2026-09-03').json()
    assert timeline['events'][0]['maintenanceId'] == identifier
    assert client.put(url, json={**payload, 'note': 'intervento registrato'}).json()['note'] == 'intervento registrato'
    assert client.delete(url).json() == {'id': identifier, 'archived': True}
    assert main.db.get_maintenance() == []
    assert client.get('/api/v1/events').json()['maintenance'][0]['archived'] is True
    assert client.put(url, json={**payload, 'archived': False}).json()['archived'] is False
    assert client.delete('/api/v1/maintenance/999').status_code == 404
    assert client.put('/api/v1/maintenance/999', json=payload).status_code == 404
    assert main.db.get_all_trips() == before
    assert main.db.get_refuels() == []


@pytest.mark.parametrize('patch', [
    {'ts': '2026-02-30T12:00'}, {'ts': '2026-09-01T12:00Z'}, {'ts': None},
    {'odometerKm': True}, {'odometerKm': 'NaN'}, {'odometerKm': -1},
    {'type': 'unknown'}, {'archived': 'true'}, {'unexpected': 1}, {'note': 'a'*2001},
])
def test_maintenance_validation_is_atomic(client, patch):
    """Invalid form input cannot create a partial intervention or modify an existing one."""
    value = {'ts': '2026-09-01T12:00', 'type': 'service', 'note': ''}
    original = client.post('/api/v1/maintenance', json=value).json()
    assert client.post('/api/v1/maintenance', json={**value, **patch}).status_code == 422
    assert client.put(f"/api/v1/maintenance/{original['id']}", json={**value, **patch}).status_code == 422
    assert main.db.get_maintenance() == [original]


def test_memory_survives_restart_and_period_reads_do_not_advance_it(client):
    """A second page load, historical period or DB reopen is not a new observation."""
    seed(*[trip(f'battery-{day}', f'2026-09-{day:02d}T10:00:00', source='obd',
                batteryStartupV=8.5, pidValues={'bat_v': {'mean': 8.5, 'samples': 100}}) for day in range(1, 7)])
    first = client.get('/api/v1/dashboard')
    assert first.status_code == 200, first.text
    states, history = main.db.get_insight_states(), main.db.get_insight_history()
    assert history and any(state['state'] == 'new' for state in states)
    for query in ('', '?from_date=2026-09-01&to_date=2026-09-03', '?from_date=2026-09-06'):
        assert client.get('/api/v1/dashboard'+query).status_code == 200
        assert client.get('/api/v1/events'+query).status_code == 200
    assert main.db.get_insight_states() == states
    assert main.db.get_insight_history() == history
    main.db.init(main.DB_PATH)
    assert client.get('/api/v1/dashboard').status_code == 200
    assert main.db.get_insight_states() == states
    assert main.db.get_insight_history() == history
    assert client.get('/api/v1/trips/battery-1').json()['id'] == 'battery-1'
    # A reported repair is not evidence that a battery recovered.
    assert client.post('/api/v1/maintenance', json={'ts': '2026-09-05T12:00', 'type': 'battery'}).status_code == 200
    assert client.get('/api/v1/dashboard').status_code == 200
    assert not any(state['state'] == 'resolved' for state in main.db.get_insight_states())
    assert main.db.get_insight_history() == history


def test_dpf_timeline_is_observational_and_deduplicated(client):
    """Repeated reads return source observations without inventing cycles or engine shutdowns."""
    seed(trip('dpf-active', '2026-09-01T10:00:00', source='obd', dpfRegenState='active'))
    first = client.get('/api/v1/events').json()
    second = client.get('/api/v1/events').json()
    dpf = [event for event in first['events'] if event['kind'] == 'dpf']
    assert len(dpf) == 1 and dpf[0]['tripId'] == 'dpf-active'
    assert 'ultima osservazione' in dpf[0]['title']
    assert first == second
    assert main.db.get_maintenance() == []
