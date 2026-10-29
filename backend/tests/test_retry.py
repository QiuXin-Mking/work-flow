import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.store import Store


def test_retry_fences_old_attempt_and_retains_history(tmp_path):
    store = Store(tmp_path / 'db.sqlite')
    uid = store.create_user('alice', 'secret one')
    job = store.create_job('url', ['acme/server'], uid, {'title': 'Bug'})
    first = store.claim_next_job('worker')
    store.append_event(job, 'evidence', {'root_cause': 'old'}, epoch=first['epoch'])
    store.set_job_status(job, 'failed', epoch=first['epoch'], error='old failure')
    client = TestClient(create_app(store, None, {}))
    client.cookies.set('session', store.create_session(uid))
    response = client.post(f'/api/jobs/{job}/retry')
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'queued' and data['error'] is None
    assert data['evidence'] == {}
    assert any(e['kind'] == 'evidence' for e in data['events'])
    assert data['events'][-1]['kind'] == 'retry'
    assert not store.set_job_status(job, 'failed', epoch=first['epoch'])
    assert not store.append_event(job, 'evidence', {}, epoch=first['epoch'])
    assert client.post(f'/api/jobs/{job}/retry').status_code == 409
    second = store.claim_next_job('worker')
    assert second['attempt_id'] != first['attempt_id']
    assert second['epoch'] > first['epoch']
    store.append_event(job, 'evidence', {'root_cause': 'new'}, epoch=second['epoch'])
    assert client.get(f'/api/jobs/{job}').json()['evidence'] == {'root_cause': 'new'}
    assert client.post('/api/jobs/missing/retry').status_code == 404
    assert TestClient(client.app).post(f'/api/jobs/{job}/retry').status_code == 401
    other = store.create_user('bob', 'secret two')
    client.cookies.set('session', store.create_session(other))
    assert client.post(f'/api/jobs/{job}/retry').status_code == 403


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'unreproduced', 'diagnosis_only'])
def test_retryable_terminal_states(tmp_path, status):
    store = Store(tmp_path / 'db.sqlite')
    uid = store.create_user('alice', 'secret one')
    job = store.create_job('url', ['acme/server'], uid)
    store.set_job_status(job, status)
    assert store.retry_job(job, uid)
