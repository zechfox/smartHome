#!/usr/bin/env bash
#
# Build a distributable tarball of smartHome.
#
# Usage:
#     ./deploy/package.sh [VERSION]
#
# Creates dist/smartHome-<version>.tar.gz (plus a .sha256 checksum) containing
# the application, requirements, config template, README and the deploy
# scripts. Copy the tarball to the target, extract it and run:
#
#     sudo ./deploy/install.sh
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${SOURCE_DIR}/dist"
PACKAGE_NAME="smartHome"

if [[ -n "${1:-}" ]]; then
    VERSION="$1"
else
    VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "${SOURCE_DIR}/app/__init__.py")"
fi
[[ -n "${VERSION}" ]] || { echo "error: cannot determine version" >&2; exit 1; }

STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGE_DIR}"' EXIT
PKG_DIR="${STAGE_DIR}/${PACKAGE_NAME}-${VERSION}"

mkdir -p "${PKG_DIR}/deploy"
cp -a "${SOURCE_DIR}/app" "${PKG_DIR}/app"
cp -a "${SOURCE_DIR}/config.yaml" "${PKG_DIR}/config.yaml"
cp -a "${SOURCE_DIR}/requirements.txt" "${PKG_DIR}/requirements.txt"
cp -a "${SOURCE_DIR}/README.md" "${PKG_DIR}/README.md"
cp -a "${SOURCE_DIR}/pyproject.toml" "${PKG_DIR}/pyproject.toml"
cp -a "${SOURCE_DIR}/deploy/install.sh" "${PKG_DIR}/deploy/install.sh"
cp -a "${SOURCE_DIR}/deploy/smart-home.user.service" \
    "${PKG_DIR}/deploy/smart-home.user.service"

find "${PKG_DIR}" -type d -name __pycache__ -prune -exec rm -rf {} +
find "${PKG_DIR}" -type f -name '*.py[co]' -delete

mkdir -p "${DIST_DIR}"
ARCHIVE="${DIST_DIR}/${PACKAGE_NAME}-${VERSION}.tar.gz"
tar -C "${STAGE_DIR}" -czf "${ARCHIVE}" "${PACKAGE_NAME}-${VERSION}"

ARCHIVE_NAME="$(basename "${ARCHIVE}")"
if command -v sha256sum >/dev/null 2>&1; then
    ( cd "${DIST_DIR}" && sha256sum "${ARCHIVE_NAME}" > "${ARCHIVE_NAME}.sha256" )
elif command -v shasum >/dev/null 2>&1; then
    ( cd "${DIST_DIR}" && shasum -a 256 "${ARCHIVE_NAME}" > "${ARCHIVE_NAME}.sha256" )
fi

echo
echo "created ${ARCHIVE}"
echo "copy it to the target, then run:"
echo
echo "    tar -xzf ${ARCHIVE_NAME}"
echo "    cd ${PACKAGE_NAME}-${VERSION}"
echo "    sudo ./deploy/install.sh"
echo
tar -tzf "${ARCHIVE}"
