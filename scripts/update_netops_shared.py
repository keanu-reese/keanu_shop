"""Helpers shared by the two host-side scripts."""
from pathlib import Path
import subprocess

APP_ID = 'netopslab-command-center'

def get_app_dir():
    candidates = (Path.home() / 'umbrel' / 'app-data' / APP_ID,
                  Path('/home/umbrel/umbrel/app-data') / APP_ID,
                  Path('/umbrel/app-data') / APP_ID)
    for candidate in candidates:
        if (candidate / 'umbrel-app.yml').is_file():
            return candidate
    return candidates[0]

def require_native(path):
    manifest = path / 'umbrel-app.yml'
    if not manifest.is_file() or f'id: {APP_ID}' not in manifest.read_text():
        raise RuntimeError('This is not a native NETOPS Umbrel app folder: ' + str(path))

def run(*args):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)
