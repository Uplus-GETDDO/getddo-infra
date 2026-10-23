"""Pinned Stage entry point. Loading validates configuration before any users."""
import gevent
from gevent.pywsgi import WSGIServer
from locust import events, task
from locust.runners import MasterRunner

from loadtest.config import load_actor, load_auth, load_manifest
from loadtest.metrics import Metrics
from loadtest.scenario import smoke_events
from loadtest.runtime import StageUser
from loadtest.web import install_web

CONFIG = load_manifest()
load_actor(CONFIG['actor_file'])  # Validate protected actor before accepting users.


if CONFIG['scenario'] == 'personal':
    from loadtest.runtime import load_personal
    globals().update(load_personal(CONFIG['scenario_file']))
else:
    class StageSmoke(StageUser):
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
    metrics = Metrics(environment, CONFIG['expected_workers'], CONFIG['scenario'])
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
