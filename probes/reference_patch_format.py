"""Explicit terminal-LF variant for archived reference predictions; never edits input."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from core import validate_predictions


def append_terminal_lf(patch):
    return patch+'\n' if patch.strip() and not patch.endswith('\n') else patch


def create_variant(source,target,manifest):
    if target.exists() or manifest.exists():raise ValueError('New variant and manifest paths required')
    original=source.read_bytes();rows=[json.loads(line) for line in original.decode('utf-8-sig').splitlines()]
    validate_predictions(rows);changes=[]
    for row in rows:
        before=row['model_patch'];after=append_terminal_lf(before)
        if before!=after:
            assert after==before+'\n'
            changes.append(dict(instance_id=row['instance_id'],appended_bytes=1,
                source_patch_sha256=hashlib.sha256(before.encode()).hexdigest(),
                variant_patch_sha256=hashlib.sha256(after.encode()).hexdigest()))
            row['model_patch']=after
    output=(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows)).encode()
    target.parent.mkdir(parents=True,exist_ok=True);manifest.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(output)
    record=dict(operation='append exactly one terminal LF to nonempty patches missing it',
        source_sha256=hashlib.sha256(original).hexdigest(),variant_sha256=hashlib.sha256(output).hexdigest(),
        rows=len(rows),changed_patch_count=len(changes),changes=changes,
        original_file_unchanged=source.read_bytes()==original,official_tests_or_harness_modified=False,
        hunk_headers_or_code_lines_modified=False,paid_rerun_authorized_by_this_file=False)
    manifest.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    return record


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['source','target','manifest']:p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();r=create_variant(a.source,a.target,a.manifest)
    print(json.dumps({k:v for k,v in r.items() if k!='changes'}))
