"""Authentication, same-master control and safe report snapshots."""
import csv
from datetime import datetime, timezone
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import time

import gevent
from flask import abort, has_request_context, jsonify, redirect, render_template_string, request, send_file, session
from flask_login import UserMixin, current_user, login_user, logout_user
from locust import __version__
from locust.html import get_html_report
from locust.util.timespan import parse_timespan
from werkzeug.security import check_password_hash

REPORT_FILES = ('run_stats.csv', 'run_stats_history.csv', 'run_failures.csv',
                'run_exceptions.csv', 'run.html', 'metadata.json')


class Admin(UserMixin):
    def __init__(self, name):
        self.id = name


def install_web(environment, config, auth, reports=Path('/reports')):
    web = environment.web_ui
    app = web.app
    username, password_hash, session_secret = auth
    app.debug = False
    app.config.update(SECRET_KEY=session_secret, SESSION_COOKIE_NAME='locust_session',
                      SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_PATH='/locust',
                      MAX_CONTENT_LENGTH=8192, PERMANENT_SESSION_LIFETIME=1800)
    web.login_manager.user_loader(lambda name: Admin(name) if hmac.compare_digest(name.encode(), username.encode()) else None)
    state = {'failures': 0, 'locked_until': 0., 'pending': False,
             'uncollected': False, 'started': None, 'stopped': None, 'parameters': None,
             'stop_checkpoint': None, 'stopping': False, 'maintenance': False}
    environment.stage_control = state
    web.auth_args = {'username_password_callback': '/locust/login_submit'}

    def csrf():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        return session['csrf']

    def collectible():
        metrics = environment.stage_metrics
        return (not state['pending'] and not state['stopping'] and environment.runner.state in ('ready', 'stopped')
                and metrics.healthy() and (state['stop_checkpoint'] is None
                    or all(metrics.seen.get(key, 0) > state['stop_checkpoint']
                           for key, node in environment.runner.clients.items() if node.state != 'missing')))

    @app.before_request
    def guard():
        if request.path == '/locust':
            return redirect('/locust/')
        if request.path in ('/metrics', '/locust/metrics'):
            abort(404)
        if request.path == '/locust/health':
            return None
        public = request.path in ('/locust/login', '/locust/login_submit') or request.path.startswith('/locust/assets/')
        if not public and not current_user.is_authenticated:
            if request.path == '/locust/':
                return redirect('/locust/login')
            abort(401)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('Origin') != config['public_origin']:
                abort(403)
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf', '')
            if not token or not hmac.compare_digest(token.encode(), session.get('csrf', '').encode()):
                abort(403)
        if request.path in ('/locust/user', '/locust/stats/reset'):
            abort(403)  # Runtime scenario and evidence cannot be edited/reset in the UI.

    @app.after_request
    def security_headers(response):
        for key in list(response.headers.keys()):
            if key.lower().startswith('access-control-'):
                del response.headers[key]
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "frame-ancestors 'none'; base-uri 'self'"
        if request.path == '/locust/' and response.status_code == 200:
            # Native React UI uses fetch/axios. Attach the same CSRF token used
            # by automation; the server remains the authority for host/start.
            token = json.dumps(csrf())
            script = '''<script>(function(){const token=TOKEN;
const oldFetch=window.fetch;window.fetch=function(input,opts={}){
const url=new URL(typeof input==='string'?input:input.url,location.href);
if(url.origin===location.origin){opts.headers=new Headers(opts.headers||(input.headers||{}));opts.headers.set('X-CSRF-Token',token);}
return oldFetch.call(this,input,opts);};
const open=XMLHttpRequest.prototype.open,send=XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open=function(method,url){this.stageSameOrigin=new URL(url,location.href).origin===location.origin;return open.apply(this,arguments);};
XMLHttpRequest.prototype.send=function(){if(this.stageSameOrigin)this.setRequestHeader('X-CSRF-Token',token);return send.apply(this,arguments);};
new MutationObserver(function(){document.querySelectorAll('input[name="host"]').forEach(function(e){e.readOnly=true;});}).observe(document.documentElement,{childList:true,subtree:true});
})();</script>'''.replace('TOKEN', token)
            response.set_data(response.get_data(as_text=True).replace('<head>', '<head>' + script, 1))
        return response

    def login_page():
        return render_template_string('''<!doctype html><html><head><title>Stage Locust login</title></head>
<body><h1>Stage Locust</h1><form method="post" action="/locust/login_submit">
<input type="hidden" name="csrf" value="{{ token }}">
<label>Username <input name="username" autocomplete="username" required></label>
<label>Password <input type="password" name="password" autocomplete="current-password" required></label>
<button type="submit">Login</button></form></body></html>''', token=csrf())

    app.view_functions['locust.login'] = login_page

    @app.post('/locust/login_submit')
    def login_submit():
        now = time.monotonic()
        if now < state['locked_until']:
            abort(429)
        # Always perform the password hash check, including unknown usernames.
        valid_password = check_password_hash(password_hash, request.form.get('password', ''))
        valid_user = hmac.compare_digest(request.form.get('username', '').encode(), username.encode())
        if not (valid_password and valid_user):
            state['failures'] += 1
            if state['failures'] >= 5:
                state['locked_until'] = now + 60
                state['failures'] = 0
            return 'Invalid credentials', 401
        state['failures'] = 0
        session.clear()
        login_user(Admin(username))
        session.permanent = True
        csrf()
        return redirect('/locust/')

    @app.get('/locust/control/session')
    def control_session():
        return jsonify(csrf=csrf(), state=environment.runner.state, collectible=collectible())

    @app.post('/locust/logout')
    def logout():
        logout_user()
        session.clear()
        return redirect('/locust/login')

    @app.get('/locust/health')
    def health():
        return jsonify(alive=True, ready=environment.stage_metrics.healthy())

    @app.post('/locust/control/maintenance')
    def maintenance():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or type(payload.get('enabled')) is not bool:
            abort(400)
        if payload['enabled'] and (state['pending'] or state['stopping']
                or environment.runner.state not in ('ready', 'stopped')):
            abort(409, 'stop the current test before applying a scenario')
        state['maintenance'] = payload['enabled']
        return jsonify(maintenance=state['maintenance'])

    def swarm():
        form = request.form
        if request.is_json:
            form = request.get_json()
        if not isinstance(form, dict) and not hasattr(form, 'getlist'):
            abort(400)
        if form.get('host', config['target']) != config['target']:
            abort(400, 'Stage target is immutable')
        # Native 2.47 UI includes an empty profile input even with no profiles.
        if form.get('profile', '') or set(form) - {'host', 'user_count', 'spawn_rate', 'run_time', 'profile'}:
            abort(400, 'unsupported runtime configuration')
        try:
            users = int(form['user_count'])
            rate = float(form['spawn_rate'])
            duration = parse_timespan(str(form['run_time']))
            if users <= 0 or not math.isfinite(rate) or rate <= 0 or duration <= 0:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            abort(400, 'positive users, spawn_rate and explicit run_time required')
        if (state['maintenance'] or state['pending'] or state['uncollected']
                or environment.runner.state not in ('ready', 'stopped')):
            abort(409, 'running or reports require collection')
        if not environment.stage_metrics.healthy():
            abort(409, 'expected workers missing or stale')
        state.update(pending=True, uncollected=True, stopped=None, stop_checkpoint=None,
                     started=datetime.now(timezone.utc).isoformat(),
                     parameters={'users': users, 'spawn_rate': rate, 'duration_seconds': duration})
        environment.host = config['target']

        def start():
            try:
                environment.runner.start(users, rate)
            finally:
                state['pending'] = False

        web._cancel_run_time_timer()
        web._swarm_greenlet = gevent.spawn(start)
        web._run_time_greenlet = gevent.spawn_later(duration, stop_run)
        return jsonify(success=True, host=config['target'], run_time=duration)

    app.view_functions['locust.swarm'] = swarm

    def stop_run():
        if state['stopping']:
            if has_request_context():
                abort(409, 'stop is still in progress')
            return
        state['stopping'] = True
        try:
            if web._swarm_greenlet is not None:
                web._swarm_greenlet.kill(block=True)
                web._swarm_greenlet = None
            if web._run_time_greenlet is gevent.getcurrent():
                web._run_time_greenlet = None
            else:
                web._cancel_run_time_timer()
            environment.runner.stop()
        finally:
            state['stopping'] = False
        return jsonify(success=True, message='Test stopped') if has_request_context() else None

    app.view_functions['locust.stop'] = stop_run

    def stopped(environment, **kwargs):
        state['stopped'] = datetime.now(timezone.utc).isoformat()
        state['stop_checkpoint'] = time.monotonic()

    environment.events.test_stop.add_listener(stopped)

    @app.post('/locust/control/collect')
    def collect():
        if state['pending'] or environment.runner.state not in ('ready', 'stopped'):
            abort(409, 'stop and wait for final worker reports before collection')
        # MasterRunner.stop waits for worker final stats. Refuse collection
        # while expected reporters are missing so gaps cannot look complete.
        if not collectible():
            abort(409, 'worker evidence incomplete')
        reports.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Native background writers retain open handles; snapshot into a
        # separate directory rather than truncating their live files.
        snapshot = reports / 'collected'
        snapshot.mkdir(mode=0o700, exist_ok=True)
        for filename, method in (('run_stats.csv', web.stats_csv_writer.requests_csv),
                                 ('run_failures.csv', web.stats_csv_writer.failures_csv)):
            with (snapshot / filename).open('w', newline='') as stream:
                method(csv.writer(stream))
        # Exception bodies and tracebacks can contain personal values. Preserve
        # counts without arbitrary exception strings in collected artifacts.
        with (snapshot / 'run_exceptions.csv').open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['Count', 'Message'])
            writer.writerow([sum(item['count'] for item in environment.runner.exceptions.values()),
                             'runtime_exception_redacted'])
        exceptions = environment.runner.exceptions
        environment.runner.exceptions = {}
        try:
            (snapshot / 'run.html').write_text(get_html_report(environment, show_download_link=False))
        finally:
            environment.runner.exceptions = exceptions
        metadata = {'locust_version': __version__, 'scenario': config['scenario'],
                    'environment': 'staging', 'started_at': state['started'], 'stopped_at': state['stopped'],
                    'collected_at': datetime.now(timezone.utc).isoformat(), 'parameters': state['parameters'],
                    'expected_workers': config['expected_workers'], 'metrics': environment.stage_metrics.render()}
        (snapshot / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
        writer = web.stats_csv_writer
        if not hasattr(writer, 'stats_history_flush') or not (reports / 'run_stats_history.csv').is_file():
            abort(409, 'full history writer is unavailable')
        writer._stats_history_data_rows(writer.stats_history_csv_writer, time.time())
        writer.stats_history_flush()
        shutil.copyfile(reports / 'run_stats_history.csv', snapshot / 'run_stats_history.csv')
        for name in REPORT_FILES:
            os.chmod(snapshot / name, 0o600)
        state['uncollected'] = False
        return jsonify(files=REPORT_FILES)

    @app.get('/locust/control/reports/<name>')
    def report(name):
        if name not in REPORT_FILES:
            abort(404)
        return send_file(reports / 'collected' / name, as_attachment=True)

    return state
