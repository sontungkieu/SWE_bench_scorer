import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


class ThinObserverTests(unittest.TestCase):
    def exercise(self, raises=False):
        fake_pytest = ModuleType('pytest')
        fake_pytest.hookimpl = lambda **kw: lambda fn: fn
        legend_module = ModuleType('matplotlib.legend')

        class Legend:
            _loc_used_default = True

            def _find_best_position(self, token):
                if raises: raise RuntimeError('original exception')
                return token

        legend_module.Legend = Legend
        original = Legend._find_best_position
        clock = time.perf_counter
        path = Path(__file__).with_name('stability_observer.py')
        spec = importlib.util.spec_from_file_location('thin_observer_test', path)
        module = importlib.util.module_from_spec(spec)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {
                'pytest': fake_pytest, 'matplotlib.legend': legend_module}), patch.dict(
                    'os.environ', {'STABILITY_OBSERVER_OUTPUT': str(Path(tmp)/'observed.json')}):
            spec.loader.exec_module(module)
            item = SimpleNamespace(name='test_warn_big_data_best_loc', nodeid='target', fspath=__file__)
            hook = module.pytest_runtest_call(item); next(hook)
            token = object()
            if raises:
                with self.assertRaisesRegex(RuntimeError, 'original exception'):
                    Legend()._find_best_position(token)
                outcome = SimpleNamespace(excinfo=(RuntimeError, RuntimeError('original exception'), None))
            else:
                self.assertIs(Legend()._find_best_position(token), token)
                outcome = SimpleNamespace(excinfo=None)
            with self.assertRaises(StopIteration): hook.send(outcome)
            observed = json.loads((Path(tmp)/'observed.json').read_text())
            self.assertIs(Legend._find_best_position, original)
            self.assertIs(time.perf_counter, clock)
            self.assertEqual(len(observed['calls']), 1)
            self.assertEqual(observed['file_hashes_before'], observed['file_hashes_after'])

    def test_return_identity_and_original_function_are_preserved(self): self.exercise()

    def test_exception_is_not_suppressed_and_function_is_restored(self): self.exercise(True)


if __name__ == '__main__': unittest.main()
