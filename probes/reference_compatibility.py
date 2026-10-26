"""Audit reference grading definitions and execution evidence without changing scores.

An archived unresolved result may come from different required IDs, failed
collection, or a skipped required test. Preserve both raw official outcomes;
never treat an explanation of a difference as a successful reproduction.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

GROUPS = ('FAIL_TO_PASS', 'PASS_TO_PASS')


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def required_ids(row):
    result = {}
    for group in GROUPS:
        values = row[group]
        if isinstance(values, str):
            values = json.loads(values)
        if (not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values)
                or len(values) != len(set(values))):
            raise ValueError('Invalid/duplicate required test IDs: ' + group)
        result[group] = sorted(values)
    if set(result[GROUPS[0]]) & set(result[GROUPS[1]]):
        raise ValueError('A test cannot be both FAIL_TO_PASS and PASS_TO_PASS')
    return result


def grading_definition(row):
    definition = {k: row[k] for k in ['instance_id', 'repo', 'base_commit', 'version']}
    definition['test_patch_sha256'] = hashlib.sha256(row['test_patch'].encode()).hexdigest()
    definition['required_tests'] = required_ids(row)
    return definition


def execution_evidence(required, statuses, text, parser_found):
    ids = {i for values in required.values() for i in values}
    missing = sorted(ids - statuses.keys())
    skipped = sorted(i for i in ids if statuses.get(i) == 'SKIPPED')
    collection = [line.strip() for line in text.splitlines()
                  if re.match(r'^\s*_*\s*ERROR collecting\s+', line)]
    # An assertion failure with captured test status is an ordinary unresolved
    # result. Collection failure or missing/skip status cannot serve as a clean
    # behavioral reference for testing prediction quality.
    issues = []
    if not parser_found:
        issues.append('official_test_output_not_found')
    if missing:
        issues.append('required_test_status_missing')
    if skipped:
        issues.append('required_test_skipped')
    if collection:
        issues.append('test_collection_error')
    return dict(valid=not issues, issues=issues, missing_required_ids=missing,
                skipped_required_ids=skipped, collection_error_lines=collection,
                failed_required_ids=sorted(i for i in ids if statuses.get(i) in ('FAILED', 'ERROR')))


def session_versions(text):
    # Observe only what headers prove. Equal Python/pytest versions do not prove
    # equal environments, image digests, CPU availability or warning policy.
    result = {}
    patterns = {'python': r'^platform \S+ -- Python ([0-9.]+)',
                'pytest': r'^platform \S+ --[^\n]*\bpytest-([0-9][A-Za-z0-9.+_-]*)'}
    for name, pattern in patterns.items():
        values = sorted(set(re.findall(pattern, text, re.M)))
        if values:
            result[name] = values
    return result


def report_required_ids(report):
    groups = report.get('tests_status', {})
    if set(GROUPS)-groups.keys():
        raise ValueError('Report must retain required-test status groups')
    result = {}
    for group in GROUPS:
        success, failure = groups[group].get('success'), groups[group].get('failure')
        if (not isinstance(success,list) or not isinstance(failure,list)
                or any(not isinstance(i,str) for i in success+failure)
                or len(success+failure) != len(set(success+failure))):
            raise ValueError('Invalid/overlapping report buckets')
        result[group] = sorted(success+failure)
    return result


def audit_reference(current, reference, current_report, reference_report,
                    current_statuses, reference_statuses, current_log='', reference_log='',
                    current_parser_found=True, reference_parser_found=True):
    if current['instance_id'] != reference['instance_id']:
        raise ValueError('Reference instance differs')
    for report in [current_report, reference_report]:
        if type(report.get('resolved')) is not bool:
            raise ValueError('Raw official resolved must be boolean')
    left, right = grading_definition(current), grading_definition(reference)
    delta = {group: dict(only_current=sorted(set(left['required_tests'][group])-set(right['required_tests'][group])),
                        only_reference=sorted(set(right['required_tests'][group])-set(left['required_tests'][group])))
             for group in GROUPS}
    fields = [k for k in ['repo','base_commit','version','test_patch_sha256'] if left[k] != right[k]]
    tests_equal = all(not v for group in delta.values() for v in group.values())
    current_evidence = execution_evidence(left['required_tests'],current_statuses,current_log,current_parser_found)
    reference_evidence = execution_evidence(right['required_tests'],reference_statuses,reference_log,reference_parser_found)
    reference_report_matches_definition = report_required_ids(reference_report) == right['required_tests']
    current_report_matches_definition = report_required_ids(current_report) == left['required_tests']
    current_versions, reference_versions = session_versions(current_log), session_versions(reference_log)
    versions_differ = {k:dict(current=current_versions[k],reference=reference_versions[k])
                       for k in current_versions.keys() & reference_versions.keys()
                       if current_versions[k] != reference_versions[k]}
    reasons = []
    if fields or not tests_equal:
        reasons.append('grading_definition_mismatch')
    if not reference_report_matches_definition:
        reasons.append('reference_report_definition_mismatch')
    if not current_report_matches_definition:
        reasons.append('current_report_definition_mismatch')
    if not reference_evidence['valid']:
        reasons.append('reference_execution_not_valid_control')
    if not current_evidence['valid']:
        reasons.append('current_execution_not_valid_control')
    if versions_differ:
        reasons.append('observed_runtime_version_mismatch')
    same_outcome = current_report['resolved'] == reference_report['resolved']
    if reasons:
        status = 'comparison_blocked'
    else:
        status = 'observed_outcome_match' if same_outcome else 'unexplained_outcome_difference'
    return dict(instance_id=current['instance_id'],comparison_status=status,blocking_reasons=reasons,
        current_official_resolved=current_report['resolved'],reference_official_resolved=reference_report['resolved'],
        raw_outcomes_match=same_outcome,grading_definition_equal=not fields and tests_equal,
        required_test_delta=delta,other_grading_fields_differ=fields,
        reference_report_matches_pinned_definition=reference_report_matches_definition,
        current_report_matches_pinned_definition=current_report_matches_definition,
        current_execution=current_evidence,reference_execution=reference_evidence,
        observed_runtime_version_delta=versions_differ,
        current_definition=left,reference_definition=right,
        current_report_sha256=canonical_sha(current_report),reference_report_sha256=canonical_sha(reference_report),
        current_log_sha256=hashlib.sha256(current_log.encode()).hexdigest(),
        reference_log_sha256=hashlib.sha256(reference_log.encode()).hexdigest(),
        scientific_reproduction_qualified=False,official_reports_modified=False,
        qualification_note='A matching outcome or absence of known blockers does not certify complete runtime identity or scientific stability')


def attach_compatibility(comparison, audit, reports, current_log_sha256):
    """Supplement the existing comparison, retaining its scores and all 500 IDs."""
    by = {r['instance_id']:r for r in comparison['cases']}
    seen = set()
    for row in audit['cases']:
        iid=row['instance_id']
        if iid in seen or iid not in by:
            raise ValueError('Repeated or outside-scope compatibility evidence')
        seen.add(iid)
        if (by[iid]['actual_resolved'] != row['current_official_resolved'] or
                by[iid]['expected_resolved'] != row['reference_official_resolved']):
            raise ValueError('Compatibility evidence belongs to different run outcomes')
        if (iid not in reports or canonical_sha(reports[iid])!=row.get('current_report_sha256')
                or current_log_sha256 is None or current_log_sha256.get(iid)!=row.get('current_log_sha256')):
            raise ValueError('Compatibility report/log provenance differs from the compared run')
    result=copy.deepcopy(comparison)
    result['reference_compatibility']=copy.deepcopy(audit)
    result['known_incompatible_reference_ids']=sorted(r['instance_id'] for r in audit['cases'] if r['blocking_reasons'])
    result['compatibility_audited_case_count']=len(seen)
    result['reference_reproduction_qualified']=False
    return result


def regrade_saved_log(current, reference, prediction, log_path, expected_sha256):
    """Regrade unchanged captured output with official reference IDs, never rerun tests.

