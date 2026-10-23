"""Full-dataset outcome comparison without fabricating absent official reports."""
from collections import Counter
from core import validate_predictions


def compare_reference(dataset,rows,expected,reports,attempted_ids):
    ids=[r['instance_id'] for r in dataset]
    if len(ids)!=500 or len(set(ids))!=500 or set(expected)!=set(ids):
        raise ValueError('Exactly 500 unique benchmark IDs and expected outcomes required')
    if any(type(v) is not bool for v in expected.values()):raise ValueError('Expected outcomes must be booleans')
    by={r['instance_id']:r for r in validate_predictions(rows)}
    if set(by)-set(ids):raise ValueError('Prediction outside benchmark')
    active={i for i,r in by.items() if r['model_patch'].strip()}
    attempted=set(attempted_ids)
    if len(attempted)!=len(attempted_ids) or attempted-active or set(reports)-attempted:
        raise ValueError('Invalid, duplicate or out-of-scope execution evidence')
    if any(type(r.get('resolved')) is not bool for r in reports.values()):
        raise ValueError('Official resolved values must be booleans')
    cases=[]
    for iid in ids:
        report=reports.get(iid)
        if iid not in by:status='missing_source_prediction';actual=False
        elif iid not in active:status='empty_patch';actual=False
        elif iid not in attempted:status='not_evaluated';actual=None
        elif report is None:status='missing_official_report';actual=None
        else:actual=report['resolved'];status='resolved' if actual else 'unresolved'
        cases.append(dict(instance_id=iid,status=status,expected_resolved=expected[iid],actual_resolved=actual,
            official_report_present=report is not None,matches_reference=actual is not None and actual==expected[iid],
            outcome_basis='official_report' if report is not None else 'no_submitted_patch' if iid not in active else 'unknown'))
    counts=Counter(r['status'] for r in cases)
    score_complete=len(reports)==len(active)
    resolved=counts['resolved'];score=resolved/500 if score_complete else None
    return dict(dataset_count=500,source_prediction_count=len(by),active_prediction_count=len(active),
        expected_resolved_count=sum(expected.values()),resolved_count=resolved,official_report_count=len(reports),
        attempted_nonempty_count=len(attempted),counts=dict(counts),score_complete=score_complete,
        source_coverage_complete=len(by)==500,verified_score=score,
        expected_score=sum(expected.values())/500,
        score_agrees=score_complete and resolved==sum(expected.values()),
        matching_cases=sum(r['matches_reference'] for r in cases),
        all_instance_outcomes_agree=score_complete and all(r['matches_reference'] for r in cases),
        mismatched_or_unknown_ids=[r['instance_id'] for r in cases if not r['matches_reference']],cases=cases)
