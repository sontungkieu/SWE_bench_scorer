"""Batch-only standard unified-diff adapter. Prediction bytes are never repaired.

Git's read-only numstat parser determines the affected paths; original headers
are cross-checked before the existing checked applicator touches any file.
"""
import argparse
import ast
import json
from pathlib import Path
import re
try:
    import checked_patch_base as base  # packaged under /tmp inside the container
except ModuleNotFoundError as exc:
    if exc.name!='checked_patch_base':raise
    import checked_patch as base

ADAPTER='batch-unified-v1'


def quoted(value):
    result=b'"'
    for n in value.encode('utf-8'):
        if n in [34,92]:result+=b'\\'+bytes([n])
        elif n<32 or n>=127:result+=('\\%03o'%n).encode()
        else:result+=bytes([n])
    return result+b'"'


def header_path(token,prefix):
    token=token.split(b'\t',1)[0]
    if token.startswith(b'"'):token=ast.literal_eval('b'+token.decode('ascii'))
    text=token.decode('utf-8')
    if text=='/dev/null':return None
    if not text.startswith(prefix+'/'):raise ValueError('Unified paths must use a/ and b/ prefixes')
    return text[2:]


def declared_paths(raw):
    lines=raw.splitlines();paths=[];index=0;old_left=new_left=0
    while index<len(lines):
        line=lines[index]
        if old_left or new_left:
            if line.startswith(b'\\ No newline'):index+=1;continue
            if not line or line.startswith(b' '):old_left-=1;new_left-=1
            elif line.startswith(b'-'):old_left-=1
            elif line.startswith(b'+'):new_left-=1
            else:raise ValueError('Unexpected unified hunk body')
            if min(old_left,new_left)<0:raise ValueError('Invalid unified hunk counters')
        elif line.startswith(b'@@ '):
            hunk=re.match(rb'^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@',line)
            if hunk is None:raise ValueError('Invalid unified hunk header')
            old_left=int(hunk[1]) if hunk[1] is not None else 1
            new_left=int(hunk[2]) if hunk[2] is not None else 1
        elif line.startswith(b'--- '):
            if index+1>=len(lines) or not lines[index+1].startswith(b'+++ '):raise ValueError('Missing unified new-file header')
            old,new=header_path(line[4:],'a'),header_path(lines[index+1][4:],'b')
            if old is None and new is None:raise ValueError('Unified diff has no file target')
            if old is not None and new is not None and old!=new:raise ValueError('Plain unified renames are unsupported')
            paths.append(new if new is not None else old);index+=1
        index+=1
    if old_left or new_left:raise ValueError('Incomplete unified hunk')
    return paths


def sections(raw):
    if raw.startswith(b'diff --git '):return base.sections(raw)
    if not raw.startswith(b'--- '):raise ValueError('Expected a git diff or standard unified diff')
    rc,output=base.command(['git','apply','--numstat','-z'],Path.cwd(),input_bytes=raw,data=True)
    if rc:raise ValueError('Unified diff failed Git syntax validation; raw bytes retained')
    names=[]
    for row in output.split(b'\0'):
        if not row:continue
        fields=row.split(b'\t',2)
        if len(fields)!=3 or not all(n.isdigit() for n in fields[:2]) or not fields[2]:
            raise ValueError('Unsupported unified binary/rename metadata')
        name=fields[2].decode('utf-8')
        # Reuse the original path boundary/symlink/submodule policy. This header
        # is only parsed as metadata; it is never prepended to the prediction.
        base.sections(b'diff --git '+quoted('a/'+name)+b' '+quoted('b/'+name)+b'\n')
        names.append(name)
    if not names or len(names)!=len(set(names)):raise ValueError('Empty/repeated unified target paths')
    declared=declared_paths(raw)
    if len(declared)!=len(names) or set(declared)!=set(names):
        raise ValueError('Unified headers do not match Git target metadata')
    return [dict(paths=names,block=raw,binary=False)]


def apply(repo,patch_path,base_commit):
    result=base.apply(repo,patch_path,base_commit,section_parser=sections)
    result['format_adapter']=ADAPTER
    result['prediction_bytes_modified']=False
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',default='/testbed');p.add_argument('--patch',required=True)
    p.add_argument('--base-commit',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    result=apply(a.repo,a.patch,a.base_commit)
    Path(a.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(applied=result['applied'],adapter=ADAPTER,error=result.get('error'))))
    raise SystemExit(0 if result['applied'] else 1)