This uses the pinned installed grader, not a reconstruction of the complete
historical environment. Refuse different source/test bodies or log bytes.
"""
    left,right=grading_definition(current),grading_definition(reference)
    if any(left[k]!=right[k] for k in ['instance_id','repo','base_commit','version','test_patch_sha256']):
        raise ValueError('An existing log cannot regrade different source/test bodies')
    if (prediction.get('instance_id')!=left['instance_id'] or
            not isinstance(prediction.get('model_patch'),str) or not prediction['model_patch'].strip()):
        raise ValueError('A nonempty matching prediction is required')
    path=Path(log_path); actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual!=expected_sha256:
        raise ValueError('Captured log hash changed')
    import importlib.metadata
    import inspect
    from swebench.harness import grading
    from swebench.harness.test_spec.test_spec import make_test_spec
    if importlib.metadata.version('swebench')!='4.1.0':
        raise ValueError('This replay requires the pinned official 4.1.0 grader')
    grading_hash=hashlib.sha256(Path(inspect.getsourcefile(grading)).read_bytes()).hexdigest()
    if grading_hash!='88fc500ebaf692a53457149cec24dcf82dfa2d48f98021a894a087d275f61df4':
        raise ValueError('Unknown grader source')
    def grade(row):
        # Set comparison uses canonical order, but the native grader emits
        # status lists in dataset order. Preserve that order for exact replay.
        normalized=dict(row)
        for group in GROUPS:
            normalized[group]=json.loads(row[group]) if isinstance(row[group],str) else list(row[group])
        spec=make_test_spec(normalized,namespace='swebench')
        return grading.get_eval_report(spec,prediction,path,True)
    result=dict(current_definition_report=grade(current),reference_definition_report=grade(reference),
        captured_log_sha256=actual,grader_version='4.1.0',grading_source_sha256=grading_hash,
        tests_executed=False,official_test_files_modified=False,
        historical_runtime_reproduced=False,mode='offline regrade of the same log under official required-test lists')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=actual:
        raise ValueError('Log changed during replay')
    return result


def audit_bundle(bundle, regrade_existing_logs=False):
    """Run a saved evidence bundle; no compute, network or score rewriting."""
    provenance = bundle['provenance']
    for name in ['current_revision','reference_revision']:
        if not re.fullmatch('[0-9a-f]{40}', provenance[name]):
            raise ValueError('Immutable dataset revisions required')
    results = []
    seen = set()
    for case in bundle['cases']:
        fields=dict(case);regrade=fields.pop('regrade',None)
        result = audit_reference(**fields)
        if regrade_existing_logs:
            if regrade is None:
                raise ValueError('Replay requires explicit prediction/log provenance')
            if regrade['expected_sha256']!=result['current_log_sha256']:
                raise ValueError('Audited text differs from the native replay log')
            replay=regrade_saved_log(fields['current'],fields['reference'],**regrade)
            iid=result['instance_id']
            if replay['current_definition_report'][iid]!=fields['current_report']:
                raise ValueError('Native parser replay differs from the retained official report')
            result['cached_log_regrade']=replay
        if result['instance_id'] in seen:
            raise ValueError('Repeated comparison case')
        seen.add(result['instance_id']); results.append(result)
    if not results:
        raise ValueError('Empty comparison bundle')
    return dict(provenance=provenance,cases=results,case_count=len(results),
        raw_outcome_match_count=sum(r['raw_outcomes_match'] for r in results),
        comparison_blocked_count=sum(bool(r['blocking_reasons']) for r in results),
        unexplained_outcome_difference_count=sum(r['comparison_status']=='unexplained_outcome_difference' for r in results),
        official_reports_modified=False,new_benchmark_score=None,scientific_reproduction_qualified=False)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--regrade-existing-logs',action='store_true',
                        help='Use installed official 4.1.0 to regrade frozen logs; never execute tests')
    args=parser.parse_args()
    if args.out.exists():
        parser.error('Use a new output file; retain prior audits')
    result=audit_bundle(json.loads(args.bundle.read_text(encoding='utf-8')),args.regrade_existing_logs)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ['cases','provenance']}))
