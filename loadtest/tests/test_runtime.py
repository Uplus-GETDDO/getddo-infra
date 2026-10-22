"""Runtime checks against Locust itself, without Docker or AWS.

Run: python -m pytest -q loadtest/tests
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gevent
import pytest
from locust import HttpUser, constant, task
from locust.env import Environment
from locust.event import Events
from locust.stats import StatsCSVFileWriter
import requests
from werkzeug.security import generate_password_hash

from loadtest import config
from loadtest.control import Control, LoginToken, credential
from loadtest.metrics import Metrics
from loadtest.scenario import LockedSession, envelope_result, events_page, smoke_events
from loadtest.web import install_web, REPORT_FILES

ORIGIN = 'https://fixture.cloudfront.net'
TARGET = 'http://internal-stage.ap-northeast-2.elb.amazonaws.com'
CONFIG = {'environment': 'staging', 'target': TARGET, 'public_origin': ORIGIN,
          'expected_workers': 2, 'scenario': 'smoke_events', 'actor_file': '/run/scenario/actor.json'}
PASSWORD = 'synthetic-test-password'


@pytest.fixture(scope='module')
def auth():
    return ('fixture-admin', generate_password_hash(PASSWORD), 'synthetic-session-secret-' * 3)


@pytest.fixture
def runtime(tmp_path, auth, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['locust'])
    environment = Environment(events=Events(), host=TARGET, locustfile='Stage smoke')
    runner = environment.create_local_runner()
    runner.clients = {name: SimpleNamespace(state='ready') for name in ('a', 'b')}
    metrics = Metrics(environment, 2)
    for name in runner.clients:
        metrics.receive(name, {'stage_business': {}})
    environment.stage_metrics = metrics
    writer = StatsCSVFileWriter(environment, [0.5, 0.95, 0.99], str(tmp_path / 'run'), full_history=True)
    writer_greenlet = gevent.spawn(writer)
    environment.create_web_ui('127.0.0.1', 0, web_base_path='/locust', web_login=True,
                              delayed_start=True, stats_csv_writer=writer)
    install_web(environment, CONFIG, auth, tmp_path)
    gevent.sleep(0)
    client = environment.web_ui.app.test_client()
    yield environment, client, tmp_path
    environment.web_ui._cancel_run_time_timer()
    if environment.web_ui._swarm_greenlet is not None:
        environment.web_ui._swarm_greenlet.kill()
    runner.quit()
    writer_greenlet.kill()
    writer.close_files()


def login(client, password=PASSWORD, username='fixture-admin'):
    page = client.get('/locust/login', base_url=ORIGIN)
    parser = LoginToken()
    parser.feed(page.text)
    assert parser.token
    response = client.post('/locust/login_submit', base_url=ORIGIN,
                           headers={'Origin': ORIGIN, 'X-CSRF-Token': parser.token},
                           data={'username': username, 'password': password})
    return response


def headers(client):
    response = client.get('/locust/control/session', base_url=ORIGIN)
    return {'Origin': ORIGIN, 'X-CSRF-Token': response.json['csrf']}


def test_login_subpath_assets_cookie_and_unauthenticated_controls(runtime):
    environment, client, _ = runtime
    assert client.get('/locust/', base_url=ORIGIN).status_code == 302
    for path in ('/swarm', '/stop', '/stats/reset', '/control/collect'):
        assert client.post('/locust' + path, base_url=ORIGIN).status_code == 401
    assert client.get('/locust/control/session', base_url=ORIGIN).status_code == 401
    assert login(client).status_code == 302
    cookie = client.get_cookie('locust_session', domain='fixture.cloudfront.net', path='/locust')
    assert cookie.secure and cookie.http_only and cookie.same_site == 'Strict'
    page = client.get('/locust/', base_url=ORIGIN)
    assert page.status_code == 200
    assets = re.findall(r'(?:src|href)="([^"]*/assets/[^"]+)"', page.text)
    assert assets
    for asset in assets:
        asset = urlsplit(urljoin(ORIGIN + '/locust/', asset)).path
        assert asset.startswith('/locust/assets/')
        assert client.get(asset, base_url=ORIGIN).status_code == 200
    assert client.get('/locust/metrics', base_url=ORIGIN).status_code == 404
    assert client.get('/metrics', base_url=ORIGIN).status_code == 404
    assert environment.runner.user_count == 0


def test_login_csrf_origin_and_bounded_attempts(runtime):
    _, client, _ = runtime
    assert client.post('/locust/login_submit', base_url=ORIGIN,
                       data={'username': 'fixture-admin', 'password': PASSWORD}).status_code == 403
    for index in range(5):
        assert login(client, password='bad', username='未知').status_code == 401
    assert login(client).status_code == 429


def test_control_rejects_csrf_origin_host_and_config_overrides(runtime, monkeypatch):
    environment, client, _ = runtime
    assert login(client).status_code == 302
    valid = {'user_count': '2', 'spawn_rate': '1', 'run_time': '30', 'host': TARGET, 'profile': ''}
    auth_headers = headers(client)
    for bad_headers in ({}, {'Origin': ORIGIN}, {'Origin': 'https://evil.example',
                        'X-CSRF-Token': auth_headers['X-CSRF-Token']}):
        assert client.post('/locust/swarm', base_url=ORIGIN, headers=bad_headers, data=valid).status_code == 403
    for override in ({'host': 'http://production.example'}, {'profile': 'other'},
                     {'user_classes': 'Other'}, {'run_time': ''}, {'spawn_rate': 'nan'}, {'user_count': '-1'}):
        response = client.post('/locust/swarm', base_url=ORIGIN, headers=auth_headers, data=valid | override)
        assert response.status_code == 400
    calls = []
    monkeypatch.setattr(environment.runner, 'start', lambda users, rate: calls.append((users, rate)))
    response = client.post('/locust/swarm', base_url=ORIGIN, headers=auth_headers, data=valid)
    assert response.status_code == 200
    assert client.post('/locust/swarm', base_url=ORIGIN, headers=auth_headers, data=valid).status_code == 409
    gevent.sleep(0)
    assert calls == [(2, 1.0)]
    assert environment.host == TARGET
    assert client.post('/locust/stats/reset', base_url=ORIGIN, headers=auth_headers).status_code == 403
    assert client.post('/locust/user', base_url=ORIGIN, headers=auth_headers).status_code == 403
    assert client.post('/locust/stop', base_url=ORIGIN, headers=auth_headers).status_code == 200


def test_worker_absent_stale_and_idle_collection(runtime):
    environment, client, reports = runtime
    assert login(client).status_code == 302
    auth_headers = headers(client)
    metrics = environment.stage_metrics
    metrics.seen['a'] -= 11
    assert not metrics.healthy()
    valid = {'user_count': '2', 'spawn_rate': '1', 'run_time': '1'}
    assert client.post('/locust/swarm', base_url=ORIGIN, headers=auth_headers, data=valid).status_code == 409
    metrics.seen['a'] = time.monotonic()
    del environment.runner.clients['b']
    assert metrics.worker_counts() == (1, 1)
    assert not metrics.healthy()
    environment.runner.clients['b'] = SimpleNamespace(state='ready')
    response = client.post('/locust/control/collect', base_url=ORIGIN, headers=auth_headers)
    assert response.status_code == 200
    assert set(response.json['files']) == set(REPORT_FILES)
    for name in REPORT_FILES:
        download = client.get('/locust/control/reports/' + name, base_url=ORIGIN)
        assert download.status_code == 200
        assert (reports / 'collected' / name).stat().st_mode & 0o777 == 0o600
    metadata = json.loads((reports / 'collected/metadata.json').read_text())
    assert metadata['started_at'] is None and metadata['parameters'] is None
    assert metadata['locust_version'] == '2.47.0'
    assert 'user_id' not in json.dumps(metadata)
    assert 'NaN' in metadata['metrics']


def test_master_global_histogram_and_cumulative_worker_reports(runtime):
    environment, _, _ = runtime
    metrics = environment.stage_metrics
    # Deliberately imbalanced worker histograms: global p95 must be 100ms,
    # rather than the average of each worker's p95 (550ms).
    for count, duration in ((100, 100), (1, 1000)):
        worker = Environment(events=Events())
        for _ in range(count):
            worker.stats.log_request('GET', 'smoke_events', duration, 10)
        environment.stats.total.extend(worker.stats.total)
    text = metrics.render()
    assert 'locust_requests_total 101' in text
    assert 'quantile="0.95",scope="cumulative"} 0.1' in text
    assert 'quantile="0.99",scope="cumulative"} 0.1' in text
    assert 'locust_data_fresh 1' in text
    metrics.receive('a', {'stage_business': {'success': 4, 'rate_limited': 1}})
    metrics.receive('a', {'stage_business': {'success': 4, 'rate_limited': 1}})
    metrics.receive('b', {'stage_business': {'success': 2}})
    metrics.receive('a', {'stage_business': {'success': 'invalid'}})
    metrics.receive('a', {'stage_business': {'success': 5, 'rate_limited': 1}})
    assert metrics.business['success'] == 7
    assert metrics.business['rate_limited'] == 1
    environment.stats.total.last_request_timestamp = time.time() - 11
    assert 'locust_rps NaN' in metrics.render()
    assert 'quantile="0.95",scope="cumulative"} NaN' in metrics.render()


def test_latency_without_measured_durations_is_no_data(runtime):
    environment, _, _ = runtime
    environment.stats.log_request('GET', 'smoke_events', None, 0)
    text = environment.stage_metrics.render()
    assert 'locust_requests_total 1' in text
    assert 'quantile="0.5",scope="cumulative"} NaN' in text
    assert 'quantile="0.5",scope="rolling"} NaN' in text


@pytest.mark.parametrize('url', ['http://production.example/', '//production.example/', r'/\production.example/'])
def test_session_target_lock(url):
    environment = Environment(events=Events())
    session = LockedSession(TARGET, environment.events.request, None)
    with pytest.raises(ValueError):
        session.get(url)
    with pytest.raises(ValueError):
        session.get('/api/v1/events', headers={'Host': 'production.example'})
    session.headers['Host'] = 'production.example'
    with pytest.raises(ValueError):
        session.get('/api/v1/events')


@pytest.mark.parametrize('status,body,result', [
    (0, {}, 'transport_error'), (429, {}, 'rate_limited'), (503, {}, 'server_error'),
    (200, {}, 'invalid_envelope'),
    (200, {'success': False, 'code': 'SUCCESS', 'message': '', 'data': {}}, 'invalid_envelope'),
    (403, {'success': False, 'code': 'POLICY', 'message': '', 'data': None}, 'expected_rejection'),
    (404, {'success': False, 'code': 'MISSING', 'message': '', 'data': None}, 'http_error'),
    (200, {'success': True, 'code': 'SUCCESS', 'message': '',
           'data': {'items': [], 'page': 1, 'size': 1, 'totalElements': 0}}, 'success'),
])
def test_bounded_business_outcomes(status, body, result):
    response = SimpleNamespace(status_code=status, json=lambda: body)
    assert envelope_result(response, events_page, {(403, 'POLICY')}) == result


def test_protected_configuration_and_auth_files(tmp_path, auth, monkeypatch):
    # Fixtures are user-owned; emulate root ownership only. Real modes,
    # symlink refusal, regular-file checks and size limits remain exercised.
    original = os.fstat
    def root_stat(fd):
        info = original(fd)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_size=info.st_size)
    monkeypatch.setattr(config.os, 'fstat', root_stat)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(CONFIG))
    manifest.chmod(0o644)
    assert config.load_manifest(manifest) == CONFIG
    manifest.chmod(0o666)
    with pytest.raises(ValueError):
        config.load_manifest(manifest)
    manifest.chmod(0o644)
    manifest.write_text(json.dumps(CONFIG | {'target': 'http://public.example'}))
    with pytest.raises(ValueError):
        config.load_manifest(manifest)
    for name, value in zip(('admin-user', 'admin-password-hash', 'session-secret'), auth):
        path = tmp_path / name
        path.write_text(value)
        path.chmod(0o600)
    assert config.load_auth(tmp_path) == list(auth)
    actor = tmp_path / 'actor.json'
    valid_actor = {'user_id': '00000000-0000-0000-0000-000000000001', 'role': 'USER', 'membership': 'vip'}
    actor.write_text(json.dumps(valid_actor))
    actor.chmod(0o600)
    assert config.load_actor(actor) == valid_actor
    actor.write_text(json.dumps({key: value for key, value in valid_actor.items() if key != 'membership'}))
    with pytest.raises(ValueError):
        config.load_actor(actor)
    (tmp_path / 'admin-password-hash').chmod(0o640)
    with pytest.raises(ValueError):
        config.load_auth(tmp_path)
    link = tmp_path / 'link'
    link.symlink_to(manifest)
    with pytest.raises(OSError):
        config.protected_read(link)
    monkeypatch.setattr(config.os, 'fstat', lambda fd: SimpleNamespace(st_mode=0o100600, st_uid=1001, st_size=10))
    with pytest.raises(ValueError):
        config.protected_read(manifest)


def test_cli_credentials_and_https_only(tmp_path):
    path = tmp_path / 'password'
    path.write_text(PASSWORD)
    path.chmod(0o600)
    assert credential(path) == PASSWORD
    path.chmod(0o644)
    with pytest.raises(ValueError):
        credential(path)
    for origin in ('http://fixture.cloudfront.net', 'https://fixture.cloudfront.net.evil', 'https://other.example'):
        with pytest.raises(ValueError):
            Control(origin, 'fixture', PASSWORD)


def test_authenticated_automation_same_master_and_reports(runtime, monkeypatch, tmp_path):
    environment, client, _ = runtime
    calls = []
    monkeypatch.setattr(environment.runner, 'start', lambda users, rate: calls.append((users, rate)))
    monkeypatch.setattr(environment.runner, 'stop', lambda: None)

    def app_request(session, method, url, **kwargs):
        response = client.open(urlsplit(url).path, method=method, base_url=ORIGIN,
                               headers=dict(session.headers), data=kwargs.get('data'))
        gevent.sleep(0)
        return SimpleNamespace(status_code=response.status_code, text=response.text,
                               content=response.data, json=lambda: response.json)

    monkeypatch.setattr(requests.Session, 'request', app_request)
    control = Control(ORIGIN, 'fixture-admin', PASSWORD)
    assert control.run(2, 1, 30) == {'state': 'start_requested'}
    assert calls == [(2, 1.0)]
    assert control.stop() == {'state': 'stopped'}
    output = tmp_path / 'download'
    assert control.collect(output) == {'state': 'collected', 'files': 6}
    assert set(path.name for path in output.iterdir()) == set(REPORT_FILES) | {'SHA256SUMS'}
    checksums = (output / 'SHA256SUMS').read_text().splitlines()
    for name in REPORT_FILES:
        import hashlib
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() + '  ' + name in checksums
        assert (output / name).stat().st_mode & 0o777 == 0o600
    assert environment.host == TARGET


def test_collection_waits_for_reports_after_completed_stop(runtime):
    environment, client, _ = runtime
    assert login(client).status_code == 302
    auth_headers = headers(client)
    environment.stage_control['stop_checkpoint'] = time.monotonic()
    assert client.post('/locust/control/collect', base_url=ORIGIN, headers=auth_headers).status_code == 409
    for name in ('a', 'b'):
        environment.stage_metrics.receive(name, {'stage_business': {}})
    environment.stage_control['stopping'] = True
    assert client.post('/locust/control/collect', base_url=ORIGIN, headers=auth_headers).status_code == 409
    environment.stage_control['stopping'] = False
    assert client.post('/locust/control/collect', base_url=ORIGIN, headers=auth_headers).status_code == 200


def test_native_browser_login_and_form_ajax(runtime, monkeypatch, tmp_path):
    from gevent.pywsgi import WSGIServer
    from playwright.sync_api import sync_playwright
    environment, _, _ = runtime
    calls = []
    monkeypatch.setattr(environment.runner, 'start', lambda users, rate: calls.append((users, rate)))
    key, cert = tmp_path / 'fixture.key', tmp_path / 'fixture.crt'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    server = WSGIServer(('127.0.0.1', 0), environment.web_ui.app, keyfile=str(key), certfile=str(cert), log=None)
    server.start()
    browser_origin = f'https://127.0.0.1:{server.server_port}'
    monkeypatch.setitem(CONFIG, 'public_origin', browser_origin)
    try:
        with sync_playwright() as playwright:
            options = {'headless': True, 'args': ['--no-sandbox', '--no-proxy-server']}
            if os.environ.get('LOCUST_TEST_CHROMIUM'):
                options['executable_path'] = os.environ['LOCUST_TEST_CHROMIUM']
            browser = playwright.chromium.launch(**options)
            page = browser.new_page(ignore_https_errors=True)
            page.goto(browser_origin + '/locust/')
            page.locator('input[name="username"]').fill('fixture-admin')
            page.locator('input[name="password"]').fill(PASSWORD)
            with page.expect_response('**/locust/login_submit') as login_response:
                page.get_by_role('button', name='Login', exact=True).click()
            page.wait_for_url(browser_origin + '/locust/')
            page.locator('input[name="userCount"]').fill('2')
            page.locator('input[name="spawnRate"]').fill('1')
            assert page.locator('input[name="host"]').evaluate('(e)=>e.readOnly')
            page.get_by_text('Advanced options', exact=True).click()
            page.locator('input[name="runTime"]').fill('30')
            with page.expect_response('**/locust/swarm') as response:
                page.locator('input[name="userCount"]').evaluate('(e)=>e.form.requestSubmit()')
            assert response.value.status == 200
            assert calls == [(2, 1.0)]
            # Read headers outside event callbacks: nested synchronous calls
            # in Playwright request callbacks can race its dispatcher.
            posts = [item.request.all_headers() for item in (login_response.value, response.value)]
            for sent_headers in posts:
                assert sent_headers['origin'] == browser_origin
            assert posts[-1]['x-csrf-token']
            cookies = page.context.cookies()
            cookie = next(item for item in cookies if item['name'] == 'locust_session')
            assert cookie['secure'] and cookie['httpOnly'] and cookie['sameSite'] == 'Strict'
            browser.close()
    finally:
        server.stop()


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('local Locust condition did not complete')
        gevent.sleep(0.05)


def test_real_master_two_workers_requests_metrics_and_collection(tmp_path, auth, monkeypatch):
    from gevent.pywsgi import WSGIServer
    monkeypatch.setattr(sys, 'argv', ['locust'])
    received = []

    def api(environ, start_response):
        assert environ['PATH_INFO'] == '/api/v1/events'
        assert environ['QUERY_STRING'] == 'page=1&size=1'
        assert environ['HTTP_X_USER_ROLE'] == 'USER'
        assert environ['HTTP_X_USER_MEMBERSHIP'] == 'vip'
        received.append(environ['HTTP_X_USER_ID'])
        status = '429 Too Many Requests' if len(received) % 3 == 0 else '200 OK'
        body = {'success': True, 'code': 'SUCCESS', 'message': 'fixture',
                'data': {'items': [], 'page': 1, 'size': 1, 'totalElements': 0}}
        gevent.sleep(0.005)
        start_response(status, [('Content-Type', 'application/json')])
        return [json.dumps(body).encode()]

    server = WSGIServer(('127.0.0.1', 0), api, log=None)
    server.start()
    local_target = f'http://127.0.0.1:{server.server_port}'

    class FixtureUser(HttpUser):
        host = local_target
        wait_time = constant(0.03)

        def __init__(self, environment):
            super().__init__(environment)
            self.client = LockedSession(local_target, environment.events.request, self)
            self.client.trust_env = False
            self.actor_headers = {'X-User-ID': '00000000-0000-0000-0000-000000000001',
                                  'X-User-Role': 'USER', 'X-User-Membership': 'vip'}

        @task
        def read(self):
            smoke_events(self)

    master = Environment(user_classes=[FixtureUser], events=Events(), host=local_target,
                         locustfile='local fixture', stop_timeout=0.1)
    master_runner = master.create_master_runner('127.0.0.1', 0)
    master.stage_metrics = Metrics(master, 2)
    master.events.worker_report.add_listener(master.stage_metrics.receive)
    writer = StatsCSVFileWriter(master, [0.5, 0.95, 0.99], str(tmp_path / 'run'), full_history=True)
    writer_greenlet = gevent.spawn(writer)
    master.create_web_ui('127.0.0.1', 0, web_base_path='/locust', web_login=True,
                         delayed_start=True, stats_csv_writer=writer)
    install_web(master, CONFIG | {'target': local_target}, auth, tmp_path)
    workers = []
    try:
        for _ in range(2):
            worker = Environment(user_classes=[FixtureUser], events=Events(), host=local_target, stop_timeout=0.1)
            worker.stage_metrics = Metrics(worker, 2)
            worker.events.report_to_master.add_listener(worker.stage_metrics.report)
            worker.create_worker_runner('127.0.0.1', master_runner.server.port)
            workers.append(worker)
        wait_for(master.stage_metrics.healthy)
        gevent.sleep(0.1)
        assert not received  # Bringing up the platform never starts load.
        client = master.web_ui.app.test_client()
        assert login(client).status_code == 302
        auth_headers = headers(client)
        response = client.post('/locust/swarm', base_url=ORIGIN, headers=auth_headers,
                               data={'host': local_target, 'user_count': 2, 'spawn_rate': 10, 'run_time': '2'})
        assert response.status_code == 200
        wait_for(lambda: bool(received))
        wait_for(lambda: master.runner.state == 'stopped')
        wait_for(lambda: client.get('/locust/control/session', base_url=ORIGIN).json['collectible'])
        count = master.stats.total.num_requests
        assert count > 0 and count == len(received), (count, len(received), dict(master.stage_metrics.business))
        assert master.stats.total.num_failures == count // 3
        assert master.stage_metrics.business['success'] == count - count // 3
        assert master.stage_metrics.business['rate_limited'] == count // 3
        assert sum(master.stage_metrics.business.values()) == count
        assert master.stage_metrics.worker_counts() == (2, 2)
        assert f'locust_requests_total {count}' in master.stage_metrics.render()
        assert client.post('/locust/control/collect', base_url=ORIGIN, headers=auth_headers).status_code == 200
        rows = list(__import__('csv').DictReader((tmp_path / 'collected/run_stats.csv').open()))
        assert int(rows[-1]['Request Count']) == count
        assert int(rows[-1]['Failure Count']) == count // 3
        history = list(__import__('csv').DictReader((tmp_path / 'collected/run_stats_history.csv').open()))
        assert history and {'Aggregated', 'smoke_events'} <= {row['Name'] for row in history}
        assert (tmp_path / 'collected/run.html').read_text().startswith('<!DOCTYPE html>')
        # Worker loss must not appear as a healthy zero.
        workers[0].runner.quit()
        master.runner.clients[workers[0].runner.client_id].state = 'missing'
        assert not master.stage_metrics.healthy()
        assert 'locust_rps NaN' in master.stage_metrics.render()
    finally:
        master.web_ui._cancel_run_time_timer()
        master_runner.quit()
        for worker in workers:
            worker.runner.quit()
            worker.runner.client.close(linger=0)
        master_runner.server.close(linger=0)
        writer_greenlet.kill()
        writer.close_files()
        server.stop()
        assert not server.started
        assert not master_runner.greenlet
        assert all(not worker.runner.greenlet and not worker.runner.user_greenlets for worker in workers)
