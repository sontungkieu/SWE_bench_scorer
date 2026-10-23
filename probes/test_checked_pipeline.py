import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checked_patch import apply, sections
from checked_pipeline import validity_from_status, prepare_overlay


class ForwardApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init')
        self.git('config', 'core.autocrlf', 'false')
        (self.repo / 'code.py').write_bytes(b'value = "old"\n')
        (self.repo / 'config').write_bytes(b'requires = "setuptools"\nextra = "base"\n')
        self.git('add', 'code.py', 'config')
        self.commit('base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        (self.repo / 'code.py').write_bytes(b'value = "fixed"\n')
        (self.repo / 'config').write_bytes(b'requires = "setuptools==68"\nextra = "base"\n')
        self.patch = self.root / 'prediction.diff'
        self.patch.write_bytes(subprocess.check_output(['git', 'diff'], cwd=self.repo))
        self.git('checkout', '--', '.')

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, text=True,
                                       stderr=subprocess.DEVNULL)

    def commit(self, message):
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-m', message)

    def image(self, config):
        (self.repo / 'config').write_bytes(config.encode())
        self.git('add', 'config')
        self.commit('image setup')

    def test_redundant_setup_hunk_never_reverses_pin_or_code(self):
        self.image('requires = "setuptools==68"\nextra = "base"\n')
        result = apply(self.repo, self.patch, self.base)
        self.assertTrue(result['applied'], result)
        self.assertTrue(result['prediction_fully_materialized'])
        self.assertIn('fixed', (self.repo / 'code.py').read_text())
        self.assertIn('setuptools==68', (self.repo / 'config').read_text())
        self.assertEqual(len(result['steps']), 1)

    def test_independent_image_setup_edits_survive_three_way_merge(self):
        # Distant lines allow a real three-way merge with no side preference.
        base = 'pin = "base"\n' + '\n'.join('line' + str(i) for i in range(20)) + '\nother = "base"\n'
        (self.repo / 'config').write_bytes(base.encode())
        self.git('add', 'config'); self.commit('larger base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        (self.repo / 'config').write_bytes(base.replace('pin = "base"', 'pin = "model"').encode())
        self.patch.write_bytes(subprocess.check_output(['git', 'diff'], cwd=self.repo))
        self.git('checkout', '--', '.')
        self.image(base.replace('other = "base"', 'other = "image"'))
        result = apply(self.repo, self.patch, self.base)
        self.assertTrue(result['applied'], result)
        self.assertIn('pin = "model"', (self.repo / 'config').read_text())
        self.assertIn('other = "image"', (self.repo / 'config').read_text())

    def test_conflicting_setup_fails_without_selecting_model_or_image(self):
        self.image('requires = "setuptools==70"\nextra = "base"\n')
        result = apply(self.repo, self.patch, self.base)
        self.assertFalse(result['applied'])
        self.assertIn('conflicts', result['error'])
        self.assertIn('setuptools==70', (self.repo / 'config').read_text())
        self.assertIn('old', (self.repo / 'code.py').read_text())

    def test_sphinx_reporting_flag_already_in_image_is_not_unapplied(self):
        (self.repo / 'config').write_bytes(b'python -m pytest\n')
        self.git('add', 'config'); self.commit('tox base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        (self.repo / 'config').write_bytes(b'python -m pytest -rA\n')
        self.patch.write_bytes(subprocess.check_output(['git', 'diff'], cwd=self.repo))
        self.git('checkout', '--', '.')
        self.image('python -m pytest -rA\n')
        result = apply(self.repo, self.patch, self.base)
        self.assertTrue(result['applied'], result)
        self.assertEqual((self.repo / 'config').read_text(), 'python -m pytest -rA\n')

    def test_unknown_base_and_unsafe_paths_fail_closed(self):
        self.assertFalse(apply(self.repo, self.patch, '0' * 40)['applied'])
        for name in ['../escape', '.git/config', '/absolute']:
            with self.assertRaises(ValueError):
                sections(('diff --git a/' + name + ' b/' + name + '\n').encode())

    @unittest.skipUnless(shutil.which('patch'), 'GNU patch required; exercised on remote coordinator')
    def test_binary_without_payload_is_an_explicit_materialization_error(self):
        self.patch.write_bytes(self.patch.read_bytes() + b'diff --git a/output.png b/output.png\nnew file mode 100644\nindex 0000000..123abcd\nBinary files /dev/null and b/output.png differ\n')
        result = apply(self.repo, self.patch, self.base)
        self.assertTrue(result['applied'], result)
        self.assertIn('fixed', (self.repo / 'code.py').read_text())
        self.assertFalse(result['prediction_fully_materialized'])
        self.assertEqual(len(result['materialization_errors']), 1)
        self.assertNotIn('--reject', str(result['steps']))
        self.assertIn('--forward', str(result['steps']))


class CaptureValidityTests(unittest.TestCase):
    def test_eight_concurrent_applicator_copies_have_no_shared_tar_file(self):
        from concurrent.futures import ThreadPoolExecutor
        import io
        import tarfile
        import threading
        from checked_pipeline import copy_applicator
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'checked_patch.py'
            payload = b'original applicator bytes\n'; source.write_bytes(payload)
            barrier = threading.Barrier(8)
            class Container:
                def put_archive(self, path, data):
                    barrier.wait(timeout=10)
                    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                        assert archive.getnames() == ['checked_patch.py']
                        assert archive.extractfile('checked_patch.py').read() == payload
                    return path == '/tmp'
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _:copy_applicator(Container(),source), range(8)))
            self.assertEqual(list(root.iterdir()), [source])

    def test_aggregate_pass_without_required_ids_is_not_a_model_failure_or_success(self):
        result = validity_from_status(['a', 'b'], {}, {'resolved': False}, {'applied': True})
        self.assertFalse(result['execution_valid'])
        self.assertIsNone(result['qualified_resolved'])
        self.assertEqual(result['missing_required_ids'], ['a', 'b'])
        self.assertFalse(result['official_report_modified'])

    def test_real_test_failure_is_a_valid_unresolved_outcome(self):
        result = validity_from_status(['a', 'b'], {'a': 'PASSED', 'b': 'FAILED'},
                                      {'resolved': False}, {'applied': True})
        self.assertTrue(result['execution_valid'])
        self.assertIs(result['qualified_resolved'], False)

    def test_setup_error_and_binary_input_cannot_be_hidden_by_passed_tests(self):
        result = validity_from_status(['a'], {'a': 'PASSED'}, {'resolved': True},
            {'applied': True, 'materialization_errors': [{'paths': ['output.png']}]}, True)
        self.assertFalse(result['execution_valid'])
        self.assertIsNone(result['qualified_resolved'])
        self.assertIn('prediction_not_fully_materialized', result['issues'])
        self.assertIn('pre_test_setup_error', result['issues'])

    def test_overlay_refuses_unrecognized_harness_without_touching_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); package = root / 'swebench'
            (package / 'harness').mkdir(parents=True)
            source = package / 'harness/run_evaluation.py'; source.write_text('unknown')
            with self.assertRaises(ValueError):
                prepare_overlay(package, root / 'overlay')
            self.assertEqual(source.read_text(), 'unknown')
            self.assertFalse((root / 'overlay').exists())

    def test_overlay_changes_only_apply_block_and_rejects_reuse(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); package = root / 'swebench'
            source = package / 'harness/run_evaluation.py'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'prefix\n        # Attempt to apply patch to container (TODO: FIX THIS)\nlegacy\n        # Get git diff before running eval script\nsuffix\n')
            from checked_pipeline import PROTECTED
            for name in PROTECTED:
                path = package / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'protected official source\n')
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = prepare_overlay(package, root / 'overlay', expected)
            self.assertEqual(manifest['changed_files'], ['harness/run_evaluation.py'])
            modified = (root / 'overlay/swebench/harness/run_evaluation.py').read_text()
            self.assertIn('apply_in_container', modified)
            self.assertTrue(modified.startswith('prefix\n'))
            self.assertTrue(modified.endswith('        # Get git diff before running eval script\nsuffix\n'))
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), expected)
            with self.assertRaises(ValueError):
                prepare_overlay(package, root / 'overlay', expected)

    def test_checked_runner_rejects_budget_excess_and_assignment_drift(self):
        from decimal import Decimal
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from n11_checked_predictions import validate_budget, lifetime_for, validate_selection
        validate_budget(Decimal('2.32348352'), Decimal('3.67651648'), Decimal('6'))
        with self.assertRaises(ValueError):
            validate_budget(Decimal('2.32348353'), Decimal('3.67651648'), Decimal('6'))
        self.assertIsNone(lifetime_for(1, 2000, Decimal('2.32348352')))
        plan = dict(selected_instance_ids=['a'], selected_count=1, full_verified_accounting=False,
                    groups=[{'instance_ids':['b']}], active_count=1, empty_patch_count=0)
        with self.assertRaises(ValueError):
            validate_selection(plan, [{'instance_id':'a', 'model_patch':'patch'}])
