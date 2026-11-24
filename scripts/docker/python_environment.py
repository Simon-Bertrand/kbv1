"""Build Python environments from the checked workspace lock and a shared uv cache."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

# These wheels share files with different distributions. Keep their official
# RECORDs intact in private directories and put the primary packages first.
PRIVATE_WHEELS = ("nvidia-nccl-cu11", "opencv-python-headless")
PROFILES = {
    "km-api": ("km-api", ()),
    "km-agent": ("km-agent", ()),
    "km-encoder": ("km-embeddings", ("inference",)),
    "km-indexer": ("km-embeddings", ()),
    "km-frontend-runtime": ("km-frontend", ()),
    "ingestion": ("rag-ingestion", ()),
    "dfctune": ("dfctune", ()),
    "embedtune": ("embedtune", ()),
    "reranker": ("km-reranker", ("inference",)),
    "pdfparse": ("pdfparse", ("studio", "vlm", "quantization", "ocr-rapid", "cuda-kernels", "layout-lite", "ocr-paddle")),
}


def uv(*args, env=None, capture=False):
    return subprocess.run(["uv", *map(str, args)], env=env, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def export(selection):
    return uv("export", "--locked", "--no-emit-workspace", "--no-header", "--no-annotate",
              *selection, capture=True)


def install_requirements(requirements, venv, *, target=None):
    if not requirements.strip():
        return
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "requirements.txt"
        path.write_text(requirements)
        options = ("--target", target) if target else ()
        uv("--no-config", "pip", "install", "--python", venv / "bin/python",
           "--no-deps", "--require-hashes", *options, "-r", path)


def locked_version_check():
    lock = tomllib.loads(Path("uv.lock").read_text())
    versions = {}
    for package in lock["package"]:
        seen = versions.setdefault(package["name"], set())
        seen.add(package["version"])
        if len(seen) != 1:
            raise SystemExit(f"{package['name']} has multiple locked versions: {sorted(seen)}")


def sync(venv, selection, *, private=False, workspace=False):
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(venv)}
    separate = PRIVATE_WHEELS if private else ()
    omit = tuple(value for name in separate for value in ("--no-install-package", name))
    local = () if workspace else ("--no-install-workspace",)
    keep_build_tools = ()
    if "--package" in selection:
        uv("sync", "--locked", "--python", sys.executable, "--only-group", "build", env=env)
        keep_build_tools = ("--inexact",)
    uv("sync", "--locked", "--python", sys.executable, *local, *selection, *omit, *keep_build_tools, env=env)
    if separate:
        locked = export(selection).replace(chr(92) + "\n", " ")
        site = Path(subprocess.check_output(
            [venv / "bin/python", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True).strip())
        for name in separate:
            requirement, = (line for line in locked.splitlines() if line.startswith(name + "=="))
            folder = f"private-{name}"
            install_requirements(requirement + "\n", venv, target=site / folder)
            (site / f"{name}.pth").write_text(folder + "\n")
    subprocess.run([venv / "bin/python", "-m", "pip", "check"], check=True)


def copy_cuda_wheel(source, destination, canonical_cache):
    """Copy uv's compiled sdist and the archive entries referenced by its symlinks."""
    relative = Path("sdists-v9/pypi/causal-conv1d")
    tree = source / relative
    if not tree.is_dir():
        return
    for link in tree.rglob("*"):
        if link.is_symlink():
            (destination / link.relative_to(source)).unlink(missing_ok=True)
    shutil.copytree(tree, destination / relative, dirs_exist_ok=True, symlinks=True,
                    ignore=shutil.ignore_patterns("build", ".lock"))
    for link in tree.rglob("*"):
        if not link.is_symlink():
            continue
        target = link.readlink()
        if target.is_relative_to(canonical_cache / "archive-v0"):
            archive = target.relative_to(canonical_cache)
            if (source / archive).is_dir():
                shutil.copytree(source / archive, destination / archive, dirs_exist_ok=True, symlinks=True)


def prime():
    wheel_cache = os.environ.get("KB_CUDA_WHEEL_CACHE")
    if wheel_cache:
        saved = Path(wheel_cache)
        cache = Path(os.environ["UV_CACHE_DIR"])
        copy_cuda_wheel(saved, cache, cache)
    locked_version_check()
    uv("lock", "--check", "--python", sys.executable)
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        compiler = directory / "compiler"
        uv("sync", "--locked", "--only-group", "cuda-build", "--no-install-workspace", "--python",
           sys.executable, env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(compiler)})
        cuda = compiler / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages/nvidia/cu13"
        (cuda / "lib/libcudart.so").symlink_to("libcudart.so.13")
        os.environ.update(CUDA_HOME=str(cuda), MAX_JOBS=os.environ.get("MAX_JOBS", "2"))
        sync(directory / "all", ("--all-packages", "--all-groups", "--all-extras"), private=True)
    if wheel_cache:
        copy_cuda_wheel(cache, saved, cache)
        for build in (cache / "sdists-v9/pypi/causal-conv1d").glob("*/*/src/build"):
            shutil.rmtree(build)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=("prime", "dev", *PROFILES))
    parser.add_argument("venv", type=Path, nargs="?", default=Path("/opt/venv"))
    args = parser.parse_args()
    if args.profile == "prime":
        prime()
    elif args.profile == "dev":
        sync(args.venv.resolve(), ("--all-packages", "--all-groups", "--all-extras"),
             private=True, workspace=True)
    else:
        package, extras = PROFILES[args.profile]
        selection = ("--package", package, "--no-dev", *(value for extra in extras for value in ("--extra", extra)))
        sync(args.venv.resolve(), selection, private=args.profile == "pdfparse")


if __name__ == "__main__":
    main()
