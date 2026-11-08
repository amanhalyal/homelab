# Adding a new device's SSH access

Password authentication is disabled on every machine ([0005](../decisions/0005-ssh-key-only-authentication.md)), so a new device needs a key in `authorized_keys` before it can connect at all.

## Steps

1. Get the device's **public** key, never the private one. It is a single line starting with `ssh-ed25519` or `ssh-rsa`. Most SSH clients, including mobile apps, can generate a keypair and export or copy the public half.
2. Append it to `~/.ssh/authorized_keys` on each machine the device needs:

   ```bash
   echo "ssh-ed25519 AAAA... device-name" >> ~/.ssh/authorized_keys
   ```

3. **Test a live login from the device before calling it done.** A key being in the file is not proof. This went wrong once: a phone's access to Citadel was assumed fine until it turned out the app was still using a password, which had just been disabled.
4. If the device needs several machines, test each one on its own. They do not share an `authorized_keys` file, so one working says nothing about the other.

## Before disabling password authentication anywhere

Ask what else logs in with a password. Machine-to-machine SSH is easy to verify because you are using it. A phone or another personal device that connects occasionally is easy to forget.
