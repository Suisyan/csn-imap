import os,json
from pathlib import Path
root=Path.home()/'csn-dsync-lab'
root.mkdir(mode=0o700,exist_ok=True)
config='''protocols =
ssl = no
log_path = /dev/stderr
info_log_path = /dev/stderr
mail_home = %s/home
mail_location = maildir:%s/Maildir
mail_uid = %d
mail_gid = %d
mail_plugins =
first_valid_uid = 1
namespace inbox {
 inbox = yes
 separator = /
 prefix =
}
'''%(root,root,os.getuid(),os.getgid())
(root/'home').mkdir(mode=0o700,exist_ok=True)
(root/'dovecot.conf').write_text(config)
print(json.dumps({'lab':str(root),'uid':os.getuid(),'production_modified':False}))
