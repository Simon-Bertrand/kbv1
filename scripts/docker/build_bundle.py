#!/usr/bin/env python3
"""Prepare or import the Docker bases and wheelhouse for KBv1."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .compose_bundle import ROOT, image_archive_name, retire_archives, sha256
from .docker_archive import verify_loaded_image
from .docker_build import environment, local_builder

PREPARATION_DOCKERFILE = ROOT / 'docker/dependencies/Dockerfile'
PREPARED_TARGETS = ('frontend', 'webui-runtime', 'webui', 'km-frontend',
                    'fileserver-base', 'fileserver-build', 'python-runtime',
                    'python-pdf-runtime', 'wheelhouse-preparer')
UPSTREAM = {
    'postgres': 'postgres:17',
    'pgvector': 'ghcr.io/pglayers/pgx-pgvector:17',
    'pgvectorscale': 'ghcr.io/pglayers/pgx-pgvectorscale:17',
    'pg-textsearch': 'ghcr.io/pglayers/pgx-pg_textsearch:17',
}
TAGS = {target: f'knowledgebase/build-{target}:local' for target in PREPARED_TARGETS}
TAGS['wheelhouse-preparer'] = 'knowledgebase/wheelhouse-preparer:local'
TAGS['webui-runtime'] = 'knowledgebase/base-webui:local'
TAGS['fileserver-base'] = 'knowledgebase/base-fileserver:local'
TAGS['fileserver-build'] = 'knowledgebase/build-fileserver:local'
TAGS.update({
    'python-runtime': 'knowledgebase/base-python:local',
    'python-pdf-runtime': 'knowledgebase/base-python-pdf:local',
})
TAGS.update({key: f'knowledgebase/base-{key}:local' for key in UPSTREAM})


def run(args, capture=False):
    return subprocess.run(args, cwd=ROOT, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None, env=environment()).stdout


def prepare_bases(directory=None):
    builder = local_builder(run)
    for target in PREPARED_TARGETS:
        run([*builder, '-f', str(PREPARATION_DOCKERFILE),
             '--target', target, '-t', TAGS[target], '.'])
    for name, source in UPSTREAM.items():
        run(['docker', 'pull', source])
        run(['docker', 'tag', source, TAGS[name]])
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / 'manifest.json'
    previous = {}
    if manifest.exists():
        previous = {item['image']: item for item in json.loads(manifest.read_text())['images']}
    images = []
    for tag in TAGS.values():
        info = json.loads(run(['docker', 'image', 'inspect', tag], True))[0]
        archive = directory / image_archive_name(tag)
        cached = previous.get(tag, {})
        if cached.get('id') == info['Id'] and cached.get('archive') == archive.name and archive.is_file():
            images.append(cached)
            continue
        handle, name = tempfile.mkstemp(dir=directory, suffix='.tar')
        os.close(handle)
        temporary = Path(name)
        try:
            print(f'Exporting {tag} to {archive}', flush=True)
            run(['docker', 'image', 'save', '-o', name, tag])
            item = {'image': tag, 'id': info['Id'], 'archive': archive.name,
                    'platform': f'{info["Os"]}/{info["Architecture"]}', 'sha256': sha256(temporary)}
            temporary.chmod(0o644)
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)
        images.append(item)
    temporary = directory / '.manifest.json.tmp'
    temporary.write_text(json.dumps({'kind': 'build-dependencies', 'version': 1, 'images': images},
                                   indent=2) + '\n')
    os.replace(temporary, manifest)
    (directory / 'SHA256SUMS').write_text(''.join(f'{item["sha256"]}  {item["archive"]}\n'
                                                 for item in images))
    retire_archives(directory, {item['archive'] for item in previous.values()}, images)


def prepare_wheelhouse(directory, profile):
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    tag = TAGS['wheelhouse-preparer']
    run(['docker', 'run', '--rm', '--pull=never', '--user', f'{os.getuid()}:{os.getgid()}',
         '--env', 'HOME=/tmp', '--volume', f'{ROOT}:/workspace:ro',
         '--volume', f'{directory}:/wheelhouse', '--workdir', '/workspace',
         tag, 'python', '/opt/wheelhouse.py', 'prepare', '/wheelhouse', '--pdfparse-profile', profile])


def load(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('kind') != 'build-dependencies' or manifest.get('version') != 1:
        raise SystemExit('Unsupported build bundle.')
    images = {i['image'] for i in manifest['images']}
    expected = {TAGS[name] for name in (*PREPARED_TARGETS, *UPSTREAM)}
    if not expected <= images:
        raise SystemExit('Incomplete build bundle.')
    items = [item for item in manifest['images'] if item['image'] in expected]
    for item in items:
        archive = directory / item['archive']
        if Path(item['archive']).name != item['archive'] or sha256(archive) != item['sha256']:
            raise SystemExit(f'Invalid archive: {archive}')
    for item in items:
        run(['docker', 'load', '-i', str(directory / item['archive'])])
        info = json.loads(run(['docker', 'image', 'inspect', item['image']], True))[0]
        verify_loaded_image(directory / item['archive'], item['image'], info)


def publish(directory, registry):
    load(directory)
    for image in TAGS.values():
        repository = image.split('/')[-1].split(':')[0]
        target = f'{registry}/{repository}:latest'
        run(['docker', 'tag', image, target])
        run(['docker', 'push', target])
    for repository, source in (('ci-buildah', 'quay.io/buildah/stable'),
                               ('ci-helm', 'alpine/helm:3.20.2')):
        target = f'{registry}/{repository}:latest'
        run(['docker', 'pull', source])
        run(['docker', 'tag', source, target])
        run(['docker', 'push', target])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'bases', 'load', 'publish'])
    parser.add_argument('--directory', type=Path, default=ROOT / '.tarball' / 'build')
    parser.add_argument('--wheelhouse', type=Path, default=ROOT / '.wheelhouse')
    parser.add_argument('--pdfparse-profile', choices=('pdfparse', 'pdfparse-full'), default='pdfparse')
    parser.add_argument('--registry')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare_bases(args.directory)
        prepare_wheelhouse(args.wheelhouse, args.pdfparse_profile)
    elif args.action == 'bases':
        prepare_bases()
    elif args.action == 'publish':
        if not args.registry:
            parser.error('publish requires --registry')
        publish(args.directory, args.registry)
    else:
        load(args.directory)


if __name__ == '__main__':
    main()
