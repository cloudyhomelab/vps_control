# App secrets

An app that needs secrets ships one SOPS-encrypted file in its own directory — for an
`inline` app that file is all the directory holds:

```
apps/whichday/
  quadlet/whichday.container    # references the secrets by name
  secrets.sops.yaml             # keys are those names; values are encrypted
```

The [systemd_app role](roles/systemd_app/README.md#secrets) finds that file the same way
it finds `quadlet/` and `config/`, decrypts it on the controller during a deploy, and
stores each entry in podman's secret store on the host. No value is written to a unit
file, a config tree, or this repository in cleartext. Keys are the podman secret names:

```yaml
# apps/whichday/secrets.sops.yaml, decrypted
whichday-oidc-issuer-uri: https://accounts.example.com
whichday-oidc-client-id: the-client-id
whichday-oidc-client-secret: the-client-secret
```

- **In git:** ciphertext only. Key *names* stay readable, so a diff shows which secret
  changed without showing the value.
- **In GitHub:** one long-lived age private key, as the `SOPS_AGE_KEY` repository secret.
- **On the host:** the values live in podman's root-only secret store.
- **In CI logs:** nothing — the decrypting task and every task handling a value run with
  `no_log`.

Understand what the age key is before relying on it: **it decrypts every one of these
files, it never expires, and its use is not logged anywhere.** That is one master key held
in GitHub's secret store — a deliberate trade for a small fleet. If it stops being the
right trade, `.sops.yaml` can point at a cloud KMS key instead and CI can reach it through
GitHub's OIDC federation with no stored credential at all, putting every decryption in that
provider's audit log. Nothing else changes: not this layout, not the playbook, not the role.

## First-time setup

1. **Generate the key** (locally, once) and keep the private half out of the repo:

   ```bash
   age-keygen -o ~/.config/sops/age/keys.txt      # prints "Public key: age1..."
   ```

2. **Record the recipient** — put that public key in `.sops.yaml` at the repo root,
   replacing the placeholder. Committing the public half is fine and expected.

3. **Add the private half to GitHub** as the `SOPS_AGE_KEY` repository secret
   (Settings → Secrets and variables → Actions): the whole `AGE-SECRET-KEY-1...` line.
   The deploy workflow passes it to `ansible-playbook`, the only place it is used.

4. **Create the file** — `sops edit ansible/apps/<app>/secrets.sops.yaml` — with one entry
   per secret the app's Quadlet references. The names have to match; a `Secret=` line
   pointing at a name that is not there makes the container fail to start.

Either kind can do this. A `source` app references the secret from its own Quadlet, so the
name and the variable are independent. An `inline` app has its `Secret=` line rendered for
it, with the variable derived from the name — upper-cased, dashes as underscores — so there
the name must be the variable the image reads, spelled in lower case with dashes
(`whichday-oidc-client-secret` → `WHICHDAY_OIDC_CLIENT_SECRET`). An image insisting on a
bare, unprefixed variable is the one case that pushes you to the `source` kind, since a
podman secret name is host-global and shared names collide.

## Rotating, renaming, removing

`sops edit` the file, commit the ciphertext, push. The deploy compares digests of what it
last stored (see the [role README](roles/systemd_app/README.md#secrets)), so:

- a **changed** value is re-created and the app restarted, because a running container
  keeps the value it was created with;
- a **renamed or deleted** key is dropped from the host, leaving no orphan secret — rename
  it in the app's Quadlet at the same time, or the container starts referencing a name that
  is gone;
- an **unchanged** file touches nothing.

## When the secrets cannot be read

The deploy **fails at the decrypting task**, with nothing on the host changed: a missing
key, the wrong key, a corrupt file. There is no fallback — an app running without its
configuration is not a better outcome than a red run.

A file that is simply absent is not an error, because that is what every app without
secrets looks like. What catches a misconfiguration then is the Quadlet itself: podman
refuses to create a container whose `Secret=` names nothing, so the unit fails to start.

## Reading a value locally

With the private key at `~/.config/sops/age/keys.txt`:

```bash
sops decrypt ansible/apps/whichday/secrets.sops.yaml
```

A local `ansible-playbook` run needs that same key present, or it stops at the decrypting
task exactly as CI does without it.

## whichday

Its OIDC client registration is treated as secret in full — issuer URI, client ID and
client secret. Register the redirect URI with the provider, or login fails after the first
hop: `https://whichday.cloudyhome.org/login/oauth2/code/oidc`.
