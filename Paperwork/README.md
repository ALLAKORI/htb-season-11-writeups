# Paperwork — Hack The Box Writeup

![Platform](https://img.shields.io/badge/Platform-Hack%20The%20Box-red)
![Operating System](https://img.shields.io/badge/OS-Linux-blue)
![Difficulty](https://img.shields.io/badge/Difficulty-Easy-green)
![Category](https://img.shields.io/badge/Category-Custom%20Services-orange)

> **Active-machine material — private only. Do not publish until Paperwork has retired.**

## Machine Information

| Property | Value |
|---|---|
| Machine | Paperwork |
| Platform | Hack The Box |
| Operating System | Linux |
| Difficulty | Easy |
| Target IP | `10.129.42.46` |
| Initial Access | LPD command injection |
| User Escalation | PJL path traversal and arbitrary file write |
| Root Escalation | `SCM_RIGHTS` file descriptor leak and password reuse |

> The target IP is assigned dynamically and may change when the machine is restarted.

## Overview

**Paperwork** is an **Easy Linux machine** centered around several custom document-processing and printing services.

The exploitation chain involves:

1. Source-code disclosure through the web application.
2. OS command injection in a custom LPD service running on TCP port `1515`.
3. An initial shell as the `lp` user.
4. Discovery of an internal JetDirect/PJL service running as `archivist`.
5. Path traversal and arbitrary file write through PJL `FSDOWNLOAD`.
6. SSH key injection to obtain access as `archivist`.
7. Analysis of a custom root-owned management daemon.
8. Leakage of a privileged file descriptor through a Unix socket using `SCM_RIGHTS`.
9. Recovery of an administrative password reused by the Linux `root` account.

```text
Web source-code disclosure
        |
        v
LPD command injection on TCP/1515
        |
        v
Shell as lp
        |
        v
Internal JetDirect/PJL service on TCP/9100
        |
        v
Path traversal + arbitrary file write
        |
        v
SSH as archivist
        |
        v
SCM_RIGHTS privileged file descriptor leak
        |
        v
Administrative password disclosure
        |
        v
Password reuse through su
        |
        v
Root
```
