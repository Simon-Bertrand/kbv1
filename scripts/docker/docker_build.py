"""Use the current daemon's built-in builder and its loaded images."""
import os
from pathlib import Path
import re
import subprocess


def local_builder(run):
    context = run(['docker', 'context', 'show'], capture=True).strip()
    if not context:
        raise SystemExit('Docker did not return its active context.')
    command = ['docker', '--context', context]
    details = run([*command, 'buildx', 'inspect', context], capture=True)
    error = re.search(r'^Error:[ \t]*(\S[^\r\n]*)', details, re.MULTILINE)
    if error:
        raise SystemExit(f'{context}: {error[1]}')
    match = re.search(r'^Driver:[ \t]*(\S+)', details, re.MULTILINE)
    driver = match[1] if match else None
    if driver != 'docker':
        raise SystemExit(f'{context}: the offline builder must use the built-in docker driver, got {driver!r}.')
    return [*command, 'build', '--builder', context]


def environment():
    # A selected container/cloud builder cannot see images restored by docker load.
    env = dict(os.environ, DOCKER_BUILDKIT='1')
    env.pop('BUILDX_BUILDER', None)
    return env


def webui_build_arguments(directory):
    """Record Git metadata before Docker hides the checkout from the build context."""
    args = []
    for variable, command in (
        ('WEBUI_GIT_COMMIT', ('rev-parse', 'HEAD')),
        ('WEBUI_GIT_BRANCH', ('symbolic-ref', '--quiet', '--short', 'HEAD')),
    ):
        result = subprocess.run(['git', *command], cwd=directory, capture_output=True, text=True)
        value = result.stdout.strip()
        if result.returncode == 0 and value:
            args.extend(('--build-arg', f'{variable}={value}'))
    return args


def image_references(dockerfile, *, contexts=()):
    """Find every external FROM/COPY image; stages are local to the Dockerfile."""
    stages, images = set(), set()
    for line in Path(dockerfile).read_text().splitlines():
        parts = line.strip().split()
        if not parts or parts[0].startswith('#'):
            continue
        if parts[0].upper() == 'FROM':
            image = parts[1].replace('${BUILD_IMAGE_PREFIX}', 'knowledgebase')
            if image not in stages and image != 'scratch':
                images.add(image)
            if len(parts) == 4 and parts[2].upper() == 'AS':
                stages.add(parts[3])
        elif parts[0].upper() == 'COPY':
            match = re.search(r'--from=([^\s]+)', line)
            if match and match[1] not in stages and match[1] not in contexts and not match[1].isdigit():
                images.add(match[1])
    if any(not image.startswith('knowledgebase/') for image in images):
        raise SystemExit(f'{dockerfile}: offline Dockerfiles must reference prepared knowledgebase/ images only.')
    return sorted(images)


def check_images(run, dockerfiles, *, provided=(), contexts=()):
    images = {image for path in dockerfiles for image in image_references(path, contexts=contexts)}
    for image in sorted(images - set(provided)):
        try:
            run(['docker', 'image', 'inspect', image], capture=True)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f'Missing local image {image}. Run make import-build (offline) or make prepare-build (online).') from exc
