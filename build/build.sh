#!/bin/bash
# NOTE: Run as root (i.e., with sudo) - sudo bash build.sh, while in the `build/` folder.

# Some checks
# ==================================================================================================
# Exit on error
set -e

# Checking if the script is being run as root
if [ "$EUID" -ne 0 ]
  then echo "Please run the build.sh script as root (i.e., with sudo)."
  exit
fi

# Checking if the script is being run from the correct directory
if [ "$(basename "$(pwd)")" != "build" ]; then
  echo "Please run the build.sh script from the correct directory (i.e., ./tirtha-public/build/)."
  exit
fi

# Checking if the tirtha.env file exists
if [ ! -f "tirtha.env" ]; then
  echo "Please make sure tirtha.env exists in ./tirtha-public/build/."
  exit
fi

# Source the environment variables
source tirtha.env  # CHANGEME: NOTE: Edit the tirtha.env file to set the environment variables.

# cd to project root, i.e., tirtha-public/
cd ../

# Checking if the project manifest exists
if [ ! -f "./pyproject.toml" ]; then
  echo "Please make sure pyproject.toml exists in ./tirtha-public/."
  exit
fi

# Checking if the tirtha.env file has the required environment variables
# if [ -z "$DB_NAME" ] || [ -z "$DB_USER" ] || [ -z "$DB_PWD" ] || [ -z "$RMQ_USER" ] || [ -z "$RMQ_PWD" ] || [ -z "$RMQ_VHOST" ] || [ -z "$DJANGO_SUPERUSER_NAME" ] || [ -z "$DJANGO_SUPERUSER_EMAIL" ] || [ -z "$DJANGO_SUPERUSER_PASSWORD" ] || [ -z "$GUNICORN_PORT" ]
#   then echo "Please set the required environment variables in the tirtha.env file."
#   exit
# fi
# ==================================================================================================

# Main build process
# ==================================================================================================
# Installing dependencies
echo "Installing dependencies..."

apt-get update \
  && apt-get install -y \
    nano \
    curl \
    git \
    tmux \
    wget \
    unzip \
    ffmpeg \
    libopencv-dev \
    nginx \
    rabbitmq-server \
    libpq-dev \
    postgresql \
    postgresql-contrib \
    python3.11-dev \
    python3.11-venv \
    python3-pip
    

# tirtha-recon (github.com/project-tirtha/tirtha-recon) is a path dependency:
# it must be cloned beside this repository, as ../tirtha-recon.
if [ ! -f "../tirtha-recon/pyproject.toml" ]; then
  echo "Please clone https://github.com/project-tirtha/tirtha-recon beside this repository (../tirtha-recon)."
  exit 1
fi

# Creating a Python virtual environment and installing dependencies
# uv resolves from pyproject.toml and pins from uv.lock, creating ./.venv
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv sync --frozen \
  && chown -R $SUDO_USER:$SUDO_USER ./.venv/

# AliceVision binaries for the photogrammetry (aV) path.
# CHANGEME: NOTE: Comment these out to skip the aV path
wget https://smlab.niser.ac.in/project/tirtha/static/artifacts/MR2021.1.0.zip \
  && unzip MR2021.1.0.zip \
  && mv ./bin21/ ./tirtha_bk/bin21/ \
  && rm ./MR2021.1.0.zip

# Setting up npm to install obj2gltf and gltfpack
# CHANGEME: NOTE: Comment these out if developing only for the frontend
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
# The trailing backslash is load-bearing: without it the next line begins with
# `&&`, which is a syntax error, and `bash -n build/build.sh` has been failing
# ever since it was dropped. Nothing caught it because this script is only run
# on a fresh host, and this deployment was installed before the break.
. /root/.bashrc \
  && nvm install node \
  && nvm use node \
  && npm install -g obj2gltf
wget https://github.com/zeux/meshoptimizer/releases/download/v0.20/gltfpack-ubuntu.zip \
  && unzip gltfpack-ubuntu.zip \
  && chmod +x gltfpack \
  && mv gltfpack /usr/local/bin/ \
  && rm /gltfpack-ubuntu.zip

