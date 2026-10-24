"""Common user and loader for trusted, locally authored Stage scenarios."""
import importlib.util
import inspect
from pathlib import Path

from locust import HttpUser, between

from loadtest.config import load_actor, load_manifest
from loadtest.scenario import LockedSession, envelope_result


class StageUser(HttpUser):
    abstract = True
    wait_time = between(1, 2)

    def __init__(self, environment):
        config = load_manifest()
        if environment.host not in (None, config['target']):
            raise ValueError('Stage target override forbidden')
        self.host = config['target']
        super().__init__(environment)
        self.client = LockedSession(config['target'], environment.events.request, self)
        self.client.trust_env = False
        actor = load_actor(config['actor_file'])
        self.actor_headers = {'X-User-ID': actor['user_id'], 'X-User-Role': actor['role'],
                              'X-User-Membership': actor['membership']}
        self.client.headers.update(self.actor_headers)

    def check(self, response, validate_data, expected_rejections=frozenset()):
        """Call inside catch_response=True to record bounded business outcomes."""
        result = envelope_result(response, validate_data, expected_rejections)
        if result in ('success', 'expected_rejection'):
            response.success()
        else:
            response.failure(result)
        self.environment.stage_metrics.record(result)
        return result


def load_personal(path):
    """Execute trusted Python; this validation is not a code sandbox."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('one regular Python file of at most 1 MiB required')
    compile(path.read_bytes(), str(path), 'exec')
    spec = importlib.util.spec_from_file_location('getddo_personal_scenario', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    classes = {name: value for name, value in vars(module).items()
               if inspect.isclass(value) and issubclass(value, StageUser)
               and value is not StageUser and not value.abstract and value.__module__ == module.__name__}
    if not classes or any(not value.tasks for value in classes.values()):
        raise ValueError('define a concrete StageUser subclass with at least one task')
    return classes
