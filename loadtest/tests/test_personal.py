"""Trusted scenario loading, common user and UI apply exclusion."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from locust.env import Environment
from locust.event import Events

from loadtest import config, runtime
from loadtest.metrics import Metrics
from test_runtime import TARGET, ORIGIN, CONFIG, login, headers, auth
from test_runtime import runtime as web_runtime


def test_personal_manifest_requires_exact_readonly_mount(tmp_path, monkeypatch):
    value = dict(CONFIG, scenario='personal', scenario_file='/run/personal/scenario.py')
    monkeypatch.setattr(config, 'protected_read', lambda *a: json.dumps(value))
    assert config.load_manifest()['scenario'] == 'personal'
    value['scenario_file'] = '/tmp/untrusted.py'
    with pytest.raises(ValueError):
        config.load_manifest()


def test_load_concrete_classes_and_common_actor(tmp_path, monkeypatch):
    file = tmp_path / 'scenario.py'
    file.write_text('from loadtest.runtime import StageUser\nfrom locust import task\n'
                    'class Personal(StageUser):\n @task\n def call(self): pass\n')
    classes = runtime.load_personal(file)
    assert set(classes) == {'Personal'}
    monkeypatch.setattr(runtime, 'load_manifest', lambda: CONFIG)
    monkeypatch.setattr(runtime, 'load_actor', lambda path: {
        'user_id': 'fixture-user', 'role': 'USER', 'membership': 'vip'})
    environment = Environment(events=Events(), host=TARGET)
    user = classes['Personal'](environment)
    assert user.client.headers['X-User-ID'] == 'fixture-user'
    assert user.client.trust_env is False
    with pytest.raises(ValueError):
        user.client.get('https://elsewhere.invalid/path')
    environment.host = 'https://elsewhere.invalid'
    with pytest.raises(ValueError):
        classes['Personal'](environment)


def test_loader_rejects_missing_classes_and_broken_imports(tmp_path):
    file = tmp_path / 'scenario.py'
    file.write_text('from locust import HttpUser\nclass Arbitrary(HttpUser): pass\n')
    with pytest.raises(ValueError):
        runtime.load_personal(file)
    file.write_text('from loadtest.runtime import StageUser\nclass Empty(StageUser): pass\n')
    with pytest.raises(ValueError):
        runtime.load_personal(file)
    file.write_text('import nonexistent_getddo_personal_dependency\n')
    with pytest.raises(ModuleNotFoundError):
        runtime.load_personal(file)


def test_personal_business_counter_bounded_labels():
    metrics = Metrics(SimpleNamespace(), 2, 'personal')
    metrics.record('success')
    assert metrics.local['success'] == 1
    with pytest.raises(ValueError):
        metrics.record('raw account id')
    with pytest.raises(ValueError):
        Metrics(SimpleNamespace(), 2, 'raw account id')


def test_maintenance_prevents_start_and_refuses_running_apply(web_runtime):
    environment, client, _ = web_runtime
    assert login(client).status_code == 302
    request_headers = headers(client)
    response = client.post('/locust/control/maintenance', base_url=ORIGIN,
                           headers=request_headers, json={'enabled': True})
    assert response.status_code == 200
    response = client.post('/locust/swarm', base_url=ORIGIN, headers=request_headers,
                           data={'user_count': '2', 'spawn_rate': '1', 'run_time': '30s'})
    assert response.status_code == 409
    assert client.post('/locust/control/maintenance', base_url=ORIGIN,
                       headers=request_headers, json={'enabled': False}).status_code == 200
    environment.runner.state = 'running'
    assert client.post('/locust/control/maintenance', base_url=ORIGIN,
                       headers=request_headers, json={'enabled': True}).status_code == 409
    environment.runner.state = 'ready'
