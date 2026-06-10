#!/bin/bash
# App Service startup command (when not using Docker)
gunicorn --bind 0.0.0.0:8000 --workers 2 --timeout 600 app:app
