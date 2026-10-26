"""Explicit, counts-only prediction variants; never part of the evaluator.

This proves syntax and byte preservation, not the model's intended semantics.
Ambiguous file boundaries, incomplete structure, and missing terminal LF fail
closed. Starts, context, file headers, and every code byte remain untouched.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from batch_patch import sections
from core import validate_predictions

HEADER=re.compile(rb'^(@@ -)(\d+)(,\d+)?( \+)(\d+)(,\d+)?( @@.*)$')
PROBE=b'--- /dev/null\n+++ b/__hunk_count_probe__.txt\n@@ -0,0 +1 @@\n+probe\n'


def sha(raw):return hashlib.sha256(raw).hexdigest()


def syntax(raw):
    result=subprocess.run(['git','apply','--numstat','-z'],input=raw,capture_output=True,timeout=30)
    return result.returncode==0,result.stderr.decode(errors='replace').strip()


def logical(line):return line.removesuffix(b'\r')


def signature(raw):
    """Retain everything except the two count fields in recognized headers."""
    result=[]
    for line in raw.split(b'\n'):
        match=HEADER.fullmatch(logical(line))
        if match:
            result.append((match[1],match[2],match[4],match[5],match[7],line.endswith(b'\r')))
        else:result.append(line)
    return result


def verify_header_only(before,after):
    if signature(before)!=signature(after):raise ValueError('Bytes outside hunk counts changed')
    return True


def recount(raw):
    if not raw.endswith(b'\n'):raise ValueError('Missing terminal LF; adding it is outside counts-only repair')
    lines=raw.split(b'\n')[:-1]
    git=raw.startswith(b'diff --git ')
    if not git and not raw.startswith(b'--- '):raise ValueError('Unsupported patch format')
    boundaries=[i for i,line in enumerate(lines) if line.startswith(b'diff --git ')] if git else [0]
    boundaries.append(len(lines));edits=[]
    for start,end in zip(boundaries,boundaries[1:]):
        index=start+1 if git else start
        if git:
            while index<end and not lines[index].startswith(b'--- '):
                if not re.fullmatch(rb'(?:index [0-9a-f]+\.\.[0-9a-f]+(?: [0-7]{6})?|(?:new file mode|deleted file mode) [0-7]{6})',logical(lines[index])):
                    raise ValueError('Unsupported Git metadata')
                index+=1
        if index+2>=end or not lines[index].startswith(b'--- ') or not lines[index+1].startswith(b'+++ '):
            raise ValueError('Missing complete file header and hunk')
        index+=2;previous_old_end=previous_new_end=-1
        while index<end:
            match=HEADER.fullmatch(logical(lines[index]))
            if match is None:raise ValueError('Expected a complete hunk header')
            header_index=index;index+=1;old=new=changes=body=0;marker_allowed=False
            while index<end and not lines[index].startswith(b'@@'):
                line=logical(lines[index])
                if line.startswith(b'--- ') and index+1<end and lines[index+1].startswith(b'+++ '):
                    raise ValueError('Ambiguous plain file-header pair inside a hunk')
                if line==b'\\ No newline at end of file':
                    if not marker_allowed:raise ValueError('Orphan or repeated no-newline marker')
                    marker_allowed=False;index+=1;continue
                if not line or line[:1] not in [b' ',b'-',b'+']:
                    raise ValueError('Unprefixed, incomplete, or trailing hunk content')
                old+=line[:1] in [b' ',b'-'];new+=line[:1] in [b' ',b'+']
                changes+=line[:1] in [b'-',b'+'];body+=1;marker_allowed=True;index+=1
            if not body or not changes:raise ValueError('Empty hunk or hunk without changes')
            old_start,new_start=int(match[2]),int(match[5])
            if (old and old_start==0) or (new and new_start==0):raise ValueError('Nonempty range starts at zero')
            if old_start<previous_old_end or new_start<previous_new_end:raise ValueError('Overlapping or reversed hunks')
            previous_old_end=old_start+old;previous_new_end=new_start+new
            def count(token,actual):
                declared=int(token[1:]) if token else 1
                return (token or b'') if declared==actual else b','+str(actual).encode()
            replacement=(match[1]+match[2]+count(match[3],old)+match[4]+match[5]+count(match[6],new)+match[7]
                         +(b'\r' if lines[header_index].endswith(b'\r') else b''))
            if replacement!=lines[header_index]:
                edits.append(dict(line=header_index+1,before=lines[header_index].decode(),after=replacement.decode(),
                                  old_start=old_start,new_start=new_start,old_count=old,new_count=new))
                lines[header_index]=replacement
    if not edits:raise ValueError('No incorrect count fields found')
    result=b'\n'.join(lines)+b'\n';verify_header_only(raw,result)
    ok,error=syntax(result)
    if not ok:raise ValueError('Git still rejects the counts-only variant: '+error)
    sections(result)  # Same path/structure policy used by the unchanged batch evaluator.
    return result,edits


def repair(raw):
    ok,error=syntax(raw)
    if ok:return raw,dict(status='unchanged_git_syntax_valid',changes=[])
    try:
        after,changes=recount(raw)
        return after,dict(status='recounted',changes=changes,original_git_error=error,
                         source_patch_sha256=sha(raw),variant_patch_sha256=sha(after),
                         code_context_paths_and_start_positions_unchanged=True)
    except ValueError as exc:
        return raw,dict(status='unrepairable_counts_only',reason=str(exc),original_git_error=error,changes=[])


def create_variant(source,target,manifest,eligible_ids=None):
    source,target,manifest=map(Path,[source,target,manifest])
    if target.exists() or manifest.exists() or target.resolve()==manifest.resolve():raise ValueError('New distinct output paths required')
    if not syntax(PROBE)[0]:raise RuntimeError('Git syntax canary failed; refusing to classify predictions')
    original=source.read_bytes();rows=[json.loads(line) for line in original.decode('utf-8-sig').splitlines()]
    validate_predictions(rows)
    eligible=set(eligible_ids) if eligible_ids is not None else {r['instance_id'] for r in rows}
    if eligible-{r['instance_id'] for r in rows}:raise ValueError('Eligible ID is absent from source')
    cases=[]
    for row in rows:
        before=row['model_patch'].encode();iid=row['instance_id']
        if not before.strip():after,detail=before,dict(status='unchanged_empty',changes=[])
        elif iid not in eligible:after,detail=before,dict(status='unchanged_outside_eligible_scope',changes=[])
        else:after,detail=repair(before)
        verify_header_only(before,after)
        cases.append(dict(instance_id=iid,**detail));row['model_patch']=after.decode()
    output=''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows).encode()
    original_rows=[json.loads(line) for line in original.decode('utf-8-sig').splitlines()]
    for old,new in zip(original_rows,rows):
        if {k:v for k,v in old.items() if k!='model_patch'}!={k:v for k,v in new.items() if k!='model_patch'}:
            raise ValueError('Prediction metadata changed')
    if source.read_bytes()!=original:raise ValueError('Source changed during preparation')
    record=dict(operation='recount hunk count fields only; preserve every other patch byte',
        source_sha256=sha(original),variant_sha256=sha(output),rows=len(rows),eligible_count=len(eligible),
        changed_patch_count=sum(c['status']=='recounted' for c in cases),cases=cases,
        original_file_unchanged=True,code_or_official_tests_modified=False,
        changed_variants_git_syntax_verified=True,intended_semantic_completeness_proven=False,
        automatic_repair_in_evaluator=False)
    target.parent.mkdir(parents=True,exist_ok=True);manifest.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as stream:stream.write(output)
    with manifest.open('x',encoding='utf-8') as stream:stream.write(json.dumps(record,indent=2)+'\n')
    return record


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['source','target','manifest']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--eligible-ids',type=Path)
    a=p.parse_args();ids=json.loads(a.eligible_ids.read_text(encoding='utf-8')) if a.eligible_ids else None
    result=create_variant(a.source,a.target,a.manifest,ids)
    print(json.dumps({k:v for k,v in result.items() if k!='cases'}))
