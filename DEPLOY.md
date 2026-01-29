# Deploying Tirtha

This document contains deployment instructions for Project Tirtha. Development-related setup can be found in [`DEVELOP.md`](./DEVELOP.md).

## Recommended Specs

- **OS**: Ubuntu 24.04 LTS (Other Linux distros may work, but are not tested)
- **RAM**: 16+ GB for moderately-sized image sets (~500 images)
- **VRAM**: 24+ GB; NVIDIA GPU required for CUDA support & Gaussian Splatting
- **CPU**: 16+ cores recommended
- **Storage**: 100+ GB free space recommended

## Software

- Python 3.11
- Node.js >= 24.0.0
- npm >= 11.0.0
- COLMAP 3.11.0
- ffmpeg >= 4.0.0

The build process (manual) installs additional dependencies.

## Deployment / Development Setup (Manual)

> The Docker deployment maintained separately is currently on hold. The manual setup below is recommended.

1. Clone the repository and `cd` to the project directory:

```bash
git clone https://github.com/project-tirtha/tirtha-public.git
cd tirtha-public
```

2. Edit the environment file used by the build: `build/tirtha.env` to set required environment variables.

3. Review `build.sh` carefully and edit to match your system paths and preferences. The script can be configured to skip installing some optional backend dependencies to save time.

4. Default ports used by the build are:

- `8000` for gunicorn
- `8001` for Postgres
- `15672` for RabbitMQ

Ensure these ports are free on the host or update `tirtha.env`, `gunicorn.conf.manual.py`, and `tirtha.docker.nginx` before running the build.

5. Run the build script from the `build` directory:

```bash
cd build
sudo bash build.sh
```

6. Start the application:

```bash
bash start.sh
```

7. Access the web interface at `http://localhost:8000` (or `http://<HOST_IP>:8000` for remote servers). Django admin is at `http://localhost:8000/admin`. Default admin credentials are set in `tirtha.env`.

8. Logs: check `/var/www/tirtha/logs/` for Tirtha logs. System service logs for RabbitMQ/Postgres are available via `journalctl`.

## SSL and systemd

- See `tirtha_bk/config/tirtha.ssl.nginx` for SSL nginx configuration.
- Example service and socket files are in `tirtha_bk/config/` (e.g., `tirthad.docker.service`, `tirthad.docker.socket`).

## Important Notes

1. The production directory is currently hard-coded to `/var/www/tirtha`. Changing this requires updating nginx and gunicorn config files and `build.sh`.
2. You may need to configure your firewall to allow traffic on the ports used by Tirtha.

## Quick Troubleshooting

- If ports are in use, change them in `tirtha.env` and corresponding config files.
- If services fail to start, check system logs with `journalctl -u <service>` and app logs in `/var/www/tirtha/logs/`.

## Further resources

- For detailed deployment automation or containerization, refer to the `tirtha-docker` project in the organisation, or open an issue/discussion in this repository.
