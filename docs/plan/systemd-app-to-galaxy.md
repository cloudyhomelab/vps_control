# Publishing the `systemd_app` role to Ansible Galaxy — done

The role left this repository. It is published as **`binarycodes.homelab` 1.0.0**
(<https://galaxy.ansible.com/ui/repo/published/binarycodes/homelab/>), built from
<https://github.com/cloudyhomelab/ansible-homelab-collection>, and this repo consumes it:
the pin is in `ansible/requirements.yml` and every app in `playbooks/deploy_apps.yml`
names `binarycodes.homelab.systemd_app`. The role, its filter plugins, their unit tests
and the molecule scenario are gone from here, along with the `roles_path` /
`filter_plugins` lines in `ansible.cfg` that only existed to find them.

## Carried over to the collection repository

Two items from this plan did not make 1.0.0, and are the collection's to fix now. Neither
affects this fleet, whose layout is what they are still defaulted to:

- **`systemd_app_caddy_confd` (defaults) is composed from another app's name** —
  `{{ systemd_app_root }}/reverse_proxy/config/conf.d`. It is the one directory the role
  writes into that another app owns, so the reverse proxy's location belongs to the
  consumer's fleet, not to the role.
- **The route snippet is not overridable.** `templates/caddy-site.caddy.j2` sets
  `disable_http_challenge`, a site-specific TLS choice (no inbound :80) that a reusable
  role should not make for its consumers. A `systemd_app_route_template` variable would
  let a consumer point at their own.
