"""Legacy v2 / v3 data migration smoke tests with mocked docker and Umbrel."""
from pathlib import Path
import os, shutil, subprocess, sys, tempfile, json, sqlite3

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'netopslab-command-center'
MIGRATE=ROOT/'scripts/migrate-from-standalone.py'

def run():
    for v in (2,3):
        with tempfile.TemporaryDirectory(prefix=f'netops-migrate-v{v}-') as tmp:
            root=Path(tmp)
            native=root/'installed';shutil.copytree(APP,native)
            nd=native/'data';nd.mkdir()
            old_data=root/'old_data';old_data.mkdir()
            old_ssh=root/'old_ssh';old_ssh.mkdir()
            (old_ssh/'config').write_text('Host lab\n  HostName 192.0.2.42\n')
            init='import store; store.setup("nativePasswordLong123!")'
            env={**os.environ, 'NETOPS_DB_FILE':str(nd/'netops.sqlite3'), 'NETOPS_DATA_FILE':str(nd/'dashboard.json'), 'PYTHONPATH':str(APP/'app')}
            r=subprocess.run([sys.executable,'-c',init],env=env,capture_output=True,text=True)
            assert r.returncode==0,r.stderr
            previous={'assets':[{'id':'migrate-1','name':'Switch 1','host':'192.0.2.42'}], 'links':[],'tasks':[],'snippets':[],'notes':'','activity':[]}
            (old_data/'dashboard.json').write_text(json.dumps(previous))
            if v==3:
                env3={**env,'NETOPS_DB_FILE':str(old_data/'netops.sqlite3')}
                r=subprocess.run([sys.executable,'-c','import store; store.setup("previousPasswordLong123!")'],env=env3,capture_output=True,text=True)
                assert r.returncode==0,r.stderr
            bin=root/'bin';bin.mkdir();log=root/'ops.log'
            (bin/'docker').write_text('''#!/bin/sh
if [ "$1" = "volume" ]; then
  case "$3" in
    netops-command-center-data) printf '%s\\n' "$MOCK_OLD_DATA";;
    netops-command-center-ssh) printf '%s\\n' "$MOCK_OLD_SSH";;
    *) exit 1;;
  esac
elif [ "$1" = "inspect" ]; then echo false
else exit 4; fi
''')
            (bin/'umbreld').write_text('''#!/bin/sh
echo "$*" >> "$MOCK_OPS_LOG"
''')
            for cmd in ('docker','umbreld'):(bin/cmd).chmod(0o755)
            env.update({'PATH':str(bin)+':'+env['PATH'],'MOCK_OLD_DATA':str(old_data),'MOCK_OLD_SSH':str(old_ssh),'MOCK_OPS_LOG':str(log)})
            r=subprocess.run([sys.executable,str(MIGRATE),'--app-dir',str(native),'--backup-dir',str(root/'backups')],env=env,capture_output=True,text=True)
            assert r.returncode==0,(r.stdout,r.stderr)
            assert (nd/'ssh-admin'/'config').is_file()
            assert (root/'backups').is_dir() and any((root/'backups').glob('*.tar.gz'))
            assert 'apps.stop.mutate' in log.read_text() and 'apps.restart.mutate' in log.read_text()
            with sqlite3.connect(nd/'netops.sqlite3') as conn:
                state=conn.execute("select state_json from dashboards where id='shared-operations'").fetchone()[0]
                if v==2: assert json.loads(state)['assets'][0]['name']=='Switch 1'
                if v==3: assert conn.execute('select count(*) from users').fetchone()[0]==1
            assert (old_data/'dashboard.json').exists()
            print(f'PASS migration from v{v}, backup, inventory/DB, SSH, volume preservation')

if __name__=='__main__': run()
