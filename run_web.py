"""
Entry point for the Flask web application.
Run from the project root: python run_web.py
"""

import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from src.app import app

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
