from .base import *  # noqa: F401,F403
from .base import ALLOWED_HOSTS

DEBUG = True

# El navegador puede entrar por localhost en IPv6 (http://[::1]:8001)
if "[::1]" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, "[::1]"]
