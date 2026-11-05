"""Host-side update smoke tests with mocked Umbrel CLI (no Docker required)."""
from pathlib import Path
import os, shutil, subprocess, tempfile, zipfile

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'netopslab-command-center'
UPDATER=ROOT/'scripts/update-netops.py'

def run():
    with tempfile.TemporaryDirectory(prefix='netops-updater-test-') as base:
        root=Path(base)
        dest=root/'installed-app'
        shutil.copytree(APP,dest)
        # Simulate data containing secrets that must survive updates.
        (dest/'data').mkdir()
        (dest/'data'/ 'netops.sqlite3').write_bytes(b'PRESERVE_DATABASE_CONTENT')
        source=root/'source'
        shutil.copytree(APP, source)
        p=source/'app/server.py'
        p.write_text('# TEST_NEW_VERSION\n'+p.read_text())
        log=root/'umbreld.log';bin=root/'bin';bin.mkdir()
        mock=bin/'umbreld'
        mock.write_text('''#!/bin/sh\necho "$*" >> "$NETOPS_TEST_LOG"\nif [ "$*" = "client apps.restart.mutate --appId netopslab-command-center" ] && [ -e "$NETOPS_TEST_FAIL" ]; then\n rm "$NETOPS_TEST_FAIL"; exit 7\nfi\nexit 0\n''')
        mock.chmod(0o755)
        env={**os.environ,'PATH':str(bin)+':'+os.environ['PATH'],'NETOPS_TEST_LOG':str(log),'NETOPS_TEST_FAIL':str(root/'failonce')}
        def call(*args, success=True):
            r=subprocess.run(['python3',str(UPDATER),'--app-dir',str(dest),'--backup-dir',str(root/'backups'),*args],env=env,capture_output=True,text=True)
            if success: assert r.returncode==0,(r.stdout,r.stderr)
            else: assert r.returncode!=0,(r.stdout,r.stderr)
            return r
        # Validation-only does not stop the running service.
        call('--local',str(source),'--dry-run')
        assert not log.exists()
        assert not (root/'backups').exists()
        # Apply update and verify preserved SQLite and backup archive.
        call('--local',str(source))
        assert 'TEST_NEW_VERSION' in (dest/'app/server.py').read_text()
        assert (dest/'data/netops.sqlite3').read_bytes()==b'PRESERVE_DATABASE_CONTENT'
        assert len(list((root/'backups').glob('*.tar.gz')))==1
        assert 'client apps.stop.mutate' in log.read_text()
        assert 'client apps.restart.mutate' in log.read_text()
        print('PASS updater dry-run, local directory update, backup, data preservation, Umbrel lifecycle')
        # A ZIP containing a nested store folder is accepted.
        z=root/'release.zip'
        with zipfile.ZipFile(z,'w') as bundle:
            for f in APP.rglob('*'):
                if f.is_file(): bundle.write(f,'netops-umbrel-store/netopslab-command-center/'+str(f.relative_to(APP)))
        call('--local',str(z),'--dry-run')
        print('PASS updater local ZIP nesting, safe structure validation')
        # Simulate a failed restart. The original code is restored.
        (root/'failonce').touch()
        call('--local',str(source),'--dry-run')
        before=(dest/'app/server.py').read_bytes()
        call('--local',str(z),success=False)
        assert (dest/'app/server.py').read_bytes()==before
        assert (dest/'data/netops.sqlite3').read_bytes()==b'PRESERVE_DATABASE_CONTENT'
        print('PASS updater rollback after failed Umbrel restart')
        # Zip-slip payload must never be extracted.
        bad=root/'malicious.zip'
        with zipfile.ZipFile(bad,'w') as zf:
            zf.writestr('../escape.txt','nope')
        call('--local',str(bad),'--dry-run',success=False)
        assert not (root/'escape.txt').exists()
        print('PASS updater rejects path traversal ZIP')

if __name__=='__main__':run()
