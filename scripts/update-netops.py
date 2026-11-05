#!/usr/bin/env python3
"""Explicit host-side NETOPS updater: local folder/zip or pinned GitHub branch/tag.

Never runs inside the dashboard; never mounts Docker socket into a web service.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlparse
import zipfile

APP_ID = 'netopslab-command-center'
MANDATORY = ('app/server.py', 'app/store.py', 'app/static/dashboard.html', 'Dockerfile')
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def err(message):
    raise RuntimeError(message)


def default_app_dir():
    home = Path.home()
    opts = [home / 'umbrel' / 'app-data' / APP_ID,
            Path('/home/umbrel/umbrel/app-data') / APP_ID,
            Path('/umbrel/app-data') / APP_ID]
    for p in opts:
        if (p / 'umbrel-app.yml').is_file():
            return p
    return opts[0]


def run(*args, cwd=None):
    print('+', ' '.join(str(i) for i in args), flush=True)
    subprocess.run([str(i) for i in args], cwd=cwd, check=True)


def find_source(root):
    root = Path(root).resolve()
    # Allow GitHub repo root, directly extracted app folder, or a ZIP with one
    # enclosing directory (e.g. release-v3/netopslab-command-center).
    candidates = [root]
    if root.is_dir():
        children = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()]
        candidates += children
        for child in children:
            candidates.extend(p for p in child.iterdir() if p.is_dir() and not p.is_symlink())
    for c in candidates:
        if c.is_dir() and all((c / name).is_file() for name in MANDATORY):
            return c
    err('Package does not contain app/server.py, app/store.py, app/static/dashboard.html and Dockerfile')


def validate_source(src):
    for name in MANDATORY:
        f = src / name
        if not f.is_file() or f.is_symlink() or f.stat().st_size > 5_000_000:
            err(f'Unsafe or missing file: {f}')
    for name in ('app/server.py', 'app/store.py'):
        ast.parse((src / name).read_text(encoding='utf-8'), filename=name)
    for p in (src / 'app').rglob('*'):
        if p.is_symlink():
            err(f'Symlinks in app source are not allowed: {p}')
    if (src / 'app/static/dashboard.html').stat().st_size < 1000:
        err('Dashboard HTML is unusually small; refusing update')


def safe_unzip(archive, dest):
    with zipfile.ZipFile(archive) as z:
        total = 0
        for entry in z.infolist():
            path = Path(entry.filename)
            total += entry.file_size
            if (path.is_absolute() or '..' in path.parts or entry.file_size > MAX_ARCHIVE_BYTES
                    or total > MAX_ARCHIVE_BYTES or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                err(f'Unsafe ZIP archive entry: {entry.filename}')
        z.extractall(dest)


def safe_github(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'github.com' or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
        err('GitHub source must be https://github.com/OWNER/REPO (no credentials or query)')
    parts = parsed.path.rstrip('/').removesuffix('.git').strip('/').split('/')
    if len(parts) != 2 or not all(re.fullmatch(r'[A-Za-z0-9_.-]+', x) for x in parts):
        err('GitHub source must be https://github.com/OWNER/REPO')
    return f'https://github.com/{parts[0]}/{parts[1]}.git'


def source_from_args(args, stage):
    if args.github:
        repo = safe_github(args.github)
        ref = args.ref
        if (not ref or not re.fullmatch(r'[A-Za-z0-9._/-]{1,128}', ref)
                or ref.startswith(('-', '/')) or '..' in ref or '@{' in ref or ref.endswith('/')):
            err('A safe --ref <tag-or-branch> must be specified for GitHub updates')
        # No GitHub actions, script execution or automatic update. Only checked-out files are read.
        target = stage / 'repo'
        run('git', '-c', 'protocol.file.allow=never', 'clone', '--depth', '1', '--branch', ref, '--', repo, target)
        return find_source(target), f'{args.github}@{ref}'
    src = Path(args.local).expanduser().resolve()
    if not src.exists():
        err(f'Local path does not exist: {src}')
    if src.is_dir():
        return find_source(src), str(src)
    if src.suffix.lower() != '.zip':
        err('Local source must be a directory or .zip archive')
    target = stage / 'extracted'
    target.mkdir()
    safe_unzip(src, target)
    return find_source(target), str(src)


def must_be_native_app(app_dir):
    manifest = app_dir / 'umbrel-app.yml'
    if not manifest.is_file() or f'id: {APP_ID}' not in manifest.read_text(encoding='utf-8'):
        err(f'Not a native NETOPS Umbrel app directory: {app_dir} (use --app-dir if needed)')
    for file in ('app/server.py', 'app/store.py', 'Dockerfile'):
        if not (app_dir / file).exists():
            err(f'Installed Umbrel app is missing {file}: {app_dir}')


def backup(app_dir, path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as f, tarfile.open(fileobj=f, mode='w:gz') as tar:
        for name in ('app', 'Dockerfile', 'data', 'umbrel-app.yml'):
            if (app_dir / name).exists():
                tar.add(app_dir / name, arcname=name, recursive=True)
    print(f'Created full backup (contains SSH keys): {path}', flush=True)


def main():
    ap = argparse.ArgumentParser(description='NETOPS native Umbrel app code updater')
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument('--github', metavar='HTTPS_GITHUB_REPO')
    source.add_argument('--local', metavar='DIR_OR_ZIP')
    ap.add_argument('--ref', help='Required exact tag or branch when using --github')
    ap.add_argument('--app-dir', type=Path, default=default_app_dir(), help='Native Umbrel app-data directory')
    ap.add_argument('--backup-dir', type=Path, default=Path.home() / 'netops-backups')
    ap.add_argument('--dry-run', action='store_true', help='Validate source/destination without stopping app')
    args = ap.parse_args()
    if args.github and not args.ref:
        ap.error('--github requires --ref to pin the requested release or branch')
    if args.local and args.ref:
        ap.error('--ref applies only to --github')
    app_dir = args.app_dir.expanduser().resolve()
    must_be_native_app(app_dir)
    with tempfile.TemporaryDirectory(prefix='netops-updater-') as scratch:
        src, label = source_from_args(args, Path(scratch))
        validate_source(src)
        print(f'Validated source: {label}\nDestination: {app_dir}', flush=True)
        if args.dry_run:
            print('DRY RUN: no changes made.')
            return
        if not shutil.which('umbreld'):
            err('umbreld is not in PATH. Run on the Umbrel host, not in a container.')
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup_path = args.backup_dir.expanduser().resolve() / f'netops-{timestamp}.tar.gz'
        # Stop first for a consistent SQLite and key backup.
        run('umbreld', 'client', 'apps.stop.mutate', '--appId', APP_ID)
        did_change = False
        moved_old = None
        old_dockerfile = None
        try:
            backup(app_dir, backup_path)
            staged = app_dir / f'.netops-staged-{timestamp}'
            shutil.copytree(src / 'app', staged)
            staged_dockerfile = app_dir / f'.netops-Dockerfile-staged-{timestamp}'
            shutil.copy2(src / 'Dockerfile', staged_dockerfile)
            old_dockerfile = app_dir / f'.netops-Dockerfile-backup-{timestamp}'
            shutil.copy2(app_dir / 'Dockerfile', old_dockerfile)
            moved_old = app_dir / f'.netops-code-backup-{timestamp}'
            (app_dir / 'app').rename(moved_old)
            # The transaction has begun; restore old code even if the next operation fails.
            did_change = True
            staged.rename(app_dir / 'app')
            staged_dockerfile.replace(app_dir / 'Dockerfile')
            run('umbreld', 'client', 'apps.restart.mutate', '--appId', APP_ID)
            # Umbrel app restart builds the local Dockerfile; inspect health if possible.
            if shutil.which('docker'):
                check = subprocess.run(['docker', 'inspect', 'netopslab-command-center_server_1',
                    '--format', '{{.State.Status}}'], capture_output=True, text=True)
                if check.returncode == 0:
                    print(f'Container state: {check.stdout.strip()}')
            meta = {'source': label, 'updated_at_utc': timestamp, 'backup': str(backup_path)}
            (app_dir / 'netops-last-update.json').write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
            print('Update complete. Existing app data and user accounts preserved.')
            print(f'Rollback archive: {backup_path}')
        except Exception:
            print('Upgrade failed. Attempting to restore old application source...', file=sys.stderr)
            if did_change and moved_old and moved_old.exists():
                if (app_dir / 'app').exists():
                    shutil.rmtree(app_dir / 'app')
                moved_old.rename(app_dir / 'app')
                if old_dockerfile and old_dockerfile.exists():
                    shutil.copy2(old_dockerfile, app_dir / 'Dockerfile')
            try:
                run('umbreld', 'client', 'apps.restart.mutate', '--appId', APP_ID)
            except Exception as exc:
                print(f'Could not restart original app automatically: {exc}', file=sys.stderr)
            raise
        finally:
            if moved_old and moved_old.exists():
                shutil.rmtree(moved_old)
            if old_dockerfile and old_dockerfile.exists():
                old_dockerfile.unlink()
            for file in app_dir.glob(f'.netops-*{timestamp}'):
                if file.is_dir(): shutil.rmtree(file)
                else: file.unlink()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, SyntaxError, zipfile.BadZipFile) as ex:
        print(f'ERROR: {ex}', file=sys.stderr)
        sys.exit(1)
