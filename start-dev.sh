#!/usr/bin/env bash
set -euo pipefail

# start-dev.sh
# - Installs Miniconda (user-local) and creates a conda env with COLMAP
# - Installs Node/npm via nvm and global tools obj2gltf + gltfpack (user-local)
# - Creates Python venv, installs requirements, runs migrations/collectstatic, and starts devserver
# Usage: ./start-dev.sh

# Check for required host tools and print install instructions if missing
check_prereqs() {
  required=(curl git wget unzip python3.11)
  missing=()
  for cmd in "${required[@]}"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd")
    fi
  done
  if [ ${#missing[@]} -ne 0 ]; then
    echo "ERROR: Missing required tools: ${missing[*]}"
    echo
    echo "Install on Debian/Ubuntu:"
    echo "  sudo apt-get update && sudo apt-get install -y curl git wget unzip python3.11"
    echo
    exit 1
  fi
}

check_prereqs

# Getting submodules for ImageOps models - Must be run as the user (not root)
# CHANGEME: NOTE: Comment out to skip ImageOps models
git config --global http.postBuffer 524288000
git submodule update --init --recursive

# Getting the pre-trained checkpoints for ImageOps models
# CHANGEME: NOTE: Comment these out to skip the ImageOps models
wget https://smlab.niser.ac.in/project/tirtha/static/artifacts/MR2021.1.0.zip \
  && unzip MR2021.1.0.zip \
  && mv ./bin21/ ./tirtha_bk/bin21/ \
  && rm ./MR2021.1.0.zip
wget https://smlab.niser.ac.in/project/tirtha/static/artifacts/ckpt_kadid10k.pt \
  && mv ./ckpt_kadid10k.pt ./tirtha_bk/nn_models/MANIQA/

USER_CONDA_DIR="$HOME/miniconda3"
CONDA_ENV_NAME="tirtha-conda"
GLTFPACK_URL="https://github.com/zeux/meshoptimizer/releases/download/v0.20/gltfpack-ubuntu.zip"
LOCAL_BIN_DIR="$HOME/.local/bin"

mkdir -p "$LOCAL_BIN_DIR"
export PATH="$LOCAL_BIN_DIR:$PATH"

install_miniconda() {
  if command -v conda >/dev/null 2>&1; then
    echo "conda found; skipping Miniconda install"
    return
  fi
  echo "Installing Miniconda to ${USER_CONDA_DIR} (user-local)..."
  tmpfile="/tmp/miniconda_install.sh"
  curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o "$tmpfile"
  bash "$tmpfile" -b -p "$USER_CONDA_DIR"
  rm -f "$tmpfile"
  export PATH="$USER_CONDA_DIR/bin:$PATH"
}

create_conda_colmap() {
  export PATH="$USER_CONDA_DIR/bin:$PATH"
  if conda info --envs | grep -q "${CONDA_ENV_NAME}"; then
    echo "Conda env ${CONDA_ENV_NAME} already exists; skipping colmap install"
    return
  fi
  echo "Creating conda env ${CONDA_ENV_NAME} and installing colmap (conda-forge)..."
  conda create -y -n "${CONDA_ENV_NAME}" -c conda-forge colmap
}

install_node_tools() {
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    echo "Node/npm found; skipping nvm install"
  else
    echo "Installing nvm and Node (user-local)..."
    # Install nvm (if not present)
    if [ ! -d "$HOME/.nvm" ]; then
      curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    fi
    # shellcheck disable=SC1090
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
    nvm install --lts
  fi

  # Ensure npm in path
  if command -v npm >/dev/null 2>&1; then
    echo "Installing obj2gltf globally (if missing)"
    if ! command -v obj2gltf >/dev/null 2>&1; then
      npm install -g obj2gltf || true
    fi
  fi

  # Install gltfpack to user-local bin
  if [ ! -x "$LOCAL_BIN_DIR/gltfpack" ]; then
    echo "Installing gltfpack to ${LOCAL_BIN_DIR}"
    curl -fsSL -o /tmp/gltfpack.zip "$GLTFPACK_URL"
    mkdir -p /tmp/gltfpack_unzip
    unzip -o /tmp/gltfpack.zip -d /tmp/gltfpack_unzip
    if [ -f /tmp/gltfpack_unzip/gltfpack ]; then
      mv /tmp/gltfpack_unzip/gltfpack "$LOCAL_BIN_DIR/gltfpack"
      chmod +x "$LOCAL_BIN_DIR/gltfpack"
    fi
    rm -rf /tmp/gltfpack.zip /tmp/gltfpack_unzip
  else
    echo "gltfpack already installed in ${LOCAL_BIN_DIR}"
  fi
}

# Ensure venv activation if present
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "=== Installing system toolchain for pipelines (Miniconda/colmap + Node/tools) ==="
install_miniconda
create_conda_colmap
install_node_tools

echo "=== Preparing Python venv and project dependencies ==="
if [ ! -d .venv ]; then
  python3.11 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install --upgrade pip setuptools wheel
pip install -r ./requirements.txt --default-timeout=2000
pip install -e ./tirtha_bk/nn_models/nsfw_model/
pip install protobuf==3.20.3

export TIRTHA_DEV=1
export DJANGO_SECRET_KEY='django-secret-change-me'  # CHANGEME:
export DJANGO_SETTINGS_MODULE=tirtha_bk.settings
export DJANGO_SUPERUSER_NAME='dbadmin'  # CHANGEME:
export DJANGO_SUPERUSER_EMAIL='tadmin@example.com'  # CHANGEME:
export DJANGO_SUPERUSER_PASSWORD='tadminpwd'  # CHANGEME:

echo "Running migrations (dev)..."
python manage.py makemigrations --noinput || true
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

# Create superuser
echo "Creating superuser..."
DJANGO_SUPERUSER_PASSWORD=$DJANGO_SUPERUSER_PASSWORD python ./tirtha_bk/manage.py createsuperuser --no-input --username $DJANGO_SUPERUSER_NAME --email "$DJANGO_SUPERUSER_EMAIL"

echo "=== Starting Django development server ==="
python manage.py runserver 0.0.0.0:8000
