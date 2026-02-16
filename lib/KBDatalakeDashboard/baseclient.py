# Import wrapper for kb-sdk compile compatibility
# kb-sdk generates Server.py that tries to import from KBDatalakeDashboard.baseclient
# but the actual baseclient is in installed_clients/
from installed_clients.baseclient import *
