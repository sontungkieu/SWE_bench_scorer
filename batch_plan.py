"""Pure multi-file snapshot scheduling and independent scientific accounting."""
from collections import defaultdict
from decimal import Decimal
import math
import re

MODE='optimizeN11batch-v1'
MAX_FILES=32
WORKERS=8
RATE=Decimal('0.0010576')
EGRESS_RESERVE=Decimal('0.25')
WAVE_RESERVE=780
VM_OVERHEAD=180
MAX_VM_LIFETIME=21600


def validate_budget(budget,prior,cap):
    if (not all(v.is_finite() for v in [budget,prior,cap]) or
            budget<=EGRESS_RESERVE or prior<0 or cap<=0 or prior+budget>cap):
        raise ValueError('Invalid batch budget or cumulative cap exceeded')


def lifetime_for(job_count,used_seconds,budget):
    validate_budget(budget,Decimal(0),budget)
    if type(job_count) is not int or not 1<=job_count<=500*MAX_FILES or not math.isfinite(used_seconds) or used_seconds<0:
        raise ValueError('Invalid batch allocation accounting')
    available=(budget-EGRESS_RESERVE)/RATE-Decimal(str(used_seconds))
    maximum=math.floor(available)-10
    if maximum<WAVE_RESERVE+VM_OVERHEAD:return None
    return min(math.ceil(job_count/WORKERS)*WAVE_RESERVE+VM_OVERHEAD,maximum,MAX_VM_LIFETIME)


def validate_file_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value):
        raise ValueError('File ID must be a safe unique slug, up to 64 characters')


def job_id(file_id,iid):
    validate_file_id(file_id)
    return file_id+'::'+iid


def assignments(files,dataset,groups,selected_ids=None):
    """Group by snapshot then instance; each file keeps its own prediction identity."""
    data_ids=[r['instance_id'] for r in dataset]
    if len(data_ids)!=500 or len(set(data_ids))!=500 or not 1<=len(files)<=MAX_FILES:
        raise ValueError('Expected 500 unique dataset IDs and a bounded nonempty batch')
    selected=data_ids if selected_ids is None else list(selected_ids)
    if not selected or len(selected)!=len(set(selected)) or set(selected)-set(data_ids):
        raise ValueError('Invalid selected dataset scope')
    by_file={};expected=set()
    for f in files:
        validate_file_id(f['id'])
        if f['id'] in by_file:raise ValueError('Duplicate file ID')
        rows=f['rows'];by={r['instance_id']:r for r in rows}
        if len(by)!=len(rows) or set(by)-set(data_ids):raise ValueError('Duplicate/outside prediction ID')
        invalid=set(f.get('input_errors',{}))
        if invalid-set(by) or any(not by[i]['model_patch'].strip() for i in invalid):raise ValueError('Invalid input-error scope')
        by_file[f['id']]=by
        expected.update(job_id(f['id'],i) for i in selected if i in by and by[i]['model_patch'].strip() and i not in invalid)
    planned=[];seen=set();seen_images=set()
    for group in groups:
        tasks=[]
        for iid in group['instance_ids']:
            if iid in seen_images or iid not in selected:raise ValueError('Repeated/outside shard instance')
            seen_images.add(iid)
            for f in files:
                row=by_file[f['id']].get(iid)
                if row is not None and row['model_patch'].strip() and iid not in f.get('input_errors',{}):
                    key=job_id(f['id'],iid)
                    if key in seen:raise ValueError('Repeated job')
                    seen.add(key);tasks.append(dict(job_id=key,file_id=f['id'],instance_id=iid))
        if tasks:planned.append(dict(group,jobs=tasks))
    if seen!=expected:raise ValueError('Batch assignment misses or adds nonempty predictions')
    return selected,planned


def waves(jobs,workers=WORKERS):
    if type(workers) is not int or not 1<=workers<=WORKERS:raise ValueError('Worker bound exceeded')
    seen=set()
    for task in jobs:
        key=job_id(task['file_id'],task['instance_id'])
        if task['job_id']!=key or key in seen:raise ValueError('Invalid/duplicate composite job')
        seen.add(key)
    return [jobs[i:i+workers] for i in range(0,len(jobs),workers)]


def split_wave(wave):
    if not 1<=len(wave)<=WORKERS:raise ValueError('Global worker bound exceeded')
    waves(wave)
    result=defaultdict(list)
    for task in wave:result[task['file_id']].append(task['instance_id'])
    if any(len(ids)!=len(set(ids)) for ids in result.values()):raise ValueError('Repeated instance within file')
    return dict(result)


