#!/bin/sh
set -eu

profile=$1
shift
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
export PIP_CONFIG_FILE=/dev/null
unset PIP_EXTRA_INDEX_URL PIP_FIND_LINKS PIP_NO_INDEX
if [ "${PYTHON_INDEX_SOURCE:-public}" = gitea ]; then
    : "${PIP_INDEX_URL:?Configurer PIP_INDEX_URL dans GitLab}"
    : "${GITEA_USER:?Configurer GITEA_USER}"
    : "${GITEA_PAT:?Configurer GITEA_PAT}"
    PIP_INDEX_URL=$(python - <<'PYTHON'
import os
from urllib.parse import quote, urlsplit

url = urlsplit(os.environ['PIP_INDEX_URL'])
if url.scheme != 'https' or not url.hostname or url.username is not None:
    raise SystemExit('PIP_INDEX_URL doit être une URL HTTPS sans identifiants')
user = quote(os.environ['GITEA_USER'], safe='')
token = quote(os.environ['GITEA_PAT'], safe='')
print(url._replace(netloc=f'{user}:{token}@{url.netloc}').geturl())
PYTHON
    )
    [ ! -f /run/secrets/pip_cert ] || export PIP_CERT=/run/secrets/pip_cert
else
    PIP_INDEX_URL=https://pypi.org/simple
    case "$profile" in
        km-encoder|pdfparse|pdfparse-full)
            export PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cu130
            ;;
    esac
    if [ "$profile" = pdfparse-full ]; then
        export PIP_EXTRA_INDEX_URL="$PIP_EXTRA_INDEX_URL https://www.paddlepaddle.org.cn/packages/stable/cu118/"
    fi
    case "$profile" in
        pdfparse|pdfparse-full)
            version=$(sed -n 's/^causal-conv1d==//p' "/opt/pins/$profile.txt")
            test -s /opt/native/pdfparse-native.whl || {
                printf 'Wheel CUDA locale manquante. Lancer make prepare-bases.\n' >&2
                exit 1
            }
            ln -s /opt/native/pdfparse-native.whl "$scratch/causal_conv1d-$version-cp312-cp312-linux_x86_64.whl"
            export PIP_FIND_LINKS="$scratch"
            ;;
    esac
fi
export PIP_INDEX_URL PIP_DISABLE_PIP_VERSION_CHECK=1
python -m venv /opt/venv
pip_install() {
    /opt/venv/bin/python -m pip install --no-cache-dir --no-compile "$@"
}
# Install large wheels individually so pip releases their temporary files
# before downloading the next one. The remaining pins are small enough to batch.
while IFS= read -r pin; do
    case "$pin" in
        '#'*|'') continue ;;
        torch==*|torchvision==*|triton==*|nvidia-*|paddlepaddle-gpu==*|causal-conv1d==*)
            pip_install --only-binary=:all: --no-deps "$pin"
            ;;
        *) printf '%s\n' "$pin" >> "$scratch/requirements.txt" ;;
    esac
done < "/opt/pins/$profile.txt"
pip_install --only-binary=:all: --no-deps -r "$scratch/requirements.txt"
if [ -f "/opt/pins/$profile-private.txt" ]; then
    # Keep the Paddle NCCL and OpenCV files separate from the main environment.
    site=$(/opt/venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
    while IFS= read -r pin; do
        case "$pin" in
            '#'*|'') continue ;;
        esac
        name=${pin%%==*}
        pip_install --only-binary=:all: --no-deps --target "$site/private-$name" "$pin"
        printf 'private-%s\n' "$name" > "$site/$name.pth"
    done < "/opt/pins/$profile-private.txt"
fi
if [ "$#" -gt 0 ]; then
    pip_install --no-index --no-deps --no-build-isolation "$@"
fi
/opt/venv/bin/python -m pip check
