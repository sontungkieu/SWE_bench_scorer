"""Read-only phase accounting from original harness logs, never a scorer.

Framework summaries are elapsed time printed by pytest/unittest/SymPy, not CPU
time or a perfect isolation of test bodies. Per-case sums overlap across workers.
"""
from collections import defaultdict
from datetime import datetime
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
from statistics import median
import tarfile


STAMP = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) - ')


def framework_times(text):
    text=re.sub(r'\x1b\[[0-9;]*m','',text)
    result=[]
    for line in text.splitlines():
        match=re.search(r'^=+ (?!tests finished:).*\bin ([\d.]+)(?:s(?:\s|=|$)| seconds\b)',line)
        kind='pytest'
        if not match:
            match=re.search(r'^Ran \d+ tests? in ([\d.]+)s$',line.strip());kind='unittest'
        if not match:
            match=re.search(r'^=+ tests finished: .*\bin ([\d.]+) seconds',line);kind='sympy'
        if match: result.append({'framework':kind,'seconds':float(match[1]),'line':line})
    return result


def parse_log(iid,group,log,output='',eval_script=None):
    markers={};runtime=[]
    for line in log.splitlines():
        match=STAMP.match(line)
        if not match: continue
        dt=datetime.strptime(match[1],'%Y-%m-%d %H:%M:%S,%f')
        for key,needle in [('create_begin','Creating container for '),
                           ('created',' created: '),('started',' started: '),
                           ('eval_dispatch','Eval script for '),
                           ('stop_begin','Attempting to stop container '),
                           ('remove_begin','Attempting to remove container ')]:
            if needle in line:
                if key in markers: raise ValueError('Duplicate phase marker: '+key)
                markers[key]=dt
        if re.search(r'Container .* removed\.',line):markers['removed']=dt
        match=re.search(r'Test runtime: ([0-9_.]+) seconds',line)
        if match:runtime.append(float(match[1].replace('_','')));markers['runtime_end']=dt
    required=['create_begin','created','started','eval_dispatch','runtime_end','stop_begin','remove_begin','removed']
    if set(required)-markers.keys() or len(runtime)!=1:
        raise ValueError('Missing/duplicate phase timing evidence for '+iid)
    if any(markers[b]<markers[a] for a,b in zip(required,required[1:])):
        raise ValueError('Nonmonotonic logged phase markers')
    def delta(a,b):return (markers[b]-markers[a]).total_seconds()
    times=framework_times(output)
    framework_sum=sum(r['seconds'] for r in times)
    outside=runtime[0]-framework_sum if times and runtime[0]+.02>=framework_sum else None
    return {'instance_id':iid,'group':group,
        'markers':{k:v.isoformat() for k,v in markers.items()},
        'container_create_seconds':delta('create_begin','created'),
        'container_start_seconds':delta('created','started'),
        'patch_and_eval_preparation_seconds':delta('started','eval_dispatch'),
        'eval_dispatch_to_runtime_log_seconds':delta('eval_dispatch','runtime_end'),
        'eval_script_seconds':runtime[0],
        'post_eval_grading_seconds':delta('runtime_end','stop_begin'),
        'container_stop_seconds':delta('stop_begin','remove_begin'),
        'container_remove_seconds':delta('remove_begin','removed'),
        'container_cleanup_seconds':delta('stop_begin','removed'),
        'task_service_seconds':delta('create_begin','removed'),
        'framework_printed_timings':times,
        'framework_printed_seconds_sum':framework_sum if times else None,
        'eval_outside_framework_printed_seconds':outside,
        'eval_script_sha256':hashlib.sha256(eval_script).hexdigest() if eval_script is not None else None}


def from_tree(root):
    rows=[]
    for path in sorted(Path(root).glob('**/run_instance.log')):
        output=path.with_name('test_output.txt');script=path.with_name('eval.sh')
        rows.append(parse_log(path.parent.name,path.parents[2].name,path.read_text(errors='replace'),
                              output.read_text(errors='replace') if output.exists() else '',
                              script.read_bytes() if script.exists() else None))
    return validate(rows)


