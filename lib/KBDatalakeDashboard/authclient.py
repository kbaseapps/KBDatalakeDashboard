# Import wrapper for kb-sdk compile compatibility
# kb-sdk generates Server.py that tries to import from KBDatalakeDashboard.authclient
# but the actual authclient is in installed_clients/
from installed_clients.authclient import *
