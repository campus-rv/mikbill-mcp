#!/bin/sh
# Regenerate the hash-pinned lock files after changing the dependencies in pyproject.toml:
#
#   sh scripts/lock.sh
#
# It runs uv inside the same Python image the Dockerfile uses, so nothing has to be installed locally. The locks are
# "universal": one file serves Linux, macOS and Windows and every supported Python (3.12 and newer).
#
#   requirements.lock         runtime dependencies   -> Dockerfile (final stage)
#   requirements-build.lock   build, hatchling       -> Dockerfile (build stage)
#   requirements-dev.lock     runtime + dev extras   -> CI
set -eu
IMAGE=$(sed -n 's/^FROM \(python:[^ ]*\) AS build$/\1/p' Dockerfile)
docker run --rm -v "$(pwd):/src" -w /src "$IMAGE" sh -ec '
  pip install -q --root-user-action=ignore --disable-pip-version-check uv
  C="uv pip compile --universal --generate-hashes --python-version 3.12 -q"
  $C pyproject.toml -o requirements.lock \
     --custom-compile-command "scripts/lock.sh   (runtime dependencies; installed by the Dockerfile with --require-hashes)"
  $C pyproject.toml --extra dev -o requirements-dev.lock \
     --custom-compile-command "scripts/lock.sh   (runtime + dev dependencies; installed by CI with --require-hashes)"
  printf "build\nhatchling\n" > /tmp/build.in
  $C /tmp/build.in -o requirements-build.lock \
     --custom-compile-command "scripts/lock.sh   (wheel build tools; installed by the Dockerfile build stage with --require-hashes)"
'
