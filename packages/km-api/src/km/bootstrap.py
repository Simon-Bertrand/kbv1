"""Register the current encoder profile after the indexer becomes ready."""
import json
import os
import time
import urllib.error
import urllib.request
from uuid import uuid4

from ._service_logging import current_request_id, get_request_id, setup_logging, shutdown_logging, upstream_headers


def main():
    log = setup_logging('embedding-bootstrap')
    context = current_request_id.set(get_request_id() or uuid4().hex)
    try:
        return register_profile(log)
    finally:
        shutdown_logging()
        current_request_id.reset(context)


def register_profile(log):
    url = os.environ['KM_INDEXER_URL'].rstrip('/') + '/profiles/register'
    token = os.environ.get('KM_INDEXER_TOKEN', '').strip()
    headers = upstream_headers()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, data=b'', headers=headers, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 90
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            with opener.open(request, timeout=min(10, remaining)) as response:
                profile = json.load(response)
            if not profile.get('profile') or profile.get('dimension') != 1024:
                log.error('Incompatible embedding profile')
                return 1
            log.info('Embedding profile registered: %s', profile['profile'])
            return 0
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 502, 503, 504):
                log.error('Profile registration failed (HTTP %s)', exc.code)
                return 1
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(max(0, min(2, deadline - time.monotonic())))
    log.error('Embedding profile registration timed out')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
