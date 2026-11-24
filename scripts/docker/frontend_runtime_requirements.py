#!/usr/bin/env python3
"""Export the KM gateway's hashed dependencies from the frontend package lock, offline."""
import argparse
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCKFILE = ROOT / 'uv.lock'
DESTINATION = ROOT / 'packages/km-frontend/requirements.lock'
DEPENDENCIES = ('fastapi', 'httpx', 'pydantic', 'uvicorn')


def direct_dependencies(pyproject_path=ROOT / 'packages/km-frontend/pyproject.toml'):
    path = Path(pyproject_path)
    if path.is_file():
        data = tomllib.loads(path.read_text())
        deps = data.get('project', {}).get('dependencies', [])
        roots = []
        for d in deps:
            name = d.split('>=')[0].split('<')[0].split('==')[0].split(';')[0].strip()
            if name:
                roots.append(name)
        if roots:
            return tuple(sorted(roots))
    return DEPENDENCIES


def render(lockfile=DEFAULT_LOCKFILE):
    lockfile = Path(lockfile)
    packages = {}
    for package in tomllib.loads(lockfile.read_text())['package']:
        packages.setdefault(package['name'], []).append(package)
    selected = {}
    pending = list(direct_dependencies())
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        variants = packages.get(name, [])
        if len(variants) != 1:
            raise ValueError(f'{name}: expected one locked version, found {len(variants)}')
        package = selected[name] = variants[0]
        pending.extend(item['name'] for item in package.get('dependencies', []))
    lines = ['# Generated offline by scripts.docker.frontend_runtime_requirements.',
             '# Source: uv.lock; package: km-frontend.',
             '# Includes the complete dependency closure; no model weights.', '']
    for name, package in sorted(selected.items()):
        hashes = sorted({item['hash'] for item in package.get('wheels', [])})
        if not hashes or 'registry' not in package['source']:
            raise ValueError(f'{name}: only hashed registry wheels are supported')
        lines.append(f'{name}=={package["version"]} \\')
        lines.extend(f'    --hash={digest}' + (' \\' if index < len(hashes) - 1 else '')
                     for index, digest in enumerate(hashes))
    return '\n'.join(lines) + '\n'


def check():
    if not DESTINATION.exists() or DESTINATION.read_text() != render():
        raise SystemExit('KM frontend runtime lock is stale. Run python -m scripts.docker.frontend_runtime_requirements.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    if parser.parse_args().check:
        check()
    else:
        DESTINATION.write_text(render())