def from_archive(path_or_stream):
    kwargs={'fileobj':path_or_stream} if hasattr(path_or_stream,'read') else {'name':path_or_stream}
    with tarfile.open(**kwargs) as archive:
        members={m.name:m for m in archive.getmembers() if m.isfile()}
        rows=[]
        for name,member in members.items():
            if not name.endswith('/run_instance.log'):continue
            p=PurePosixPath(name)
            def read(suffix):
                item=members.get(str(p.with_name(suffix)))
                return archive.extractfile(item).read() if item else None
            output=read('test_output.txt');script=read('eval.sh')
            rows.append(parse_log(p.parent.name,p.parents[2].name,
                         archive.extractfile(member).read().decode(errors='replace'),
                         output.decode(errors='replace') if output else '',script))
        if not rows:
            nested=[m for n,m in members.items() if PurePosixPath(n).name=='score-raw-artifacts.tar.gz']
            if len(nested)!=1: raise ValueError('Expected one nested scoring archive')
            return from_archive(io.BytesIO(archive.extractfile(nested[0]).read()))
    return validate(rows)


def validate(rows):
    if len(rows)!=24 or len({r['instance_id'] for r in rows})!=24:
        raise ValueError('Exactly 24 unique control logs required')
    return sorted(rows,key=lambda r:r['instance_id'])


def summarize(rows):
    groups=defaultdict(list)
    for row in rows:groups[row['group']].append(row)
    group_stats=[]
    for group,members in sorted(groups.items()):
        begin=min(datetime.fromisoformat(r['markers']['create_begin']) for r in members)
        graded=max(datetime.fromisoformat(r['markers']['stop_begin']) for r in members)
        end=max(datetime.fromisoformat(r['markers']['removed']) for r in members)
        group_stats.append({'group':group,'count':len(members),
                            'service_span_seconds':(end-begin).total_seconds(),
                            'tail_after_last_grade_seconds':(end-graded).total_seconds()})
    fields=['container_create_seconds','container_start_seconds','patch_and_eval_preparation_seconds',
            'eval_script_seconds','post_eval_grading_seconds','container_stop_seconds',
            'container_remove_seconds','task_service_seconds']
    timed=[r for r in rows if r['framework_printed_seconds_sum'] is not None]
    return {'task_count':len(rows),'groups':group_stats,
            'sum_per_case_seconds_overlapping_workers':{k:sum(r[k] for r in rows) for k in fields},
            'summed_group_service_spans_seconds':sum(g['service_span_seconds'] for g in group_stats),
            'summed_group_cleanup_tails_seconds':sum(g['tail_after_last_grade_seconds'] for g in group_stats),
            'framework_timing_coverage':len(timed),
            'sum_framework_printed_seconds':sum(r['framework_printed_seconds_sum'] for r in timed),
            'sum_eval_outside_framework_seconds_for_covered_tasks':sum(r['eval_outside_framework_printed_seconds'] for r in timed if r['eval_outside_framework_printed_seconds'] is not None),
            'not_cpu_time_or_job_wall':True,'per_case':rows}


def compare_runs(runs,baseline):
    reference={r['instance_id']:r for r in runs[baseline]}
    result={}
    for name,rows in runs.items():
        if {r['instance_id'] for r in rows}!=set(reference):raise ValueError('Control sets differ')
        ratios=[r['eval_script_seconds']/reference[r['instance_id']]['eval_script_seconds'] for r in rows]
        result[name]=dict(summarize(rows),paired_median_eval_ratio=median(ratios),
            eval_script_byte_matches=sum(r['eval_script_sha256'] is not None and
                r['eval_script_sha256']==reference[r['instance_id']]['eval_script_sha256'] for r in rows))
    return {'baseline':baseline,'runs':result,
            'limitations':['One observation per layout; no causal isolation of snapshot size',
                'Framework summaries include collection/fixtures and differ between frameworks',
                'Time outside framework summaries is not measured pip-only or I/O-only time',
                'Per-case durations overlap; summed group spans exclude harness startup/imports',
                'Official reports are scored separately and never modified by this analysis']}
