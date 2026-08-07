#!/usr/bin/env bash
# VPS provisioning — Agent H (agents/AGENT_H_DEPLOY.md task 5).
#
# Vendor-neutral: any plain Debian/Ubuntu box with a public IP works, no
# cloud-specific step anywhere in this script. NOT YET RUN AGAINST A REAL
# SERVER — CONTRACTS.md task 5 says to confirm host and domain with Anuraag
# before provisioning anything, and neither exists yet (agents/README.md:
# "A VPS and a domain — not urgent"). This is the exact, ready-to-run script
# for when they do; see HANDOVER.md.
#
# Usage (as root, once, on a fresh box):
#   scp -r install/vps root@<ip>:/root/vps-setup
#   ssh root@<ip>
#   cd /root/vps-setup && bash setup.sh
#
# What it does, in order:
#   1. creates a non-root user (itemcode) to actually run the app
#   2. installs Caddy (TLS reverse proxy) and Python 3
#   3. firewalls everything except 22 (until step 5) and 443
#   4. installs the systemd unit, enables it
#   5. hardens SSH: key-only, no root login, no password auth
#
# After this script: `git clone`/`scp` the app itself into
# /opt/itemcodestudio (owned by the itemcode user), run `python3 seed.py`
# once as that user, then `systemctl start itemcodestudio`.

set -euo pipefail

APP_USER="itemcode"
APP_DIR="/opt/itemcodestudio"

if [ "$(id -u)" -ne 0 ]; then
    echo "run this as root (it's a one-time box setup script)" >&2
    exit 1
fi

echo "==> creating non-root service account: $APP_USER"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi
mkdir -p "$APP_DIR/data" "$APP_DIR/exports"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> installing Python 3 and firewall tooling"
apt-get update -y
apt-get install -y python3 python3-pip ufw debian-keyring debian-archive-keyring apt-transport-https curl gnupg

echo "==> installing Caddy (TLS terminator)"
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update -y
apt-get install -y caddy
cp "$(dirname "$0")/Caddyfile" /etc/caddy/Caddyfile
echo "    edit /etc/caddy/Caddyfile's domain line before reloading Caddy"

echo "==> firewall: deny everything except SSH (22) and HTTPS (443)"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> installing the systemd unit"
cp "$(dirname "$0")/itemcodestudio.service" /etc/systemd/system/itemcodestudio.service
systemctl daemon-reload
echo "    (not starting yet — the app itself isn't deployed to $APP_DIR until you copy it there)"

cat <<'EOF'

==> SSH hardening — read before running, this can lock you out
    The commands below disable password auth entirely. Confirm you can log
    in with an SSH KEY first (test it in a second terminal, do not close
    this session until you have):

      sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/'  /etc/ssh/sshd_config
      sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/'                /etc/ssh/sshd_config
      systemctl reload sshd

    Run those two sed lines + reload yourself, once you've confirmed key
    login works — deliberately not automatic in this script, because an
    unattended SSH lockout on a box nobody else has a login to is a much
    worse failure than a slightly manual step here.

==> next steps
    1. copy the application into /opt/itemcodestudio (owned by itemcode)
    2. edit /opt/itemcodestudio/config.json:
         - "host": "127.0.0.1"   (Caddy is what's public, not this process)
         - "tls": true           (turns the session cookie's Secure flag on
                                   — core/auth.py's _tls_configured(); leave
                                   this false only on a plain-HTTP tier 2)
         - "ledger": {"mode": "server", ...}
    3. edit /etc/caddy/Caddyfile — put the real domain in, then:
         systemctl reload caddy
    4. as the itemcode user: cd /opt/itemcodestudio && python3 seed.py
    5. systemctl enable --now itemcodestudio
    6. confirm: curl https://<domain>/api/v1/health
    7. python3 manage.py adduser <you> --admin   (then log in and set the
       LLM/ERPNext keys from the Settings screen — never in config.json)
EOF
