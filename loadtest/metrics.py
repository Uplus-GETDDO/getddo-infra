"""Master global native Locust statistics plus bounded cumulative worker outcomes."""
from collections import Counter
import time
from locust.stats import CURRENT_RESPONSE_TIME_PERCENTILE_WINDOW
from .scenario import RESULTS

STALE_SECONDS = 10


class Metrics:
    def __init__(self, environment, expected):
        self.environment = environment
        self.expected = expected
        self.local = Counter()
        self.business = Counter()
        self.previous = {}
        self.seen = {}

    def record(self, result):
        if result not in RESULTS:
            raise ValueError('unbounded business result')
        self.local[result] += 1

    def report(self, client_id, data, **kwargs):
        data['stage_business'] = dict(self.local)

    def receive(self, client_id, data, **kwargs):
        self.seen[client_id] = time.monotonic()
        current = data.get('stage_business', {})
        if not isinstance(current, dict):
            return
        previous = self.previous.get(client_id, {})
        accepted = dict(previous)
        for result in RESULTS:
            value = current.get(result, 0)
            if type(value) is not int or value < 0:
                continue
            # Worker IDs include boot UUID. Native stats reports are deltas;
            # our payload is cumulative and duplicate reports must not recount.
            self.business[result] += max(0, value - previous.get(result, 0))
            accepted[result] = max(value, previous.get(result, 0))
        self.previous[client_id] = accepted

    def worker_counts(self):
        clients = getattr(self.environment.runner, 'clients', {})
        connected_ids = {key for key, node in clients.items() if node.state != 'missing'}
        fresh = sum(time.monotonic() - self.seen.get(key, float('-inf')) <= STALE_SECONDS
                    for key in connected_ids)
        return len(connected_ids), fresh

    def healthy(self):
        connected, fresh = self.worker_counts()
        return connected >= self.expected and fresh >= self.expected

    def rolling_has_samples(self, total):
        # Match native cache selection, but distinguish an empty histogram
        # delta from a measured zero-millisecond response.
        timestamp = int(time.time()) - CURRENT_RESPONSE_TIME_PERCENTILE_WINDOW
        candidates = [timestamp]
        for offset in range(1, 9):
            candidates.extend((timestamp - offset, timestamp + offset))
        for timestamp in candidates:
            cached = (total.response_times_cache or {}).get(timestamp)
            if cached is not None:
                return any(count > cached.response_times.get(duration, 0)
                           for duration, count in total.response_times.items())
        return False

    def render(self):
        total = self.environment.stats.total
        connected, fresh = self.worker_counts()
        has_data = bool(total.num_requests and total.last_request_timestamp
                        and time.time() - total.last_request_timestamp <= STALE_SECONDS and self.healthy())
        lines = []

        def metric(name, kind, value, labels=''):
            if not any(line == f'# TYPE {name} {kind}' for line in lines):
                lines.append(f'# TYPE {name} {kind}')
            lines.append(f'{name}{labels} {value}')

        metric('locust_users', 'gauge', self.environment.runner.user_count)
        metric('locust_requests_total', 'counter', total.num_requests)
        metric('locust_failures_total', 'counter', total.num_failures)
        metric('locust_rps', 'gauge', total.current_rps if has_data else 'NaN')
        metric('locust_data_fresh', 'gauge', int(has_data))
        metric('locust_workers_expected', 'gauge', self.expected)
        metric('locust_workers_connected', 'gauge', connected)
        metric('locust_workers_fresh', 'gauge', fresh)
        metric('locust_workers_healthy', 'gauge', int(self.healthy()))
        for quantile in (0.5, 0.95, 0.99):
            for scope in ('cumulative', 'rolling'):
                value = None
                if has_data and total.response_times and (scope == 'cumulative' or self.rolling_has_samples(total)):
                    getter = (total.get_response_time_percentile if scope == 'cumulative'
                              else total.get_current_response_time_percentile)
                    value = getter(quantile)
                metric('locust_response_time_seconds', 'gauge', 'NaN' if value is None else value / 1000,
                       f'{{quantile="{quantile}",scope="{scope}"}}')
        for result in RESULTS:
            metric('locust_business_total', 'counter', self.business[result],
                   f'{{scenario="smoke_events",method="GET",result="{result}"}}')
        return '\n'.join(lines) + '\n'

    def application(self, environ, start_response):
        if environ['PATH_INFO'] != '/metrics' or environ['REQUEST_METHOD'] != 'GET':
            start_response('404 Not Found', [])
            return [b'']
        start_response('200 OK', [('Content-Type', 'text/plain; version=0.0.4')])
        return [self.render().encode()]
