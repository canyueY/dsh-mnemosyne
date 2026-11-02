#!/usr/bin/env bash
# Provision the dedicated Python runtime for the standalone Mnemosyne plugin.
# The runtime lives outside the repository:
#   $HOME/.local/share/Mnemosyne DSH Node/venv
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQ="${ROOT}/service/requirements.txt"
PYTHON="${PYTHON:-python3}"
RUNTIME="${HOME}/.local/share/Mnemosyne DSH Node"
VENV="${RUNTIME}/venv"
VPY="${VENV}/bin/python"

if [[ ! -f "${REQ}" ]]; then
  echo "requirements.txt not found: ${REQ}" >&2
  exit 1
fi

if [[ "${1:-}" == "--recreate" && -d "${VENV}" ]]; then
  echo "Removing existing runtime: ${VENV}"
  rm -rf "${VENV}"
fi

if [[ ! -x "${VPY}" ]]; then
  command -v "${PYTHON}" >/dev/null 2>&1 || {
    echo "Python 3.10+ not found (${PYTHON}). Set PYTHON=/path/to/python3." >&2
    exit 1
  }
  echo "Creating runtime venv: ${VENV}"
  mkdir -p "${RUNTIME}"
  "${PYTHON}" -m venv "${VENV}"
fi

echo "Installing Mnemosyne Node dependencies..."
"${VPY}" -m pip install --disable-pip-version-check --upgrade pip
"${VPY}" -m pip install --disable-pip-version-check -r "${REQ}"
"${VPY}" -c "import sys, fastapi, uvicorn, pydantic, pydantic_settings, httpx; print('runtime ok:', sys.version.split()[0], '| fastapi', fastapi.__version__, '| uvicorn', uvicorn.__version__)"

echo
echo "Mnemosyne runtime ready:"
echo "  python : ${VPY}"
echo "  bank   : ${HOME}/.local/share/Mnemosyne DSH Memory Bank"
