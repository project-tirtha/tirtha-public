#!/bin/bash
# NOTE: Run as root (i.e., with sudo) - sudo bash build.sh, while in the `build/` folder.

# Some checks
# ==================================================================================================
# Exit on error
set -e

# Checking if the script is being run from the correct directory
if [ "$(basename "$(pwd)")" != "build" ]; then
  echo "Please run the build.sh script from the correct directory (i.e., ./tirtha-public/build/)."
  exit
fi

# Source the environment variables
source tirtha.env  # CHANGEME: NOTE: Edit the tirtha.env file to set the environment variables.

# cd to project root, i.e., tirtha-public/
cd ../
# ==================================================================================================

# Triggering the services
# ==================================================================================================
# Activating the virtual environment
# `./.venv`, with the dot. `uv sync` creates `.venv` -- line 79 of build.sh
# says so in its own comment and line 86 activates it correctly -- and these
# two then activated `./venv`, which has never existed. `source` on a missing
# file is a non-fatal error under `set +e`, so the script carried on with the
# system Python and failed later, somewhere else.
source ./.venv/bin/activate

# cd to the backend directory
cd ./tirtha_bk/

# Starting celery in a tmux session
tmux new-session -d -s celery_session || tmux attach-session -t celery_session
# Two workers, not one (internal design notes). A single process cannot have two
# concurrencies, and that is the whole point of the split: image checks used to
# inherit the reconstruction bottleneck because they shared a --concurrency=1
# worker with it.
#
# No --max-tasks-per-child on either. It was on both, and under `-P threads` it
# does nothing: celery's thread pool builds a bare ThreadPoolExecutor and never
# reads the option -- only prefork implements it. So the recycling this script
# used to claim ("a reconstruction leaves large allocations behind") was never
# in force.
#
# `-P threads` is what stays, and it is not the thing to change. AliceVision's
# `_parallelRunner` opens a stdlib multiprocessing.Pool, and prefork workers are
# daemonic, so switching pools to make the flag work breaks the aV path with
# `daemonic processes are not allowed to have children`. The premise was hollow
# anyway: every heavy stage is a subprocess, so the worker never holds those
# allocations.
#
# In production these run as systemd services (one per queue)
# rather than from tmux, so they survive a reboot and restart on a crash. This
# script is the from-source path.
tmux send-keys -t celery_session "celery -A tirtha worker -l INFO -Q recon -c 1 -P threads --beat -n recon@%h" C-m
tmux new-window -t celery_session -n ingest
tmux send-keys -t celery_session:ingest "celery -A tirtha worker -l INFO -Q ingest -c 8 -P threads -n ingest@%h" C-m

# Starting the frontend | NOTE: Browse to HOST_IP:8000 in a browser to access the frontend.
gunicorn --bind 0.0.0.0:$GUNICORN_PORT tirtha_bk.wsgi
# ==================================================================================================
