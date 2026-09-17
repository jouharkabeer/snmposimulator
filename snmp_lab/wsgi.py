"""WSGI module kept for Django completeness. The simulator does not run a web server."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "snmp_lab.settings")

application = get_wsgi_application()
