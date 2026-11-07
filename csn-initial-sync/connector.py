#!/usr/bin/python3
"""Connect doveadm's command transport to the source wrapper's Unix socket."""
import os,socket,sys,threading
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.connect(sys.argv[1])
def receive():
 try:
  while True:
   data=s.recv(65536)
   if not data:break
   pos=0
   while pos<len(data):pos+=os.write(1,data[pos:])
 finally:os.close(1)
t=threading.Thread(target=receive);t.start()
try:
 while True:
  data=os.read(0,65536)
  if not data:break
  s.sendall(data)
 s.shutdown(socket.SHUT_WR);t.join(120)
finally:s.close()
