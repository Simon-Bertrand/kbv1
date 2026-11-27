"""Prepare locked wheelhouses, fetch a profile from Gitea, or install it offline."""

import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen
import zipfile

PROFILES = {
    'km-api': ('km-api', ()),
    'km-encoder': ('km-embeddings', ('inference',)),
    'km-indexer': ('km-embeddings', ()),
    'km-frontend-runtime': ('km-frontend', ()),
    'pdfparse': ('pdfparse', ('pipeline-base',)),
    'pdfparse-full': ('pdfparse', ('pipeline-full',)),
}
PRIVATE_WHEELS = ('nvidia-nccl-cu11', 'opencv-python-headless')
NATIVE_PACKAGE = 'causal-conv1d'


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require_platform():
    if (sys.platform, platform.machine(), sys.implementation.name, sys.version_info[:2]) != (
        'linux', 'x86_64', 'cpython', (3, 12)
    ):
        raise SystemExit('The Docker wheelhouse targets Linux x86_64 with CPython 3.12.')


def inputs(profile, root):
    project, _ = PROFILES[profile]
    workspace = tomllib.loads((root / 'pyproject.toml').read_text())['tool']['uv']['workspace']
    projects = {project}
    if project == 'km-embeddings':
        projects.add('km-api')
    paths = ['pyproject.toml', 'uv.lock']
    for member in workspace['members']:
        path = Path(member) / 'pyproject.toml'
        if (root / path).is_file() and tomllib.loads((root / path).read_text())['project']['name'] in projects:
            paths.append(path.as_posix())
    if len(paths) != 2 + len(projects):
        raise SystemExit(f'Missing workspace metadata for {profile}.')
    return {path: sha256(root / path) for path in sorted(paths)}


def manifest_package(profile, checksums):
    name = 'kbv1-wheelhouse-' + profile
    key = hashlib.sha256(json.dumps(checksums, sort_keys=True).encode()).hexdigest()
    return name, '1+' + key


def export(*selection):
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.utils import canonicalize_name

    output = subprocess.check_output(
        ['uv', 'export', '--locked', '--no-emit-workspace', '--no-header',
         '--no-annotate', '--no-hashes', *selection], text=True)
    requirements = [Requirement(line) for line in output.splitlines()
                    if line and not line.startswith(('#', '--'))]
    return {canonicalize_name(req.name): req for req in requirements
            if req.marker is None or req.marker.evaluate()}


def profile_requirements(profile, tools):
    project, extras = PROFILES[profile]
    return tools | export('--package', project, '--no-dev',
                          *(arg for extra in extras for arg in ('--extra', extra)))


def filename(artifact):
    return unquote(Path(urlsplit(artifact['url']).path).name)


def download(artifact, directory):
    path = directory / filename(artifact)
    digest = artifact.get('hash', '')
    if not digest.startswith('sha256:'):
        raise SystemExit(f'Missing SHA-256 in uv.lock for {path.name}.')
    expected = digest.removeprefix('sha256:')
    if path.is_file() and sha256(path) == expected:
        return path
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as file:
        temporary = Path(file.name)
        try:
            with urlopen(artifact['url'], timeout=120) as response:
                shutil.copyfileobj(response, file)
            file.flush()
            if sha256(temporary) != expected:
                raise SystemExit(f'Hash mismatch for {path.name}')
            temporary.chmod(0o644)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def locked_wheel(package, tags):
    from pip._vendor.packaging.utils import parse_wheel_filename

    candidates = []
    for artifact in package.get('wheels', ()):
        wheel_tags = parse_wheel_filename(filename(artifact))[3]
        rank = min((tags[tag] for tag in wheel_tags if tag in tags), default=None)
        if rank is not None:
            candidates.append((rank, filename(artifact), artifact))
    if not candidates:
        raise SystemExit(f"No compatible locked wheel for {package['name']}=={package['version']}")
    return min(candidates, key=lambda item: item[:2])[2]


def entry(path):
    from pip._vendor.packaging.utils import parse_wheel_filename

    name, version, _, _ = parse_wheel_filename(path.name)
    return {'name': str(name), 'version': str(version), 'file': path.name, 'sha256': sha256(path)}


