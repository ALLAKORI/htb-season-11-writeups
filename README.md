# Hack The Box — Season 11 Writeups

Private technical notes for Hack The Box Season 11 machines solved by Kossi Richard Allado.

> **Private repository:** these notes may contain active-machine solutions, credentials, flags, target addresses, and complete exploitation chains. Do not make this repository public until every documented machine has retired.

## Machines

| Machine | OS | Difficulty | Status | Writeup |
| --- | --- | --- | --- | --- |
| Reactor | Linux | Easy | Active — private only | [Read](Reactor/README.md) |
| DevHub | Linux | Medium | Active — private only | [Read](DevHub/README.md) |
| Connected | Linux | Easy | Active — private only | [Read](Connected/README.md) |
| Checkpoint | Windows | Medium | Active — private only | [Read](Checkpoint/README.md) |
| Enigma | Linux | Easy | Active — private only | [Read](Enigma/README.md) |
| Paperwork | Linux | Easy | Active — private only | [Read](Paperwork/README.md) |
| MakeSense | Linux | Medium | Active — private only | [Read](MakeSense/README.md) |
| Bedside | Linux | Medium | Active — private only | [Read](Bedside/README.md) |

## Vulnerability index

| Machine | CVEs / vulnerability names used | Stage |
| --- | --- | --- |
| Reactor | CVE-2025-55182 React2Shell / React Server Components RCE; CVE-2025-66478 Next.js RSC advisory; Node.js Inspector misconfiguration | Initial access; root privilege escalation |
| DevHub | CVE-2026-23744 MCPJam Inspector RCE; exposed Jupyter token; hardcoded OPSMCP API key and hidden `ops._admin_dump` tool | Initial access; lateral movement; root privilege escalation |
| Connected | CVE-2025-57819 FreePBX Endpoint Manager SQL injection to RCE; root-owned Incron / writable DAHDI config misconfiguration | Initial access; root privilege escalation |
| Checkpoint | Active Directory object recovery, ACL abuse, dMSA / BadSuccessor abuse, memory forensics, Pass-the-Hash | Initial access; privilege escalation |
| Enigma | CVE-2026-38751 OpenSTAManager module-upload RCE PoC; related CVE-2025-69212 OpenSTAManager command-injection context; CVE-2026-27626 OliveTin password argument command injection | Foothold; root privilege escalation |
| Paperwork | LPD command injection; PJL path traversal and arbitrary file write; `SCM_RIGHTS` file descriptor leak; password reuse | Initial access; user escalation; root privilege escalation |
| MakeSense | Hardcoded client-side encryption key; stored XSS; WordPress administrator account creation; PHP reverse shell; credential reuse; internal OCR-to-PHP root RCE | Initial access; lateral movement; root privilege escalation |
| Bedside | CVE-2025-64512 pdfminer.six pickle deserialization RCE; internal development-server path traversal; PyTorch checkpoint deserialization through `torch.load()`; shared datastore permission boundary failure | Initial access; container-to-host pivot; root privilege escalation |

## Writeup standard

Each machine document follows a consistent structure: machine information, executive summary, CVE/vulnerability mapping where relevant, reproducible exploitation steps, attack-chain summary, lessons learned, remediation guidance, and flag status. Commands and evidence are kept in fenced blocks, while active-machine material remains private.

## Publication policy

- Keep active-machine material private.
- Recheck retirement status before publishing any writeup.
- Redact flags and environment-specific addresses when appropriate.
- Publish only in accordance with Hack The Box rules.
