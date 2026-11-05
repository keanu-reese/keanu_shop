#!/usr/bin/env python3
"""One-time migration from existing NETOPS v2/v3 Compose named volumes to native Umbrel storage.

Always stops the native app, makes a backup, and keeps the old volumes intact.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from update_netops_shared import get_app_dir, run, require_native  # local helper, no external deps

APP_ID = 'netopslab-command-center'
OLD_DATA = 'netops-command-center-data'
OLD_SSH = 'netops-command-center-ssh'


def volume_path(name):
    p = subprocess.run(['docker', 'volume', 'inspect', name, '--format', '{{.Mountpoint}}'],
                       capture_output=True, text=True, check=True)
    path = Path(p.stdout.strip())
    if not path.is_dir(): raise RuntimeError(f'Volume mountpoint not available: {name}')
    return path


def copy_contents(src: Path, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    for source in src.iterdir():
        if source.is_symlink(): raise RuntimeError('Refusing source symlink: ' + str(source))
        target = dest / source.name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True, symlinks=False)
        else:
            shutil.copy2(source, target)


def main():
    ap = argparse.ArgumentParser(description='Migrate NETOPS standalone Docker volumes to a native Umbrel app')
    ap.add_argument('--app-dir', type=Path, default=get_app_dir())
    ap.add_argument('--backup-dir', type=Path, default=Path.home() / 'netops-backups')
    args = ap.parse_args()
    native = args.app_dir.expanduser().resolve()
    require_native(native)
    old_data, old_ssh = volume_path(OLD_DATA), volume_path(OLD_SSH)
    if not any(old_data.iterdir()): raise RuntimeError('Old volume appears empty; refusing migration')
    prior = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', 'netops-command-center'],
                           capture_output=True, text=True)
    if prior.returncode == 0 and prior.stdout.strip().lower() == 'true':
        raise RuntimeError('Old container netops-command-center is running. Stop it first: docker stop netops-command-center')
    run('umbreld', 'client', 'apps.stop.mutate', '--appId', APP_ID)
    data = native / 'data'
    args.backup_dir.expanduser().mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = args.backup_dir.expanduser() / ('native-before-import-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        if data.exists(): tar.add(data, arcname='data')
    os.chmod(archive, 0o600)
    print('Backed up native data to:', archive)
    try:
        data.mkdir(exist_ok=True)
        old_db = old_data / 'netops.sqlite3'
        new_db = data / 'netops.sqlite3'
        # Never replace existing native user accounts/dashboards with older database.
        if old_db.exists():
            if new_db.exists():
                with sqlite3.connect(new_db) as conn:
                    n_users = conn.execute('select count(*) from users').fetchone()[0]
                    n_boards = conn.execute('select count(*) from dashboards').fetchone()[0]
                if n_users != 1 or n_boards != 2:
                    raise RuntimeError('Native accounts/boards already changed; refusing overwrite. Restore backup or migrate manually.')
            if any((old_data / f'netops.sqlite3{s}').exists() for s in ('-wal', '-shm')):
                print('Note: copying SQLite WAL files from stopped container')
            for name in ('netops.sqlite3', 'netops.sqlite3-wal', 'netops.sqlite3-shm'):
                if (old_data / name).exists(): shutil.copy2(old_data / name, data / name)
            if (old_data / 'dashboard.json').exists(): shutil.copy2(old_data / 'dashboard.json', data / 'dashboard.json')
            print('Preserved old v3 accounts and dashboards.')
        elif (old_data / 'dashboard.json').exists():
            import_json = json.loads((old_data / 'dashboard.json').read_text())
            if not isinstance(import_json, dict) or not isinstance(import_json.get('assets'), list):
                raise RuntimeError('Legacy v2 JSON missing inventory assets; refusing import')
            if not new_db.exists(): raise RuntimeError('Native SQLite DB missing; install and start native app once before migrating')
            with sqlite3.connect(new_db) as conn:
                row = conn.execute("select state_json from dashboards where id='shared-operations'").fetchone()
                if not row or row[0] not in (None, 'null', '{}'):
                    raise RuntimeError('Native shared board has data; refusing overwrite')
                conn.execute("update dashboards set state_json=?, revision=revision+1 where id='shared-operations'",
                             (json.dumps(import_json, separators=(',', ':')),))
            shutil.copy2(old_data / 'dashboard.json', data / 'dashboard.json')
            print('Migrated v2 inventory to Operations · Shared. Admin password is native Umbrel app password.')
        else:
            raise RuntimeError('No old netops.sqlite3 or dashboard.json in data volume')
        dest_ssh = data / 'ssh-admin'
        if old_ssh.exists():
            copy_contents(old_ssh, dest_ssh)
            print('Copied legacy admin SSH host keys / config.')
        # Native container runs uid/gid 10001. Restrict SSH-key permissions.
        for root, dirs, files in os.walk(data):
            os.chown(root, 10001, 10001)
            for n in dirs:
                os.chown(os.path.join(root, n), 10001, 10001)
            for n in files:
                file = os.path.join(root, n)
                if not os.path.islink(file): os.chown(file, 10001, 10001)
        if dest_ssh.exists():
            os.chmod(dest_ssh, 0o700)
            for f in dest_ssh.iterdir():
                if f.is_file() and (f.name.startswith('id_') or f.name == 'config'): os.chmod(f, 0o600)
        run('umbreld', 'client', 'apps.restart.mutate', '--appId', APP_ID)
        print('Migration complete. Old Docker volumes were NOT deleted. Backup:', archive)
    except Exception:
        print('Migration failed. Native data backup is at ' + str(archive), file=sys.stderr)
        print('Restore that archive with the native app stopped before retrying.', file=sys.stderr)
        try: run('umbreld', 'client', 'apps.restart.mutate', '--appId', APP_ID)
        except Exception: pass
        raise

if __name__ == '__main__':
    try: main()
    except Exception as e:
        print('ERROR:', e, file=sys.stderr)
        sys.exit(1)
