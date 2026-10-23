"""Pinned Stage entry point. Loading validates configuration before any users."""
import gevent
from gevent.pywsgi import WSGIServer
from locust import HttpUser, between, events, task
from locust.runners import MasterRunner

from loadtest.config import load_actor, load_auth, load_manifest
from loadtest.metrics import Metrics
from loadtest.scenario import LockedSession, smoke_events
from loadtest.web import install_web

CONFIG = load_manifest()
ACTOR = load_actor(CONFIG['actor_file'])


class StageSmoke(HttpUser):
    host = CONFIG['target']
    wait_time = between(1, 2)

    def __init__(self, environment):
        if environment.host not in (None, CONFIG['target']):
            raise ValueError('Stage target override forbidden')
        super().__init__(environment)
        self.client = LockedSession(CONFIG['target'], environment.events.request, self)
        self.client.trust_env = False
        self.actor_headers = {'X-User-ID': ACTOR['user_id'], 'X-User-Role': ACTOR['role'],
                              'X-User-Membership': ACTOR['membership']}

    @task
    def read_events(self):
        smoke_events(self)


@events.init.add_listener
def initialize(environment, **kwargs):
    options = environment.parsed_options
    if (not isinstance(environment.runner, MasterRunner) and environment.web_ui
            or environment.host not in (None, CONFIG['target'])
            or any(getattr(options, key, False) for key in ('headless', 'autostart', 'class_picker'))):
        raise SystemExit('unsupported Stage runtime mode')
    environment.host = CONFIG['target']
    metrics = Metrics(environment, CONFIG['expected_workers'])
    environment.stage_metrics = metrics
    environment.events.report_to_master.add_listener(metrics.report)
    environment.events.worker_report.add_listener(metrics.receive)
    if isinstance(environment.runner, MasterRunner):
        environment.events.test_start.add_listener(lambda **kw: metrics.business.clear())
        if not environment.web_ui or not environment.web_ui.web_login:
            raise SystemExit('authenticated master web UI required')
        install_web(environment, CONFIG, load_auth())
        exporter = WSGIServer(('0.0.0.0', 9646), metrics.application, log=None)
        gevent.spawn(exporter.serve_forever)
        environment.events.quitting.add_listener(lambda **kw: exporter.stop())
