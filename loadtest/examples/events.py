"""Copy into your team directory, edit, then run loadtest apply <file>."""
from locust import between, task
from loadtest.runtime import StageUser
from loadtest.scenario import events_page


class MyEvents(StageUser):
    wait_time = between(1, 2)

    @task
    def read_events(self):
        with self.client.get('/api/v1/events?page=1&size=10', name='events_list',
                             timeout=10, catch_response=True) as response:
            self.check(response, events_page)