def requirements_file(entries, directory):
    path = directory / 'requirements.txt'
    path.write_text(''.join(f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}\n"
                            for item in entries))
    return path


def pip(*arguments, python=sys.executable, env=None):
    subprocess.run([str(python), '-m', 'pip', '--disable-pip-version-check', '--no-cache-dir', '--no-input',
                    *map(str, arguments)], check=True, env=env)


def install_entries(entries, wheels, python, *, target=None):
    if not entries:
        return
    with tempfile.TemporaryDirectory() as scratch:
        requirements = requirements_file(entries, Path(scratch))
        options = ('--target', target) if target else ()
        pip('install', '--no-index', '--find-links', wheels, '--only-binary=:all:',
            '--no-deps', '--require-hashes', '--no-compile', *options, '-r', requirements, python=python)


def native_version():
    return 'kb.' + sha256(Path('uv.lock'))[:16]


def build_native(package, dependencies, cuda_requirements, resolve, wheels, output):
    key_data = [package, dependencies, {name: str(req) for name, req in cuda_requirements.items()},
                sys.version, platform.libc_ver(),
                subprocess.check_output(['g++', '-dumpfullversion'], text=True).strip(),
                sha256(Path(__file__))]
    key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
    receipt = output / f'.{NATIVE_PACKAGE}-{key}.json'
    if receipt.is_file():
        cached = json.loads(receipt.read_text())
        path = wheels / cached['file']
        if path.is_file() and sha256(path) == cached['sha256']:
            return cached

    with tempfile.TemporaryDirectory(dir=output) as scratch:
        scratch = Path(scratch)
        python = scratch / 'venv/bin/python'
        subprocess.run([sys.executable, '-m', 'venv', str(python.parents[1])], check=True)
        install_entries(dependencies, wheels, python)
        build_wheels = scratch / 'wheels'
        build_wheels.mkdir()
        install_entries([resolve(req, build_wheels) for req in cuda_requirements.values()], build_wheels, python)
        cuda = python.parents[1] / 'lib/python3.12/site-packages/nvidia/cu13'
        (cuda / 'lib/libcudart.so').symlink_to('libcudart.so.13')
        source = download(package['sdist'], scratch)
        env = {**os.environ, 'CUDA_HOME': str(cuda), 'CAUSAL_CONV1D_FORCE_BUILD': 'TRUE',
               'CAUSAL_CONV1D_LOCAL_VERSION': native_version(),
               'MAX_JOBS': os.environ.get('MAX_JOBS', '2'), 'TMPDIR': str(scratch),
               'PATH': f'{python.parent}:{os.environ["PATH"]}'}
        pip('wheel', '--no-index', '--no-deps', '--no-build-isolation', '--wheel-dir', scratch,
            source, python=python, env=env)
        wheel, = scratch.glob('*.whl')
        wheel.replace(wheels / wheel.name)
        result = entry(wheels / wheel.name)
    write_manifest(receipt, result)
    return result


