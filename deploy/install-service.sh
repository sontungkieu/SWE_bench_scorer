#!/usr/bin/env bash
# Run on the existing GCP VM after extracting this repository into a release dir.
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
app_root="$HOME/apps/swebench-web"
config_root="$HOME/.config/swebench-web"
username=$(id -un)
mkdir -p "$app_root/state" "$config_root"
chmod 700 "$app_root/state" "$config_root"
if [[ ! -f "$config_root/config.json" ]]; then
    install -m 600 "$source_dir/deploy/config.example.json" "$config_root/config.json"
fi
if [[ ! -f "$config_root/modal.toml" ]]; then
    echo 'Missing scoped Modal credentials. Configure them without putting secrets in the repository.' >&2
    exit 1
fi
chmod 600 "$config_root/modal.toml"
if [[ ! -x "$app_root/runtime/venv/bin/python" ]]; then
    echo 'Create the remote client runtime before installing the service.' >&2
    exit 1
fi
ln -sfn "$source_dir" "$app_root/current"
cat > "$config_root/service.env" <<EOF
SWE_WEB_CONFIG=$config_root/config.json
SWE_WEB_STATE=$app_root/state
MODAL_CONFIG_PATH=$config_root/modal.toml
MODAL_PROFILE=phamvanvuhoan
PYTHONUNBUFFERED=1
EOF
chmod 600 "$config_root/service.env"
unit_file=$(mktemp)
trap 'rm -f -- "$unit_file"' EXIT
cat > "$unit_file" <<EOF
[Unit]
Description=SWE-bench private upload and evaluation dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$username
WorkingDirectory=$app_root/current
EnvironmentFile=$config_root/service.env
ExecStart=$app_root/runtime/venv/bin/python $app_root/current/webapp.py --port 8765
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$app_root/state
MemoryMax=384M
CPUQuota=50%
KillMode=control-group
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
sudo -n install -m 644 "$unit_file" /etc/systemd/system/swebench-web.service
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now swebench-web.service
sudo -n systemctl restart swebench-web.service
systemctl is-active swebench-web.service
