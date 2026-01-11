"""Vercel serverless entry point."""
import os
import sys

# Voeg de root directory toe aan het path voor imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import app

# Vercel verwacht 'app' als ASGI application
