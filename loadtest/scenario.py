"""Read-only smoke fixture and bounded callable response contract."""
from urllib.parse import urlsplit
from locust.clients import HttpSession

RESULTS = ('success', 'expected_rejection', 'rate_limited', 'server_error',
           'http_error', 'invalid_envelope', 'transport_error')


class LockedSession(HttpSession):
    """Reject absolute URLs, authority-relative URLs, host headers and redirects."""
    def request(self, method, url, *args, **kwargs):
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc or not url.startswith('/') or '\\' in url:
            raise ValueError('scenario requests must use a relative Stage path')
        if any(key.lower() == 'host' for key in (*self.headers, *kwargs.get('headers', {}))):
            raise ValueError('Host override forbidden')
        kwargs['allow_redirects'] = False
        return super().request(method, url, *args, **kwargs)


def envelope_result(response, validate_data, expected_rejections=frozenset()):
    """Callable scenario contract: data validator and explicit (status, code) pairs.

    Only these bounded result strings may be recorded. Never include response
    body, user identifier, raw URL or exception text in names/errors/labels.
    """
    status = response.status_code
    if status == 0:
        return 'transport_error'
    if status == 429:
        return 'rate_limited'
    if status >= 500:
        return 'server_error'
    try:
        body = response.json()
    except (ValueError, TypeError):
        return 'invalid_envelope'
    if (not isinstance(body, dict) or type(body.get('success')) is not bool
            or not isinstance(body.get('code'), str) or not isinstance(body.get('message'), str)
            or 'data' not in body):
        return 'invalid_envelope'
    if (status, body['code']) in expected_rejections and body['success'] is False:
        return 'expected_rejection'
    if status != 200:
        return 'http_error'
    if body['success'] is not True or body['code'] != 'SUCCESS' or not validate_data(body['data']):
        return 'invalid_envelope'
    return 'success'


def events_page(data):
    return (isinstance(data, dict) and isinstance(data.get('items'), list)
            and type(data.get('page')) is int and data['page'] >= 1
            and type(data.get('size')) is int and data['size'] >= 1
            and type(data.get('totalElements')) is int and data['totalElements'] >= 0
            and len(data['items']) <= data['size'])


def smoke_events(user):
    with user.client.get('/api/v1/events?page=1&size=1', name='smoke_events',
                         headers=user.actor_headers, timeout=10, catch_response=True) as response:
        result = envelope_result(response, events_page)
        if result in ('success', 'expected_rejection'):
            response.success()
        else:
            response.failure(result)
        user.environment.stage_metrics.record(result)
