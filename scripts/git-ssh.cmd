@echo off
"C:\Windows\System32\OpenSSH\ssh.exe" -i "%USERPROFILE%\.ssh\id_ed25519" -o UserKnownHostsFile="%USERPROFILE%\.ssh\known_hosts" -o IdentitiesOnly=yes %*
