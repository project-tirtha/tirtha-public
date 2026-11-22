"""
Configuration shared by the views package, read from settings once, at import.
"""

from django.conf import settings

# Configuration
LOG_LOCATION = settings.LOG_LOCATION
PRE_URL = settings.PRE_URL
GOOGLE_LOGIN = settings.GOOGLE_LOGIN
ADMIN_MAIL = settings.ADMIN_MAIL
OAUTH_CONF = settings.OAUTH_CONF
FALLBACK_ARK_RESOLVER = settings.FALLBACK_ARK_RESOLVER

# Logging setup
# Logging is configured once, in settings (`LOGGING`). `basicConfig` is a
# no-op after the first caller wins, so these two calls were a race that
# `admin.py` always won -- and `LOG_LOCATION` was whatever that race decided.
