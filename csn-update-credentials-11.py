#!/usr/bin/python3
"""Update the inactive credential importer and template; never fetch mail."""
import datetime, hashlib, json, os, pwd, shutil, stat, subprocess, tempfile
from pathlib import Path

BASE=Path('/home/mailadmin/csn-migration')
HELPER=Path('/usr/local/sbin/csn-import-credentials')
TEMPLATE=Path('/home/fetchcsn/.fetchmailrc.template')
MANIFEST=Path('/etc/csn-mail/bundle-sha256.json')
OLD_HELPER='febb426e32edd807cac37c29c8968c572597f221e2a5fbdd50f2a0fcb5ecc0f6'
OLD_TEMPLATE='4de42ffb5ee3a541075f696bffc08321c2f54f439555b94c6c863a6184e043d1'
NEW_HELPER='65aed2291b7d6a0952222aae35bb2180672a255f09ec0d5cc2606b7ffe52578d'
NEW_TEMPLATE='bd5b1b19eeb3307a8bfc68c53528f4bc7ade4aaefb1ccfa157d83232ba8de580'

def read(path, expected=None):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise RuntimeError('Not a regular file: '+str(path))
    data=path.read_bytes()
    if expected and hashlib.sha256(data).hexdigest()!=expected:
        raise RuntimeError('Hash mismatch: '+str(path))
    return data

def inactive():
    for unit in ('fetchmail-csn.service','fetchmail-csn.timer'):
        result=subprocess.run(['/usr/bin/systemctl','show',unit,'-p','ActiveState','-p','UnitFileState'],capture_output=True,check=True,text=True)
        state=dict(line.split('=',1) for line in result.stdout.splitlines())
        if state.get('ActiveState')!='inactive' or (unit.endswith('.timer') and state.get('UnitFileState')!='disabled'):
            raise RuntimeError('Mail collection must remain disabled')
    for path in ('/etc/csn-mail/enable-fetch','/home/fetchcsn/.fetchmailrc'):
        if os.path.lexists(path):
            raise RuntimeError('Activation or credential file already exists')

def replace(path,data,mode,uid,gid):
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.update-',dir=str(path.parent))
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchown(stream.fileno(),uid,gid);os.fchmod(stream.fileno(),mode)
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def main():
    if os.geteuid()!=0:raise RuntimeError('Run with sudo python3')
    os.umask(0o077);inactive()
    helper=read(BASE/'csn-import-credentials.py',NEW_HELPER)
    template=read(BASE/'delivery-draft/fetchmailrc.template',NEW_TEMPLATE)
    read(HELPER,OLD_HELPER);read(TEMPLATE,OLD_TEMPLATE)
    manifest=json.loads(read(MANIFEST))
    if manifest.get('fetchmailrc.template')!=OLD_TEMPLATE:
        raise RuntimeError('Unexpected installed manifest')
    manifest['fetchmailrc.template']=NEW_TEMPLATE
    owner=pwd.getpwnam('fetchcsn')
    backup=Path('/var/backups')/('csn-credentials-11-before-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    original={}
    for path in (HELPER,TEMPLATE,MANIFEST):
        info=path.stat();original[path]=(path.read_bytes(),stat.S_IMODE(info.st_mode),info.st_uid,info.st_gid)
        shutil.copy2(path,backup/path.name)
    try:
        inactive()
        replace(HELPER,helper,0o700,0,0)
        replace(TEMPLATE,template,0o600,owner.pw_uid,owner.pw_gid)
        replace(MANIFEST,(json.dumps(manifest,indent=2)+'\n').encode(),0o600,0,0)
        read(HELPER,NEW_HELPER);read(TEMPLATE,NEW_TEMPLATE);inactive()
    except Exception:
        for path,args in original.items():replace(path,*args)
        raise
    print('11-account credential helper/template update complete.')
    print('Backup: '+str(backup))
    print('No credentials installed. Mail collection remains disabled.')

if __name__=='__main__':main()
