"""Relay only a local Unix socket and standard streams; no TCP listener."""
import os,socket,sys,threading
from pathlib import Path
path=str(Path(__file__).resolve().parent/'relay.sock')
sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
if sys.argv[1]=='listen':
 if os.path.lexists(path):raise SystemExit('Socket exists; inspect previous test')
 sock.bind(path);os.chmod(path,0o600);sock.listen(1)
 print('READY',file=sys.stderr,flush=True)
 conn,_=sock.accept();sock.close();sock=conn
else:sock.connect(path)
def receive():
 while True:
  data=sock.recv(65536)
  if not data:break
  offset=0
  while offset<len(data):offset+=os.write(1,data[offset:])
 os.close(1)
t=threading.Thread(target=receive);t.start()
try:
 while True:
  data=os.read(0,65536)
  if not data:break
  sock.sendall(data)
 sock.shutdown(socket.SHUT_WR)
 t.join(60)
finally:
 sock.close()
 if sys.argv[1]=='listen':os.unlink(path)
