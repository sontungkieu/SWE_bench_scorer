"""Isolated SWE-bench 4.1.0 apply overlay and supplemental outcome validity.

The legacy package, official test scripts, parsers and report JSON stay intact.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tarfile

from checked_patch import VERSION

UPSTREAM_RUN_SHA256 = '6959f0b4e4eaf979771f529b88e3e9df1daa7fe86bc4291feec2e7d320bf7f2e'
PROTECTED = ['harness/grading.py', 'harness/test_spec/test_spec.py',
             'harness/test_spec/create_scripts.py', 'harness/log_parsers/python.py']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare_overlay(package, destination, expected_run_sha256=UPSTREAM_RUN_SHA256):
    package, destination = Path(package), Path(destination)
    if destination.exists():
        raise ValueError('Never overwrite an evaluator overlay')
    original = package / 'harness/run_evaluation.py'
    if sha(original) != expected_run_sha256:
        raise ValueError('Unknown upstream harness; overlay refused')
    text = original.read_text()
    start = text.index('        # Attempt to apply patch to container (TODO: FIX THIS)')
    end = text.index('        # Get git diff before running eval script', start)
    replacement = '''        # Versioned checked applicator; tests and grading are unchanged.
        from checked_pipeline import apply_in_container
        apply_in_container(container, test_spec.instance_id, logger, log_dir, DOCKER_PATCH)

'''
    target = destination / 'swebench'
    shutil.copytree(package, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (target / 'harness/run_evaluation.py').write_text(text[:start] + replacement + text[end:])
    changed = [p.relative_to(target).as_posix() for p in target.rglob('*')
               if p.is_file() and p.read_bytes() != (package / p.relative_to(target)).read_bytes()]
    if changed != ['harness/run_evaluation.py']:
        raise ValueError('Overlay modified a protected file')
    protected = {name: sha(target / name) for name in PROTECTED}
    manifest = dict(pipeline=VERSION, upstream_run_sha256=sha(original),
                    overlay_run_sha256=sha(target / 'harness/run_evaluation.py'),
                    changed_files=changed, protected_sha256=protected)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def copy_applicator(container, source):
    # The upstream helper derives a shared *.tar path from the source filename.
    # Eight threads copying this one source race on creation/unlink. Each call
    # owns its archive bytes; no shared temporary file or global lock is needed.
    payload = Path(source).read_bytes()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        member = tarfile.TarInfo('checked_patch.py')
        member.size = len(payload); member.mode = 0o644
        archive.addfile(member, io.BytesIO(payload))
    if not container.put_archive('/tmp', buffer.getvalue()):
        raise RuntimeError('Container rejected the applicator archive')


def apply_in_container(container, instance_id, logger, log_dir, patch_path):
    from swebench.harness.utils import EvaluationError
    dataset = Path(os.environ['SWE_CHECKED_DATASET'])
    if sha(dataset) != os.environ['SWE_CHECKED_DATASET_SHA256']:
        raise ValueError('Checked dataset hash changed')
    rows = {r['instance_id']: r for r in json.loads(dataset.read_text())}
    source = Path(__file__).with_name('checked_patch.py')
    copy_applicator(container, source)
    process = container.exec_run(['python', '/tmp/checked_patch.py', '--repo', '/testbed',
        '--patch', str(patch_path), '--base-commit', rows[instance_id]['base_commit'],
        '--output', '/tmp/patch-application.json'], workdir='/testbed', user='root')
    output = container.exec_run(['cat', '/tmp/patch-application.json'])
    if output.exit_code:
        raise EvaluationError(instance_id, 'Checked applicator did not retain evidence', logger)
    audit = json.loads(output.output)
    if audit['prediction_sha256'] != sha(Path(log_dir, 'patch.diff')):
        raise ValueError('Prediction bytes changed across the container boundary')
    Path(log_dir, 'patch-application.json').write_text(json.dumps(audit, indent=2) + '\n')
    if process.exit_code or not audit['applied']:
        raise EvaluationError(instance_id, 'Checked forward patch failed: ' + audit.get('error', ''), logger)
    logger.info('>>>>> Applied Patch: %s; materialization_errors=%d', VERSION,
                len(audit['materialization_errors']))


def validity_from_status(required, statuses, report, application, setup_error=False):
    missing = sorted(set(required) - set(statuses))
    skipped = sorted(i for i in required if statuses.get(i) == 'SKIPPED')
    issues = []
    if not application.get('applied'):
        issues.append('patch_application_failed')
    if application.get('materialization_errors'):
        issues.append('prediction_not_fully_materialized')
    if missing:
        issues.append('required_test_status_missing')
    if skipped:
        issues.append('required_test_skipped')
    if setup_error:
        issues.append('pre_test_setup_error')
    valid = not issues
    return dict(pipeline=VERSION, official_resolved=report['resolved'],
                qualified_resolved=report['resolved'] if valid else None,
                execution_valid=valid, issues=issues, missing_required_ids=missing,
                skipped_required_ids=skipped, official_report_modified=False)


def summarize_checked(rows, reports, selected, validity, full=False):
    """Keep official counts visible, but never qualify incomplete input/capture."""
    from core import summarize
    result = summarize(rows, reports, selected, full=full)
    by = {r['instance_id']:r for r in rows}
    active = {i for i in selected if by[i]['model_patch'].strip()}
    invalid = sorted(i for i in active if i not in reports or not validity.get(i,{}).get('execution_valid'))
    qualified = sorted(i for i in active-set(invalid) if validity[i]['qualified_resolved'] is True)
    for iid in active.intersection(validity):
        if validity[iid].get('execution_valid'):
            if iid not in reports or validity[iid].get('qualified_resolved') != reports[iid]['resolved']:
                raise ValueError('Supplemental validity contradicts the official report')
    result.update(result_basis='unaltered official reports; supplemental qualification is separate',
        official_reports_complete=result['complete'], official_resolved_count=result['resolved_count'],
        official_subset_resolved_rate=result['subset_resolved_rate'],
        qualified_resolved_count=len(qualified), qualified_resolved_ids=qualified,
        invalid_or_unchecked_ids=invalid, qualified_complete=not invalid)
    if invalid:
        result.update(complete=False, verified_score=None, subset_resolved_rate=None)
    return result


def audit_case(spec, directory):
    from swebench.harness.grading import get_logs_eval
    directory = Path(directory)
    report = json.loads((directory / 'report.json').read_text())[spec.instance_id]
    application = json.loads((directory / 'patch-application.json').read_text())
    log = directory / 'test_output.txt'
    statuses, found = get_logs_eval(spec, log)
    text = log.read_text(errors='replace').split('>>>>> Start Test Output', 1)[0]
    setup_error = bool(re.search(r'error: subprocess-exited-with-error|ERROR: Failed building wheel|ModuleNotFoundError:', text))
    result = validity_from_status(spec.FAIL_TO_PASS + spec.PASS_TO_PASS, statuses,
                                  report, application, setup_error)
    if not found:
        result.update(execution_valid=False, qualified_resolved=None)
        result['issues'].append('official_test_markers_missing')
    (directory / 'validity.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
