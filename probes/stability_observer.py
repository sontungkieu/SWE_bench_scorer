"""Diagnostic-only pytest plugin. Never used to produce an official score.

Clock/test files are untouched. Thin wrappers are restored after the target
test. Unlike sys.setprofile, the legend wrapper adds no per-loop callbacks.
"""
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import time

import pytest


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cookie_rows(jar):
    return [dict(name=c.name, value=c.value, domain=c.domain, path=c.path,
                 expires=c.expires, discard=c.discard) for c in jar]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    target = item.name
    if target not in ('test_warn_big_data_best_loc', 'test_cookie_removed_on_expire'):
        yield
        return
    result = {'test': item.nodeid, 'epoch': time.time(), 'calls': [],
              'diagnostic_only': True, 'clock_replaced': False}
    paths = {'test': str(item.fspath)}
    if target == 'test_warn_big_data_best_loc':
        from matplotlib.legend import Legend
        owner, attr = Legend, '_find_best_position'
        original = getattr(owner, attr)
        paths['implementation'] = inspect.getsourcefile(original)

        @functools.wraps(original)
        def wrapped(self, *args, **kwargs):
            default = self._loc_used_default
            started = time.perf_counter()
            try:
                return original(self, *args, **kwargs)
            finally:
                elapsed = time.perf_counter() - started
                result['calls'].append({'outer_elapsed_seconds': elapsed,
                                        'loc_used_default': bool(default)})
    else:
        import requests
        owner, attr = requests.sessions.Session, 'send'
        original = getattr(owner, attr)
        paths['implementation'] = inspect.getsourcefile(original)
        result['httpbin_test_setting'] = getattr(item.module, 'HTTPBIN', None)
        result['send_source'] = inspect.getsource(original)
        result['test_source'] = inspect.getsource(getattr(item.module.RequestsTestCase, target))

        @functools.wraps(original)
        def wrapped(self, request, **kwargs):
            row = {'url': request.url, 'epoch_before': time.time(),
                   'cookies_before': cookie_rows(self.cookies)}
            try:
                response = original(self, request, **kwargs)
                row.update(status=response.status_code, response_url=response.url,
                           headers={k: v for k, v in response.headers.items()
                                    if k.lower() in {'date', 'set-cookie', 'location', 'server',
                                                     'age', 'cache-control', 'via', 'x-cache'}},
                           cookies_after=cookie_rows(self.cookies),
                           response_cookie_jar=cookie_rows(response.cookies),
                           history=[{'url': r.url, 'status': r.status_code,
                                     'set_cookie': r.headers.get('Set-Cookie')}
                                    for r in response.history],
                           body_prefix=response.text[:1024])
                return response
            except Exception as exc:
                row['exception'] = type(exc).__name__ + ': ' + str(exc)
                raise
            finally:
                row['epoch_after'] = time.time()
                result['calls'].append(row)
    result['file_hashes_before'] = {k: digest(v) for k, v in paths.items()}
    setattr(owner, attr, wrapped)
    try:
        outcome = yield
        result['test_exception'] = str(outcome.excinfo[1]) if outcome.excinfo else None
    finally:
        setattr(owner, attr, original)
        result['file_hashes_after'] = {k: digest(v) for k, v in paths.items()}
        Path(os.environ['STABILITY_OBSERVER_OUTPUT']).write_text(json.dumps(result, indent=2) + '\n')
