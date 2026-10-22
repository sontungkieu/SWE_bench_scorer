"""Offline 500-task inventory and explicitly modelled snapshot cost strategies."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import heapq
import json
from pathlib import Path
import re
from statistics import mean, median, pstdev
import tarfile

GIB=1024**3
PIN='6a6fb72484bfc40f35cecbae1ae4615e9460905f729c57659df5f4ef9d7dec6e'


def load_observations(archive_path):
    found={}
    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith('run_instance.log'):continue
            iid=member.name.split('/')[-2]
            text=archive.extractfile(member).read().decode()
            times=re.findall(r'Test runtime: ([0-9_.]+) seconds',text)
            stamps=[]; stop=None; removed=None
            for line in text.splitlines():
                match=re.match(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) - ',line)
                if not match:continue
                dt=datetime.strptime(match[1],'%Y-%m-%d %H:%M:%S,%f');stamps.append(dt)
                if 'Attempting to stop container ' in line:stop=dt
                if re.search(r'Container .* removed\.',line):removed=dt
            if iid in found or len(times)!=1 or not stamps or stop is None or removed is None:
                raise ValueError('Missing/duplicate task timing evidence')
            found[iid]={'instance_id':iid,'eval_script_seconds':float(times[0].replace('_','')),
                'container_cleanup_logged_seconds':(removed-stop).total_seconds(),
                'task_service_logged_seconds':(removed-stamps[0]).total_seconds(),
                'scope':'historical N11 serial gold control; log timestamps, not CPU time'}
    if len(found)!=24:raise ValueError('Exactly 24 measured tasks expected')
    return found


def layers(row):
    return {b['diff_id']:b['uncompressed_size'] for b in row['layers']}


def cost_partition(rows, weights, n, cap=35*GIB):
    """Greedy marginal sum-of-VM-time partition under the preparation envelope.

    It is an offline heuristic, not a new cache/snapshot or a provider guarantee.
    """
    bins=[{'index':i,'images':[],'layers':{},'durations':[]} for i in range(n)]
    for row in sorted(rows,key=lambda r:(-weights[r['reference']],r['reference'])):
        image_layers=layers(row); choices=[]
        for b in bins:
            added=sum(size for key,size in image_layers.items() if key not in b['layers'])
            total=sum(b['layers'].values())+added
            if total<=cap and len(b['images'])<100:
                delta=queue_seconds(b['durations']+[weights[row['reference']]])-queue_seconds(b['durations'])
                # Seed N nonempty snapshots, then target total VM-time, not finish balance.
                seed=0 if not b['images'] else 1
                if all(item['images'] for item in bins):seed=0
                choices.append(((seed,delta,added,b['index']),b))
        if not choices:return None
        _,target=min(choices,key=lambda item:item[0])
        target['images'].append(row);target['layers'].update(image_layers)
        target['durations'].append(weights[row['reference']])
    if any(not b['images'] for b in bins):return None
    return [dict(index=b['index'],images=b['images']) for b in bins]


def queue_seconds(durations, workers=8):
    slots=[0.]*min(workers,len(durations));heapq.heapify(slots)
    for value in sorted(durations,reverse=True):
        first=heapq.heappop(slots);heapq.heappush(slots,first+value)
    return max(slots,default=0.)


def analyze(features, catalog, original, observations):
    tasks={r['instance_id']:r for r in features['tasks']}
    if features['dataset_sha256']!=PIN or len(tasks)!=len(features['tasks']) or len(tasks)!=500:
        raise ValueError('Wrong pinned dataset feature inventory')
    rows=original['images'];by_ref={r['reference']:r for r in rows}
    iid_ref={r['reference'].removeprefix('swebench/sweb.eval.x86_64.').removesuffix(':latest').replace('_1776_','__'):r['reference'] for r in rows}
    if set(iid_ref)!=set(tasks):raise ValueError('Image/dataset IDs differ')
    repo_samples=defaultdict(list)
    for iid,value in observations.items():repo_samples[tasks[iid]['repo']].append(value['task_service_logged_seconds'])
    if set(repo_samples)!={r['repo'] for r in tasks.values()}:raise ValueError('No measured task in a repo')
    proxies={iid_ref[iid]:median(repo_samples[row['repo']]) for iid,row in tasks.items()}
    current=[{'index':s['index'],'images':[by_ref[r['reference']] for r in s['images']]} for s in catalog['shards']]
    if len({r['reference'] for s in current for r in s['images']})!=500:raise ValueError('Catalog incomplete')
    def describe(groups):
        result=[]
        for group in groups:
            members=[tasks[next(iid for iid,ref in iid_ref.items() if ref==r['reference'])] for r in group['images']]
            layer_set={k:v for row in group['images'] for k,v in layers(row).items()}
            measured=[r['instance_id'] for r in members if r['instance_id'] in observations]
            ds=[proxies[r['reference']] for r in group['images']]
            result.append({'index':group['index'],'task_count':len(members),'repos':dict(Counter(r['repo'] for r in members)),
                'required_test_entries':sum(r['fail_to_pass_count']+r['pass_to_pass_count'] for r in members),
                'measured_task_count':len(measured),'unmeasured_task_count':len(members)-len(measured),
                'unique_layer_tar_gib':sum(layer_set.values())/GIB,
                'proxy_service_sum_seconds':sum(ds),'proxy_8_worker_queue_seconds':queue_seconds(ds)})
        counts=[r['task_count'] for r in result];queue=[r['proxy_8_worker_queue_seconds'] for r in result]
        # Fixed per-VM price => sum VM-seconds drives compute, not max finish time.
        return {'groups':result,'task_count':sum(counts),'count_max_min_ratio':max(counts)/min(counts),
                'count_coefficient_of_variation':pstdev(counts)/mean(counts),
                'layer_tar_gib_sum_with_replication':sum(r['unique_layer_tar_gib'] for r in result),
                'proxy_sum_vm_test_seconds':sum(queue),'proxy_parallel_test_makespan_seconds':max(queue),
                'proxy_vm_seconds_including_startup':{str(startup):sum(queue)+len(groups)*startup for startup in [5,15,30]},
                'not_measured_full500_runtime_or_billing':True}
    candidates={}
    for n in [9,10,11,12,16,20]:
        partition=cost_partition(rows,proxies,n)
        candidates[str(n)]=describe(partition) if partition else {'feasible_under_heuristic':False}
    return {'dataset_feature_pin':PIN,'task_count':500,'measured_gold_task_count':len(observations),
        'unmeasured_task_count':500-len(observations),'repo_runtime_prior_sample_counts':{k:len(v) for k,v in repo_samples.items()},
        'imputation':'median historical task-service wall per repo; 476 tasks have no measured duration',
        'test_count_is_not_runtime':True,'objective':'minimize sum allocated VM-seconds at a fixed resource profile, including startup',
        'candidate_heuristic':'seed N bins then minimize marginal sum of 8-worker LPT queue duration; not a proven optimum',
        'current_N11':describe(current),'offline_candidate_partitions':candidates,
        'observations':list(observations.values()),'all500_features':list(tasks.values()),
        'candidate_snapshots_built':False,'full_500_evaluated':False,
        'capacity_lower_bound_from_unique_tar_bytes':-(-original['unique_layer_tar_bytes']//(35*GIB)),
        '35_gib_is_internal_preparation_envelope_not_provider_limit':True,
        'candidate_assignment_refs':{n:[[r['reference'] for r in g['images']] for g in p] for n in ['16'] if (p:=cost_partition(rows,proxies,int(n))) is not None}}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--features',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]/'evidence'
    observations=load_observations(root/'2026-10-22-sharded-snapshot-pilot/raw-evidence.tar.gz')
    value=analyze(json.loads(args.features.read_text()),json.loads((root/'2026-10-22-verified-snapshots-90d/usable-shards-catalog.json').read_text()),
                  json.loads(gzip.decompress((root/'2026-10-22-verified-snapshots-90d/catalog-plan.json.gz').read_bytes())),observations)
    args.out.write_bytes((json.dumps(value,indent=2)+'\n').encode())
    print(json.dumps({k:v for k,v in value.items() if k not in ['current_N11','offline_candidate_partitions','all500_features','observations','candidate_assignment_refs']},indent=2))
