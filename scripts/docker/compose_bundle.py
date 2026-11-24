#!/usr/bin/env python3
"""Build/export or verify/load Compose images or the catalog of package Dockerfiles."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from .docker_build import webui_build_arguments, check_images, image_references, environment as build_environment, local_builder

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ROOT / 'packages'
LEGACY_KM_IMAGE_TAGS = frozenset({
    'km-agent:1.4.0', 'km-api:1.4.0', 'km-encoder:1.4.0',
    'km-frontend:1.4.0', 'km-indexer:1.4.0',
})


def run(args, *, capture=False):
    result = subprocess.run(args, cwd=ROOT, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None, env=build_environment())
    return result.stdout if capture else None


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def image_archive_name(image):
    repository = image.split('@', 1)[0].rsplit('/', 1)[-1].split(':', 1)[0]
    return f'{repository}.tar'


def retire_archives(directory, previous_archives, images):
    for name in previous_archives - {item['archive'] for item in images}:
        if Path(name).name == name and name.endswith('.tar'):
            (directory / name).unlink(missing_ok=True)


def selected_services(config, names=None):
    services = config['services']
    if names:
        unknown = set(names) - services.keys()
        if unknown:
            raise SystemExit(f'Unknown services: {", ".join(sorted(unknown))}')
        return {name: services[name] for name in names}
    return services


def definition(environment):
    return ROOT / 'tests/docker/packages.json' if environment == 'packages' else ROOT / f'compose.{environment}.yaml'


def configuration(environment):
    if environment == 'packages':
        config = json.loads(definition(environment).read_text())
        for service in config['services'].values():
            service['build']['context'] = str(ROOT / service['build']['context'])
        return config
    return json.loads(run(['docker', 'compose', '-f', str(definition(environment)),
                           'config', '--format', 'json'], capture=True))


def build_plan(services, available=None):
    """Include package producers and order local images before their consumers."""
    merged = {}
    if available:
        for k, v in available.items():
            merged[k] = dict(v)
    for k, v in services.items():
        if k in merged:
            merged[k] = {**merged[k], **v}
            if 'build' not in v and 'build' in available.get(k, {}):
                merged[k]['build'] = available[k]['build']
        else:
            merged[k] = dict(v)
    available = merged
    producers = {service.get('image'): name for name, service in available.items() if service.get('build')}
    visiting, done, planned, result = set(), set(), set(), []

    def visit(name):
        if name in done:
            return
        if name in visiting:
            raise SystemExit(f'Circular image dependency: {name}')
        visiting.add(name)
        service = available[name]
        spec = service.get('build')
        if not spec:
            if service.get('image') in producers:
                producer_name = producers[service['image']]
                if producer_name != name:
                    visit(producer_name)
            visiting.remove(name)
            done.add(name)
            return
        context = Path(spec['context'])
        dockerfile = context / spec.get('dockerfile', 'Dockerfile')
        for dependency in image_references(dockerfile, contexts=spec.get('additional_contexts', {})):
            if dependency in producers:
                visit(producers[dependency])
        visiting.remove(name)
        done.add(name)
        build_key = (service['image'], context, dockerfile)
        if build_key not in planned:
            planned.add(build_key)
            result.append((name, service, context, dockerfile))

    for name in sorted(services):
        visit(name)
    return result


def build(environment, names=None, *, image=None):
    """Use the local image store and prohibit networking in every build step."""
    config = configuration(environment)
    services = selected_services(config, names)
    if image and len(services) != 1:
        raise SystemExit('An image override requires exactly one selected service.')
    available = configuration('packages')['services'] if environment != 'packages' else config['services']
    specifications = build_plan(services, available)
    # Producers in this plan need no stale image in the local store.
    provided = {service['image'] for _, service, _, _ in specifications}
    for _, service, _, dockerfile in specifications:
        contexts = service['build'].get('additional_contexts', {})
        check_images(run, [dockerfile], provided=provided, contexts=contexts)
    builder = local_builder(run)
    for name, service, context, dockerfile in specifications:
        metadata = webui_build_arguments(ROOT) if name == 'webui' else []
        contexts = service['build'].get('additional_contexts', {})
        context_arguments = [arg for name, path in contexts.items()
                             for arg in ('--build-context', f'{name}={path}')]
        run([*builder, '--pull=false', '--network=none', *metadata, *context_arguments,
             '-f', str(dockerfile), '-t', image if image and name in services else service['image'], str(context)])


def export(environment, directory, *, rebuild=True, names=None):
    composefile = definition(environment)
    # Validate includes and environment before starting any expensive operation.
    config = configuration(environment)
    services = selected_services(config, names)
    previous_manifest = directory / 'manifest.json'
    previous_archives = set()
    retained = []
    if previous_manifest.exists():
        previous = json.loads(previous_manifest.read_text())
        if previous.get('environment') == environment:
            previous_archives = {item['archive'] for item in previous.get('images', [])}
            if names:
                retained = [item for item in previous.get('images', []) if item['service'] not in services]
        elif names:
            raise SystemExit('Bundle environment does not match the requested environment.')
    archive_images = {item['archive']: item['image'] for item in retained}
    for name, service in services.items():
        if not service.get('image'):
            raise SystemExit(f'{name}: an explicit image tag is required for offline use.')
        tag = service['image']
        archive_name = image_archive_name(tag)
        if archive_images.setdefault(archive_name, tag) != tag:
            raise SystemExit(f'Multiple image references use archive {archive_name}.')
    if rebuild:
        if names:
            build(environment, names)
        else:
            build(environment)
    directory.mkdir(parents=True, exist_ok=True)
    images = list(retained)
    saved_images = {}
    for name, service in sorted(services.items()):
        tag = service['image']
        info = json.loads(run(['docker', 'image', 'inspect', tag], capture=True))[0]
        if tag in saved_images:
            archive = saved_images[tag]['archive']
            archive_sha256 = saved_images[tag]['sha256']
        else:
            archive = directory / image_archive_name(tag)
            print(f'Exporting {tag} -> {archive}', flush=True)
            handle, temporary = tempfile.mkstemp(prefix=f'.{name}-', suffix='.tar', dir=directory)
            os.close(handle)
            try:
                run(['docker', 'image', 'save', '--output', temporary, tag])
                os.chmod(temporary, 0o644)
                os.replace(temporary, archive)
            finally:
                Path(temporary).unlink(missing_ok=True)
            archive_sha256 = sha256(archive)
            saved_images[tag] = {'archive': archive, 'sha256': archive_sha256, 'id': info['Id'],
                                 'platform': f'{info["Os"]}/{info["Architecture"]}'}
        images.append({'service': name, 'image': tag, 'id': info['Id'],
                       'platform': f'{info["Os"]}/{info["Architecture"]}',
                       'archive': archive.name, 'sha256': archive_sha256})
    for item in retained:
        saved = saved_images.get(item['image'])
        if saved and saved['archive'].name == item['archive']:
            item.update(id=saved['id'], platform=saved['platform'], sha256=saved['sha256'])
    images.sort(key=lambda item: item['service'])
    manifest = {'environment': environment, 'compose': composefile.name, 'images': images}
    temporary_manifest = directory / '.manifest.json.tmp'
    temporary_manifest.write_text(json.dumps(manifest, indent=2) + '\n')
    os.replace(temporary_manifest, directory / 'manifest.json')
    (directory / 'SHA256SUMS').write_text(''.join(f'{item["sha256"]}  {item["archive"]}\n' for item in images))
    retire_archives(directory, previous_archives, images)
    print(f'Bundle ready: {directory} ({len(images)} images). No containers were started.')


def load(environment, directory, names=None):
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest['environment'] != environment:
        raise SystemExit('Bundle environment does not match the requested environment.')
    entries = manifest['images']
    by_service = {item['service']: item for item in entries}
    if len(by_service) != len(entries):
        raise SystemExit('Bundle contains duplicate service entries.')
    if not by_service:
        raise SystemExit('Bundle contains no images.')
    services = selected_services(configuration(environment), names)
    missing = [name for name in services if name not in by_service]
    if missing:
        raise SystemExit(f'Incomplete {environment} bundle. Missing services: {", ".join(sorted(missing))}')
    images = [by_service[name] for name in services]
    retags = {}
    # Check the entire bundle before loading its first image.
    for item in images:
        image = item['image']
        expected = services[item['service']].get('image')
        if image != expected:
            if image not in LEGACY_KM_IMAGE_TAGS or expected != f'knowledgebase/{image}':
                raise SystemExit(f'Unexpected image for {item["service"]}: {image}')
            retags[expected] = item['id']
        archive = directory / item['archive']
        if archive.parent != directory or not archive.is_file() or sha256(archive) != item['sha256']:
            raise SystemExit(f'Invalid archive: {archive}')
    loaded_archives = set()
    for item in images:
        archive = directory / item['archive']
        if archive.name not in loaded_archives:
            run(['docker', 'image', 'load', '--input', str(archive)])
            loaded_archives.add(archive.name)
        info = json.loads(run(['docker', 'image', 'inspect', item['image']], capture=True))[0]
        if info['Id'] != item['id']:
            raise SystemExit(f'Loaded image ID mismatch: {item["image"]}')
    for tag, image_id in retags.items():
        run(['docker', 'image', 'tag', image_id, tag])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['export', 'load', 'build'])
    parser.add_argument('environment', choices=['dev', 'prod', 'packages'])
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--no-build', action='store_true', help='Export already-built local images.')
    parser.add_argument('--services', nargs='+', help='Build/export only these services, preserving other bundle entries.')
    parser.add_argument('--image', help='Override the output image for a single-service build.')
    args = parser.parse_args()
    if args.image and args.action != 'build':
        parser.error('--image is supported only with build')
    directory = (args.directory or ROOT / '.tarball' / args.environment).resolve()
    if args.action == 'build':
        build(args.environment, args.services, image=args.image)
    else:
        if args.action == 'export':
            export(args.environment, directory, rebuild=not args.no_build, names=args.services)
        else:
            load(args.environment, directory, args.services)


if __name__ == '__main__':
    main()
