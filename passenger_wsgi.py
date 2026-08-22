import os
import sys

# Ajouter le répertoire de l'application au PATH Python
sys.path.insert(0, os.path.dirname(__file__))

from a2wsgi import ASGIMiddleware
from main import app

# Adaptateur ASGI vers WSGI pour Passenger (PlanetHoster / cPanel / N0C)
application = ASGIMiddleware(app)