# Copying the configuration files to the appropriate locations
cp ./tirtha_bk/tirtha_bk/local_settings.manual.py ./tirtha_bk/tirtha_bk/local_settings.py \
  && cp ./tirtha_bk/gunicorn/gunicorn.conf.manual.py ./tirtha_bk/gunicorn/gunicorn.conf.py
# ==================================================================================================

# Main Configuration
# ==================================================================================================
echo "Configuring dependencies and Tirtha..."

# Postgres
source ./build/init_db.sh
systemctl start postgresql
systemctl enable postgresql

# RabbitMQ
systemctl start rabbitmq-server
systemctl enable rabbitmq-server
rabbitmq-plugins enable rabbitmq_management
rabbitmqctl add_user $RMQ_USER $RMQ_PWD
rabbitmqctl add_vhost $RMQ_VHOST
rabbitmqctl set_user_tags $RMQ_USER administrator
rabbitmqctl set_permissions -p $RMQ_VHOST $RMQ_USER ".*" ".*" ".*"
rabbitmqctl eval 'application:get_env(rabbit, consumer_timeout).'
echo "consumer_timeout = 31556952000" | tee -a /etc/rabbitmq/rabbitmq.conf
systemctl restart rabbitmq-server

# The error pages nginx serves are rendered by Django further down
# (`render_error_pages`), not copied: a verbatim copy left `{% url 'index' %}`
# as the link text's href.

# Copying the production folder to the container and setting permissions
cp -r ./build/tirtha "$TIRTHA_ROOT"

# Render the nginx config from its template. envsubst substitutes only the
# variables named, so nginx's own $host / $remote_addr survive untouched.
for tpl in ./tirtha_bk/config/*.nginx.template; do
  [ -e "$tpl" ] || continue
  out="${tpl%.template}"
  envsubst '${TIRTHA_ROOT} ${PROD_DIR} ${ERRORS_DIR} ${PRE_URL}' < "$tpl" > "$out"
  echo "Rendered $out"
done
# Nginx: the plain-HTTP config. For TLS use tirtha.ssl.nginx instead.
cp ./tirtha_bk/config/tirtha.docker.nginx /etc/nginx/sites-available/tirtha
ln -sf /etc/nginx/sites-available/tirtha /etc/nginx/sites-enabled/
chmod -R 755 "$TIRTHA_ROOT"/
chown -R $SUDO_USER:$SUDO_USER "$TIRTHA_ROOT"/

# Django
# Run some config commands as the user (not root)
# `./.venv`, with the dot. `uv sync` creates `.venv` -- line 79 says so in its
# own comment and line 86 activates it correctly -- while this line and start.sh
# activated `./venv`, which has never existed. `source` on a missing file is a
# non-fatal error under `set +e`, so the script carried on with the system
# Python and failed later, somewhere unrelated.
#
# This comment sits above the heredoc, not inside it. The delimiter below is
# unquoted because $DJANGO_SUPERUSER_PASSWORD and $SUDO_USER have to expand --
# and in an unquoted heredoc backticks are command substitutions run by the
# shell that reads it, which here is the root shell running sudo bash build.sh.
# The five lines this replaces held twelve backticks, so uv sync, .venv,
# ./venv, ./.venv, source and set +e all executed as root in the repository
# root, and the comment reached the sub-shell mangled. Anything added inside a
# heredoc in this file has the same property.
sudo -u $SUDO_USER bash - <<EOF
  source ./.venv/bin/activate
  python ./tirtha_bk/manage.py makemigrations tirtha
  python ./tirtha_bk/manage.py collectstatic --no-input
  python ./tirtha_bk/manage.py render_error_pages "$ERRORS_DIR"
  python ./tirtha_bk/manage.py migrate
  DJANGO_SUPERUSER_PASSWORD=$DJANGO_SUPERUSER_PASSWORD python ./tirtha_bk/manage.py createsuperuser --no-input --username $DJANGO_SUPERUSER_NAME --email "$DJANGO_SUPERUSER_EMAIL"
EOF
# ==================================================================================================

# Starting services
# ==================================================================================================
# Trigger the services
# echo "Starting services..."
# source start.sh  # CHANGEME: NOTE: Uncomment if you want to start the services after the build
echo "Run the start.sh script to start the services manually."
# ==================================================================================================
