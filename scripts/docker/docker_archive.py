"""Verify docker-save contents across classic and containerd image stores."""
import hashlib
import json
import tarfile


def image_config(archive, tag):
    # Read metadata only; never extract paths or image layers to disk.
    with tarfile.open(archive, 'r:*') as bundle:
        def read(name):
            stream = bundle.extractfile(name)
            if stream is None:
                raise ValueError(f'Missing archive metadata: {name}')
            with stream:
                return stream.read()

        entries = json.loads(read('manifest.json'))
        matches = [entry for entry in entries if tag in (entry.get('RepoTags') or [])]
        if len(matches) != 1:
            raise ValueError(f'Expected exactly one image tagged {tag} in {archive}')
        raw = read(matches[0]['Config'])
        return 'sha256:' + hashlib.sha256(raw).hexdigest(), json.loads(raw)


def verify_loaded_image(archive, tag, info):
    config_id, config = image_config(archive, tag)
    if info['Id'] == config_id:
        return
    # Some stores expose a manifest/index digest as Id instead of config digest.
    # Compare executable configuration and uncompressed layer identities instead.
    matches = (
        info.get('Os') == config.get('os')
        and info.get('Architecture') == config.get('architecture')
        and info.get('Variant', '') == config.get('variant', '')
        and info.get('Config') == config.get('config')
        and info.get('RootFS', {}).get('Type') == config.get('rootfs', {}).get('type')
        and info.get('RootFS', {}).get('Layers') == config.get('rootfs', {}).get('diff_ids')
    )
    if not matches:
        raise SystemExit(f'Loaded image differs from verified archive: {tag} '
                         f'(loaded {info["Id"]}, archive config {config_id})')
