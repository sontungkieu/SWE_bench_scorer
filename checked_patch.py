"""Versioned forward patch application; never edits a prediction or test script.

Run inside a fresh instance container. Align touched files to the dataset base,
apply the original patch, then three-way merge the image's setup changes back.
Conflicts fail closed. Incomplete binary input is retained as an explicit error,
even when GNU patch's upstream-compatible fallback accepts the text changes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import tempfile

VERSION = 'checked-forward-v1'


def command(args, cwd, input_bytes=None, data=False):
    p = subprocess.run(args, cwd=cwd, input=input_bytes, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=90)
    if data and p.returncode == 0 and p.stderr:
        raise ValueError('Unexpected stderr while reading source/merge content')
    return p.returncode, p.stdout if data and not p.returncode else p.stdout + p.stderr


def sections(patch):
    blocks = re.split(rb'(?=^diff --git )', patch, flags=re.M)
    result = []
    for block in blocks:
        if not block.startswith(b'diff --git '):
            if block.strip():
                raise ValueError('Only explicit git diff sections are supported')
            continue
        header = shlex.split(block.splitlines()[0].decode('utf-8'))
        if len(header) != 4 or not header[2].startswith('a/') or not header[3].startswith('b/'):
            raise ValueError('Unsupported diff path encoding')
        paths = list(dict.fromkeys([header[2][2:], header[3][2:]]))
        for name in paths:
            p = PurePosixPath(name)
            if (p.is_absolute() or '..' in p.parts or '.git' in p.parts or
                    '\\' in name or '\x00' in name or not p.parts):
                raise ValueError('Unsafe prediction path')
        if re.search(rb'^(?:new file mode|old mode|new mode|deleted file mode) (?:120000|160000)', block, re.M):
            raise ValueError('Symlink/submodule changes require a separate qualified applicator')
        binary = bool(re.search(rb'^(?:Binary files .* differ|GIT binary patch)$', block, re.M))
        result.append(dict(paths=paths, block=block, binary=binary))
    if not result:
        raise ValueError('No git diff sections')
    return result


def safe_path(repo, name):
    path = repo / name
    if path.is_symlink() or repo not in path.resolve().parents:
        raise ValueError('Prediction traverses a symlink or repository boundary')
    if path.exists() and not path.is_file():
        raise ValueError('Prediction target is not a regular file')
    return path


def read_state(path):
    return (path.read_bytes(), path.stat().st_mode & 0o777) if path.is_file() else (None, None)


def write_state(path, state):
    data, mode = state
    if data is None:
        if path.exists():
            path.unlink()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def merge_state(model, base, image, cwd, scratch):
    m, b, x = model[0], base[0], image[0]
    if m == b:
        return image
    if x == b or x == m:
        content = m
    elif m is None or b is None or x is None:
        raise ValueError('Conflicting file creation/deletion in image and prediction')
    else:
        if any(b'\0' in v for v in (m, b, x)):
            raise ValueError('Conflicting binary image setup change')
        files = [scratch / n for n in ('model', 'base', 'image')]
        for path, data in zip(files, (m, b, x)):
            path.write_bytes(data)
        rc, content = command(['git', 'merge-file', '-p', *map(str, files)], cwd, data=True)
        if rc:
            raise ValueError('Prediction conflicts with image setup; no side selected')
    if content is None:
        return None, None
    if model[1] != base[1] and image[1] != base[1] and model[1] != image[1]:
        raise ValueError('Conflicting file modes')
    return content, model[1] if model[1] != base[1] else image[1] or model[1]


def apply(repo, patch_path, base_commit):
    repo = Path(repo).resolve()
    patch_path = Path(patch_path).resolve()
    raw = patch_path.read_bytes()
    audit = dict(pipeline=VERSION, prediction_sha256=digest(raw), base_commit=base_commit,
                 steps=[], paths={}, materialization_errors=[], applied=False)
    original = {}
    try:
        if not re.fullmatch(r'[0-9a-f]{40}', base_commit):
            raise ValueError('An immutable dataset base commit is required')
        parts = sections(raw)
        names = sorted({n for part in parts for n in part['paths']})
        paths = {n: safe_path(repo, n) for n in names}
        original = {n: read_state(p) for n, p in paths.items()}
        rc, tree = command(['git', 'ls-tree', '-z', base_commit, '--', *names], repo, data=True)
        if rc:
            raise ValueError('Dataset base commit is unavailable')
        base = {n: (None, None) for n in names}
        for entry in tree.split(b'\0'):
            if not entry:
                continue
            meta, name = entry.split(b'\t', 1)
            mode, kind, obj = meta.decode().split()
            if kind != 'blob' or mode not in ('100644', '100755'):
                raise ValueError('Nonregular dataset base target')
            rc, blob = command(['git', 'cat-file', 'blob', obj], repo, data=True)
            if rc:
                raise ValueError('Dataset base blob is unavailable')
            base[name.decode()] = blob, int(mode[-3:], 8)
        with tempfile.TemporaryDirectory(prefix='checked-patch-') as temp:
            scratch = Path(temp)
            for attempt, args in enumerate((['git', 'apply', '--verbose', str(patch_path)],
                    ['patch', '--batch', '--forward', '--fuzz=5', '-p1', '-i', str(patch_path)])):
                # A failed attempt must never become the next attempt's input.
                for name in names:
                    write_state(paths[name], base[name])
                if attempt and any(re.search(rb'^(?:rename|copy) (?:from|to) ', p['block'], re.M) for p in parts):
                    raise ValueError('GNU fallback cannot verify rename/copy semantics')
                rc, output = command(args, repo)
                audit['steps'].append(dict(command=args[:-1] + ['<original-patch>'],
                                           exit_code=rc, output=output.decode(errors='replace')))
                if rc == 0:
                    break
            else:
                raise ValueError('All forward application methods failed')
            model = {n: read_state(paths[n]) for n in names}
            for name in names:
                merged = merge_state(model[name], base[name], original[name], repo, scratch)
                write_state(paths[name], merged)
                audit['paths'][name] = dict(base_sha256=digest(base[name][0]),
                    image_sha256=digest(original[name][0]), model_sha256=digest(model[name][0]),
                    final_sha256=digest(merged[0]))
            for number, part in enumerate(parts):
                section = scratch / ('section-' + str(number) + '.diff')
                section.write_bytes(part['block'])
                rc, output = command(['git', 'apply', '--reverse', '--check', str(section)], repo)
                if rc:
                    if part['binary']:
                        audit['materialization_errors'].append(dict(paths=part['paths'],
                            reason='Binary section could not be verified; no data synthesized',
                            check_output=output.decode(errors='replace')))
                    else:
                        raise ValueError('Forward postcondition failed for ' + ', '.join(part['paths']) + ': ' + output.decode(errors='replace'))
            audit['applied'] = True
            audit['prediction_fully_materialized'] = not audit['materialization_errors']
    except Exception as exc:
        for name, state in original.items():
            write_state(safe_path(repo, name), state)
        audit['error'] = type(exc).__name__ + ': ' + str(exc)
    return audit


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', default='/testbed')
    parser.add_argument('--patch', required=True)
    parser.add_argument('--base-commit', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = apply(args.repo, args.patch, args.base_commit)
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(pipeline=VERSION, applied=result['applied'], error=result.get('error'),
                         materialization_errors=len(result['materialization_errors']))))
    raise SystemExit(0 if result['applied'] else 1)