def write_manifest(path, manifest):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write((json.dumps(manifest, indent=2) + '\n').encode())
            stream.flush()
            temporary.chmod(0o644)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def publishable_manifest(wheels, profile, manifest):
    """Transport the profile manifest through the same PyPI index as its wheels."""
    name, version = manifest_package(profile, manifest['inputs'])
    distribution = name.replace('-', '_')
    info = f'{distribution}-{version}.dist-info'
    contents = {
        f'{distribution}/manifest.json': json.dumps(manifest, sort_keys=True).encode(),
        f'{info}/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n'.encode(),
        f'{info}/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    records = []
    for path, data in contents.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        records.append(f'{path},sha256={digest},{len(data)}')
    record = f'{info}/RECORD'
    contents[record] = ('\n'.join([*records, f'{record},,', ''])).encode()
    path = wheels / f'{distribution}-{version}-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for member, data in contents.items():
            archive.writestr(zipfile.ZipInfo(member), data)


def prepare(output, pdfparse_profile, only=None, native_only=False):
    from pip._vendor.packaging.tags import sys_tags
    from pip._vendor.packaging.utils import canonicalize_name

    output.mkdir(parents=True, exist_ok=True)
    wheels = output / 'wheels'
    wheels.mkdir(exist_ok=True)
    tags = {tag: index for index, tag in enumerate(sys_tags())}
    packages = {(package['name'], package['version']): package
                for package in tomllib.loads(Path('uv.lock').read_text())['package'] if 'version' in package}
    resolved = {}

    def find_package(req):
        version, = tuple(req.specifier)
        if version.operator != '==':
            raise SystemExit(f'Expected an exact pin: {req}')
        return packages[canonicalize_name(req.name), version.version]

    def resolve(req, directory=wheels):
        key = (str(req), directory)
        if key not in resolved:
            resolved[key] = entry(download(locked_wheel(find_package(req), tags), directory))
        return resolved[key]

    tools = export('--only-group', 'build')
    profiles = [only] if only else [profile for profile in PROFILES if not profile.startswith('pdfparse')] + [pdfparse_profile]
    selections = {profile: profile_requirements(profile, tools) for profile in profiles}
    for profile, requirements in selections.items():
        print(f'Preparing wheelhouse for {profile}', flush=True)
        dependencies = [] if native_only else [resolve(req) for name, req in requirements.items() if name != NATIVE_PACKAGE]
        if NATIVE_PACKAGE in requirements:
            native_inputs = [resolve(req) for req in profile_requirements('km-encoder', tools).values()]
            native_inputs.extend(resolve(requirements[name]) for name in ('ninja', 'packaging'))
            native_inputs = list({item['name']: item for item in native_inputs}.values())
            cuda = export('--only-group', 'cuda-build')
            dependencies.append(build_native(find_package(requirements[NATIVE_PACKAGE]), native_inputs,
                                             cuda, resolve, wheels, output))

        if native_only:
            context = output / 'native'
            context.mkdir(exist_ok=True)
            link = context / 'pdfparse-native.whl'
            link.unlink(missing_ok=True)
            os.link(wheels / dependencies[0]['file'], link)
            return

        manifest = {'profile': profile, 'inputs': inputs(profile, Path.cwd()),
                    'wheels': sorted(dependencies, key=lambda item: item['name'])}
        publishable_manifest(wheels, profile, manifest)

    for profile in (*PROFILES, 'dbclient'):
        directory = output / profile
        manifest_path = directory / 'manifest.json'
        if directory.is_symlink() or not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text())
        expected = {item['file'] for item in manifest['wheels']} | {'manifest.json'}
        if manifest['profile'] == profile and {path.name for path in directory.iterdir()} <= expected:
            shutil.rmtree(directory)


def stage_profiles(directory, destination):
    wheels = directory / 'wheels'
    for profile in PROFILES:
        name, version = manifest_package(profile, inputs(profile, Path.cwd()))
        package = name.replace('-', '_')
        metadata = wheels / f'{package}-{version}-py3-none-any.whl'
        if not metadata.is_file():
            continue
        manifest = read_manifest_wheel(metadata, name)
        check_manifest(manifest, profile, Path.cwd())

        view = destination / profile
        view.mkdir()
        for item in manifest['wheels']:
            filename = item['file']
            if Path(filename).name != filename or not filename.endswith('.whl'):
                raise SystemExit(f'Invalid wheel filename for {profile}: {filename}')
            source = wheels / filename
            if not source.is_file():
                raise SystemExit(f'Missing wheel for {profile}: {source}')
            os.link(source, view / filename)
        write_manifest(view / 'manifest.json', manifest)


def use(directory, command):
    if not command:
        raise SystemExit('Specify a command after use DIRECTORY.')
    parent = directory if directory.is_dir() else None
    with tempfile.TemporaryDirectory(prefix='.contexts-', dir=parent) as scratch:
        stage_profiles(directory, Path(scratch))
        return subprocess.run(command, env={**os.environ, 'WHEELHOUSE_DIR': scratch}).returncode


def read_manifest_wheel(metadata, name):
    with zipfile.ZipFile(metadata) as archive:
        return json.loads(archive.read(f"{name.replace('-', '_')}/manifest.json"))


def check_manifest(manifest, profile, root):
    if manifest['profile'] != profile or manifest['inputs'] != inputs(profile, root):
        raise SystemExit(f'Incompatible wheelhouse for {profile}. Prepare wheels for this checkout.')


