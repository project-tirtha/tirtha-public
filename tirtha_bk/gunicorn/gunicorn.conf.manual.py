# NOTE: Rename this file to gunicorn.conf.py after editing
import os

workers = 16  # CHANGEME: as needed
syslog = True
bind = ["0.0.0.0:80"]  # CHANGEME:
umask = 0
loglevel = "info"
user = os.getenv("USER", "")  # CHANGEME:
group = os.getenv("USER", "")  # CHANGEME:
# Log paths follow LOG_DIR so a second deployment does not write into the
# first one's logs (internal design notes).
_log_dir = os.getenv("LOG_DIR", "/var/www/tirtha/prod/logs")
accesslog = os.path.join(_log_dir, "gunicorn_access.log")
access_log_format = (
    '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" "%({X-Real-IP}i)s"'
)
errorlog = os.path.join(_log_dir, "gunicorn_error.log")
