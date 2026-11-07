#!/usr/bin/python3
"""Fixed one-way backup source. Stdout is dsync protocol, never a user report."""
import os,pwd,socket,stat,subprocess,sys,tempfile,threading,resource
from pathlib import Path

def main():
 resource.setrlimit(resource.RLIMIT_CORE,(0,0));os.umask(0o077)
 lab=os.geteuid()!=0 and sys.argv[1:]==['--lab']
 if not lab and (os.geteuid()!=0 or sys.argv[1:]):raise RuntimeError('Invalid invocation')
 if lab:
  owner=pwd.getpwuid(os.getuid());config=Path.home()/'csn-dsync-lab/dovecot.conf'
  connector=Path.home()/'csn-dsync-lab/backup-connector.py';prefix=[];parent=None
 else:
  owner=pwd.getpwnam('csn');config=Path('/etc/csn-initial-source.conf')
  connector=Path('/usr/local/libexec/csn-initial-connector.py');prefix=['/sbin/runuser','-u','csn','--'];parent='/run'
  if owner.pw_uid!=1001 or owner.pw_dir!='/home/csn':raise RuntimeError('Unexpected account')
  for path in (config,connector):
   info=path.lstat()
   if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise RuntimeError('Untrusted installed file')
 with tempfile.TemporaryDirectory(prefix='csn-backup-source-',dir=parent) as directory:
  # Traversal only for the dropped-privilege CSN process; socket itself is private.
  os.chmod(directory,0o711)
  path=directory+'/stream.sock';listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
  listener.bind(path);os.chown(path,owner.pw_uid,owner.pw_gid);os.chmod(path,0o600);listener.listen(1);listener.settimeout(30)
  args=prefix+['/usr/bin/doveadm','-c',str(config),'backup','-f','/usr/bin/python3',str(connector),path]
  with tempfile.TemporaryFile() as diagnostic:
   process=subprocess.Popen(args,stdout=diagnostic,stderr=diagnostic)
   conn=None;thread=None
   try:
    conn,_=listener.accept();listener.close()
    def receive():
     try:
      while True:
       data=conn.recv(65536)
       if not data:break
       pos=0
       while pos<len(data):pos+=os.write(1,data[pos:])
     finally:os.close(1)
    thread=threading.Thread(target=receive);thread.daemon=True;thread.start()
    while True:
     data=os.read(0,65536)
     if not data:break
     conn.sendall(data)
    conn.shutdown(socket.SHUT_WR);thread.join(120)
    code=process.wait(timeout=120)
    if code:
     diagnostic.seek(0)
     sys.stderr.buffer.write(b'Source diagnostic (captured privately by transfer runner):\n'+diagnostic.read(65536))
     sys.stderr.buffer.flush()
    print('Fixed one-way source exit='+str(code),file=sys.stderr)
    return code
   except Exception:
    diagnostic.seek(0)
    sys.stderr.buffer.write(b'Source startup diagnostic (private):\n'+diagnostic.read(65536))
    sys.stderr.buffer.flush()
    raise
   finally:
    if process.poll() is None:process.terminate();process.wait(timeout=15)
    if conn:conn.close()
    listener.close()

if __name__=='__main__':
 try:sys.exit(main())
 except Exception:
  print('One-way source stopped; diagnostics suppressed.',file=sys.stderr);sys.exit(1)