def fetch(profile, output):
    if not os.environ.get('PIP_INDEX_URL'):
        raise SystemExit('Set PIP_INDEX_URL to the internal Gitea PyPI index.')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('PIP_') or key in ('PIP_INDEX_URL', 'PIP_CERT', 'PIP_CLIENT_CERT')}
    env['PIP_CONFIG_FILE'] = os.devnull
    output.mkdir(parents=True, exist_ok=True)
    name, version = manifest_package(profile, inputs(profile, Path.cwd()))
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        options = ('download', '--only-binary=:all:', '--no-deps')
        pip(*options, '--dest', scratch, f'{name}=={version}', env=env)
        metadata, = scratch.glob('*.whl')
        manifest = read_manifest_wheel(metadata, name)
        check_manifest(manifest, profile, Path.cwd())
        requirements = requirements_file(manifest['wheels'], scratch)
        pip(*options, '--require-hashes', '--dest', output, '-r', requirements, env=env)
    write_manifest(output / 'manifest.json', manifest)


def install(profile, directory, target):
    manifest = json.loads((directory / 'manifest.json').read_text())
    selected = manifest['profile']
    if profile == 'pdfparse' and selected == 'pdfparse-full':
        profile = selected
    check_manifest(manifest, profile, Path.cwd())
    subprocess.run([sys.executable, '-m', 'venv', str(target)], check=True)
    python = target / 'bin/python'
    private = PRIVATE_WHEELS if profile == 'pdfparse-full' else ()
    entries = manifest['wheels']
    install_entries([item for item in entries if item['name'] not in private], directory, python)
    if private:
        site = Path(subprocess.check_output(
            [str(python), '-c', "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip())
        for name in private:
            folder = f'private-{name}'
            install_entries([item for item in entries if item['name'] == name], directory, python,
                            target=site / folder)
            (site / f'{name}.pth').write_text(folder + '\n')
    if profile in ('pdfparse', 'pdfparse-full'):
        pip('check', python=python)


def resolve_profile(profile, output):
    if os.environ.get('WHEEL_SOURCE') == 'gitea':
        index = Path('/run/secrets/pip_index_url')
        os.environ['PIP_INDEX_URL'] = index.read_text().strip()
        if Path('/run/secrets/pip_cert').is_file():
            os.environ['PIP_CERT'] = '/run/secrets/pip_cert'
        fetch(profile, output)
        return
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch)
        prepare(archive, profile, only=profile)
        name, version = manifest_package(profile, inputs(profile, Path.cwd()))
        distribution = name.replace('-', '_')
        manifest = read_manifest_wheel(archive / 'wheels' / f'{distribution}-{version}-py3-none-any.whl', name)
        output.mkdir(parents=True, exist_ok=True)
        for item in manifest['wheels']:
            shutil.move(archive / 'wheels' / item['file'], output / item['file'])
        write_manifest(output / 'manifest.json', manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest='action', required=True)
    for action in ('prepare', 'fetch', 'install', 'resolve'):
        command = subcommands.add_parser(action)
        if action == 'prepare':
            command.add_argument('--native-only', action='store_true')
            command.add_argument('--pdfparse-profile', choices=('pdfparse', 'pdfparse-full'), default='pdfparse')
        else:
            command.add_argument('profile', choices=PROFILES)
        command.add_argument('directory', type=Path)
        if action == 'install':
            command.add_argument('target', type=Path)
    staging = subcommands.add_parser('stage')
    staging.add_argument('directory', type=Path)
    staging.add_argument('destination', type=Path)
    local = subcommands.add_parser('use')
    local.add_argument('directory', type=Path)
    local.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action in ('prepare', 'fetch', 'install', 'resolve'):
        require_platform()
    if args.action == 'prepare':
        directory = args.directory.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / '.prepare.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            prepare(directory, args.pdfparse_profile,
                    only=args.pdfparse_profile if args.native_only else None, native_only=args.native_only)
    elif args.action == 'resolve':
        resolve_profile(args.profile, args.directory)
    elif args.action == 'fetch':
        fetch(args.profile, args.directory)
    elif args.action == 'stage':
        stage_profiles(args.directory.resolve(), args.destination.resolve())
    elif args.action == 'use':
        raise SystemExit(use(args.directory.resolve(), args.command))
    else:
        install(args.profile, args.directory, args.target)


if __name__ == '__main__':
    main()
