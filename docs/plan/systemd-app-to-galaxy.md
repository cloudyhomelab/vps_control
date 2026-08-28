# Publishing the `systemd_app` role to Ansible Galaxy

What remains to be done to make `ansible/roles/systemd_app` usable outside this repository.
Every file:line reference was verified against commit 6a0befd; paths are relative to
`ansible/roles/systemd_app/` unless stated.

Publish as a **collection**, not a standalone role: `binarycodes.homelab`, keeping the role
name `systemd_app` and its `systemd_app_` variable prefix. The role's filters
(`ansible/filter_plugins/systemd_app.py`) are the deciding factor — they are loaded by
`ansible.cfg`'s `filter_plugins = ./filter_plugins` (ansible.cfg:19), which no consumer will
have, and only a collection can both host them properly (`plugins/filter/`) and declare the
`community.sops` dependency they sit alongside.

## Blockers — the role is wrong or silently broken outside this repo

1. **Make the filter calls FQCNs.** Five call sites use bare names: `secret_digests`
   (defaults/main.yml:126), `route_problems` (tasks/main.yml:52), `container_problems`
   (tasks/main.yml:68), `reconcile_secrets` (tasks/present.yml:277), `systemd_env_lines`
   (templates/inline.container.j2:13). Each becomes `binarycodes.homelab.<name>`. Note
   defaults/main.yml:126 in particular — a *default variable* that invokes a custom filter, so
   the role fails to even resolve its vars if the plugin is not found. The `collections:` play
   keyword shortens module and role names but is not a reliable shortcut for Jinja filter and
   test plugins, so these must be written out in full.

2. **Stop naming a sibling app in the defaults.** `systemd_app_caddy_confd`
   (defaults/main.yml:75) defaults to `{{ systemd_app_root }}/reverse_proxy/config/conf.d` —
   composed from the root, but still built around the name of another app in *this* playbook,
   and it is the one directory this role writes into that another app owns. Same for
   `systemd_app_network: web.network` (defaults/main.yml:87), which names
   `apps/shared/quadlet/web.network`. Both become explicit, un-defaulted (or neutrally
   defaulted) inputs — the reverse proxy's location is a property of the consumer's fleet, not
   of this role.

3. **Make the route snippet overridable.** `templates/caddy-site.caddy.j2:4` sets
   `disable_http_challenge`, a site-specific TLS choice (no inbound :80) that a reusable role
   must not make. Add `systemd_app_route_template: caddy-site.caddy.j2` and template from that
   variable so a consumer can point at their own.

4. **Remove the `meta: flush_handlers` calls** (tasks/present.yml:354, tasks/absent.yml:108).
   They flush the *play's* handlers, not the role's: in someone else's play they fire unrelated
   pending handlers early, mid-converge. The behaviour is load-bearing here — daemon-reload
   must precede the start/restart — so replace it with a conditional task calling
   `daemon_reload` directly, keeping the handler only for cosmetic reloads.

## Before publishing

5. **Fill in `meta/main.yml`**: `author: infra` (:4) needs a real author, there are no
   `galaxy_tags`, `platforms` is limited to Debian/Ubuntu (nothing in the role is
   Debian-specific and Quadlet is most at home on Fedora/EL — add them), and `license: MIT` is
   claimed with **no LICENSE file anywhere in the repo**.

6. **Declare `community.sops`.** `tasks/present.yml:20` calls `community.sops.load_vars`,
   reached whenever an app ships `secrets.sops.yaml`, but `meta/main.yml:17` says
   `dependencies: []`. In the collection this goes in `galaxy.yml`'s `dependencies:`, which is
   what installs it for consumers automatically.

7. **Write YAML doc blocks for the five filters.** Collection filters are collection-global
   public API — callable by anyone who installs the collection, whether or not they use the
   role, with semver applying to their names and return shapes. `ansible-doc -t filter
   binarycodes.homelab.route_problems` surfaces them and `ansible-test sanity` expects
   `DOCUMENTATION`/`EXAMPLES`/`RETURN`. The module has prose docstrings and per-function
   comments but no doc blocks. Worth documenting as part of the contract: the two `*_problems`
   filters both return a list of human-readable problem strings and never raise, so a caller
   reports everything wrong at once.