def summarize_file(rows,selected,reports,validity,attempted,full,input_errors=None):
    by={r['instance_id']:r for r in rows};selected=list(selected);scope=set(selected)
    if not scope or len(scope)!=len(selected) or (full and len(scope)!=500):raise ValueError('Invalid summary scope')
    if len(by)!=len(rows) or set(reports)-scope or set(validity)-scope:raise ValueError('Unexpected result scope')
    active={i for i in selected if i in by and by[i]['model_patch'].strip()}
    blocked=set(input_errors or {})
    if blocked-active:raise ValueError('Input-error scope differs from nonempty predictions')
    schedulable=active-blocked
    if len(attempted)!=len(set(attempted)) or set(attempted)-schedulable or set(reports)-set(attempted):
        raise ValueError('Repeated, unattempted or inactive result')
    if any(type(r.get('resolved')) is not bool for r in reports.values()):raise ValueError('Invalid official verdict')
    cases=[];qualified=[];invalid=[]
    for iid in selected:
        r=reports.get(iid);v=validity.get(iid,{})
        if iid not in by:status='missing_source_prediction';outcome=False;basis='no_submitted_patch'
        elif iid not in active:status='empty_patch';outcome=False;basis='no_submitted_patch'
        elif iid in blocked:status='input_error';outcome=None;basis='input_validation'
        elif r is None:status='missing_official_report' if iid in attempted else 'not_evaluated';outcome=None;basis='unknown'
        else:status='resolved' if r['resolved'] else 'unresolved';outcome=r['resolved'];basis='official_report'
        good=bool(r is not None and v.get('execution_valid') is True)
        if good and (type(v.get('qualified_resolved')) is not bool or v['qualified_resolved']!=outcome):
            raise ValueError('Validity contradicts the official report')
        if iid in active:
            if not good:invalid.append(iid)
            elif outcome:qualified.append(iid)
        cases.append(dict(instance_id=iid,status=status,official_resolved=outcome,
            qualified_resolved=outcome if good or iid not in active else None,outcome_basis=basis,
            validity_issues=v.get('issues',[]) if r else []))
    resolved=sum(r['resolved'] for r in reports.values());scheduled_complete=set(reports)==schedulable
    reports_complete=scheduled_complete and not blocked
    return dict(scope='full_verified' if full else 'explicit_subset',selected_count=len(scope),
        source_prediction_count=len(rows),active_prediction_count=len(active),attempted_count=len(attempted),
        schedulable_prediction_count=len(schedulable),input_error_count=len(blocked),
        official_report_count=len(reports),official_resolved_count=resolved,
        empty_patch_count=sum(c['status']=='empty_patch' for c in cases),
        missing_source_prediction_count=sum(c['status']=='missing_source_prediction' for c in cases),
        missing_attempted_report_ids=sorted(set(attempted)-set(reports)),
        pending_nonempty_ids=sorted(schedulable-set(attempted)),
        execution_complete=set(attempted)==schedulable,scheduled_reports_complete=scheduled_complete,reports_complete=reports_complete,
        qualified_complete=reports_complete and not invalid,qualified_resolved_count=len(qualified),
        invalid_or_unchecked_ids=invalid,verified_score=resolved/500 if full and reports_complete and not invalid else None,
        subset_resolved_rate=resolved/len(scope) if not full and reports_complete and not invalid else None,
        scientific_stability_qualified=False,cases=cases)


def merge_output(expected_jobs,payload,reports,validity,attempted):
    """Retain completed waves even when a later worker dies; reject cross-file leaks."""
    expected={t['job_id']:t for t in expected_jobs}
    ids=payload['attempted_job_ids'];found=payload['reports'];checks=payload['validity']
    if (len(expected)!=len(expected_jobs) or len(ids)!=len(set(ids)) or set(ids)-set(expected)
            or set(found)-set(ids) or set(checks)!=set(found)
            or set(payload.get('errors',{}))-set(ids) or set(payload.get('errors',{}))&set(found)):
        raise ValueError('Invalid worker result coverage')
    if set(ids)&set(attempted) or set(found)&set(reports):raise ValueError('Duplicate batch attempt')
    if any(type(row.get('resolved')) is not bool for row in found.values()):raise ValueError('Invalid official verdict')
    reports.update(found);validity.update(checks);attempted.extend(ids)
