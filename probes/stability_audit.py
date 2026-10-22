"""Keep official results intact while rejecting unsupported stability claims.

An observed pass is not a stability certificate. The known issues apply to
execution environments, so a clean rerun does not clear their qualification.
This module neither excludes tests nor changes the official denominator.
"""
import argparse
import json
from pathlib import Path


KNOWN_RISKS = {
    'matplotlib__matplotlib-20859': {
        'required_test': 'lib/matplotlib/tests/test_legend.py::test_warn_big_data_best_loc',
        'mechanism': 'real_elapsed_time_warning_threshold',
        'source_verified': True, 'historical_failure_cause_fully_proven': False,
        'evidence': 'evidence/2026-10-22-matplotlib-timing/README.md',
    },
    'psf__requests-1921': {
        'required_test': 'test_requests.py::RequestsTestCase::test_cookie_removed_on_expire',
        'mechanism': 'external_http_dependency_with_unexplained_historical_cookie_failure',
        'source_verified': True, 'historical_failure_cause_fully_proven': False,
        'evidence': 'evidence/2026-10-22-stability-diagnosis/README.md',
    },
}


def assess(reports, selected):
    selected = list(selected)
    if not selected or len(selected) != len(set(selected)):
        raise ValueError('Expected nonempty unique selected IDs')
    if set(reports) - set(selected):
        raise ValueError('Extra reports outside selection')
    missing = sorted(set(selected) - set(reports))
    invalid = sorted(iid for iid, row in reports.items()
                     if not isinstance(row, dict) or type(row.get('resolved')) is not bool)
    affected = []
    for iid in sorted(set(selected) & KNOWN_RISKS.keys()):
        risk = dict(KNOWN_RISKS[iid], instance_id=iid)
        row = reports.get(iid)
        states = row.get('tests_status', {}) if isinstance(row, dict) else {}
        passed = states.get('PASS_TO_PASS', {}).get('success', [])
        failed = states.get('PASS_TO_PASS', {}).get('failure', [])
        test = risk['required_test']
        if not isinstance(passed, list) or not isinstance(failed, list) or test in passed and test in failed:
            raise ValueError('Invalid/contradictory required-test status')
        risk['current_required_test'] = 'pass' if test in passed else 'fail' if test in failed else 'unobserved'
        affected.append(risk)
    return {
        'selected_count': len(selected), 'report_count': len(reports),
        'raw_resolved_count': sum(row.get('resolved') is True for row in reports.values() if isinstance(row, dict)),
        'missing_report_ids': missing, 'invalid_report_ids': invalid,
        'status': 'incomplete' if missing or invalid else 'blocked_known_instability' if affected else 'stability_not_assessed',
        'scientific_stability_qualified': False,
        'research_score': None,
        'known_risks': affected,
        'official_reports_modified': False, 'tests_excluded': [],
        'interpretation': 'raw official outcome retained; no rerun-to-pass, denominator reduction or stability guarantee',
    }


def write_audit(out):
    reports = json.loads((out/'reports.json').read_text())
    selection = json.loads((out/'selection.json').read_text())
    ids = [row['instance_id'] for row in selection['selected']]
    result = assess(reports, ids)
    path = out/'scientific-validity.json'
    if path.exists() and json.loads(path.read_text()) != result:
        raise ValueError('Refuse to overwrite different scientific validity evidence')
    path.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result))
    return 2 if result['status'] != 'stability_not_assessed' else 0


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    raise SystemExit(write_audit(p.parse_args().out))