8. **Rewrite the README app-agnostic.** It uses `whichday`, `nasplan`, `cloudyhome.org`,
    `caddy-data` and the `web` network as examples throughout (README.md:79, 295, 307, 317-348,
    454-469) and cross-references `ansible/SECRETS.md` (:390),
    `ansible/filter_plugins/systemd_app.py` (:485) and `ansible/tests/` (:487) — paths a
    consumer will not have. Needs generic examples (`myapp`, `app.example.com`) with the
    secrets and filter material inlined or dropped.

9. **State the `become` and rootful-podman contract in the README.** The role writes
    `/etc/containers/systemd`, runs `podman secret` as root and sets root-owned files, but a
    role cannot set `become` — it relies on the play's `become: true`. It is also
    rootful-podman-only by construction.

## Nice to have

10. **A molecule scenario** (podman driver, systemd-enabled container). The filters have
    pytest coverage; the role's actual behaviour — the install-manifest prune, a kind flip,
    `absent` — has none, and that coverage is what makes the extraction safe to refactor
    through.

## Target layout

```
ansible_collections/binarycodes/homelab/
  galaxy.yml            # dependencies: community.sops >=2.3.0
  meta/runtime.yml      # requires_ansible: ">=2.14"
  LICENSE
  plugins/filter/                    # from ansible/filter_plugins/systemd_app.py,
                                     # split by concern: secrets.py, validation.py, systemd.py
  roles/systemd_app/                 # from ansible/roles/systemd_app/, unchanged
  tests/unit/                        # from ansible/tests/test_systemd_app_filters.py
  extensions/molecule/default/
```

## What consumers write

```yaml
# requirements.yml
collections:
  - name: binarycodes.homelab
    version: ">=1.0.0"
  - name: community.sops        # still needed at runtime; the collection declares it too
    version: ">=2.3.0"
```

Before publishing, the same file can point at git instead:

```yaml
  - name: https://github.com/binarycodes/ansible-homelab.git
    type: git
    version: main
```

Call sites change in two ways only — the role's FQCN, and the vars the role does not default,
hoisted to play level because every app in a play shares them: `systemd_app_apps_dir`, which
this repo's playbook already sets, plus the two blocker 2 stops defaulting:

```yaml
  vars:
    systemd_app_apps_dir: "{{ playbook_dir }}/../apps"
    systemd_app_network: web.network
    systemd_app_caddy_confd: /var/app/reverse_proxy/config/conf.d

  roles:
    - role: binarycodes.homelab.systemd_app        # was: systemd_app
      systemd_app_kind: inline
      systemd_app_name: calculators
      systemd_app_image: docker.io/binarycodes/calculators:latest
      systemd_app_domain: calc.cloudyhome.org
      systemd_app_health_cmd: "wget -qO /dev/null http://127.0.0.1:8080/ || exit 1"
```

`include_role` works the same way, if a loop ever replaces the near-identical `roles:` entries:

```yaml
- name: Deploy apps
  ansible.builtin.include_role:
    name: binarycodes.homelab.systemd_app
  vars: "{{ app_definition }}"
  loop: "{{ apps }}"
  loop_control:
    loop_var: app_definition
```

## Order of work

Blockers 2-4 can land in this repo first, one commit each, keeping CI green. Blocker 1 cannot:
the FQCN form only exists once the collection does. Then:

1. Extract into the collection layout above, splitting the filter module by concern, and apply
   blocker 1.
2. Publishing metadata: `galaxy.yml`, `meta/runtime.yml`, LICENSE, `galaxy_info` (5, 6).
3. Filter doc blocks (7).
4. README rewrite (8, 9).
5. Molecule scenario (10).
6. Point this repo at the published collection: `ansible/requirements.yml`, and drop
   `filter_plugins` / `roles_path` from `ansible/ansible.cfg`. The
   `.github/workflows/deploy-application.yml` triggers currently list six paths
   (`ansible/apps/**`, `playbooks/deploy_apps.yml`, `roles/systemd_app/**`,
   `filter_plugins/**`, `ansible.cfg`, `requirements.yml`); they collapse to the apps tree,
   the playbook and the collection pin.
