# Private GCP deployment

Web binds **127.0.0.1:8765 only** on the existing VM. Access through SSH:

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18765:127.0.0.1:8765 Free-tierGCP
```

Open <http://127.0.0.1:18765> and keep the SSH process running. The VM keeps
the web worker and SQLite queue running when a browser tab closes. No public
HTTP listener or new VM is required. SSH authenticates access; this build is
not a publicly exposed multi-user service.

App layout on the VM:

- `~/apps/swebench-web/releases/<commit>/`: source only, current release symlink.
- `~/apps/swebench-web/runtime/venv/`: Modal client 1.6.2; Python already on VM.
- `~/apps/swebench-web/state/`: SQLite queue, normalized inputs, logs, artifacts.
- `~/.config/swebench-web/`: config.json, service.env, scoped modal.toml; 0700
  directory, 0600 files. **Never copy these files into Git or browser code.**

Only the `phamvanvuhoan` Modal profile is configured for this service. No GCP
or Modal credential is passed into a grading VM. Config example contains no
tokens. The scoped TOML must be provisioned separately via a secure channel.
The installer preserves an existing config and fails if credentials are absent.

Runtime preparation happens remotely, with no Docker/harness on the GCP host:

```bash
app_root="$HOME/apps/swebench-web"
mkdir -p "$app_root/runtime"
python3 -m venv --without-pip "$app_root/runtime/venv"
curl -fsSL https://bootstrap.pypa.io/pip/pip.pyz -o "$app_root/runtime/pip.pyz"
"$app_root/runtime/venv/bin/python" "$app_root/runtime/pip.pyz" install modal==1.6.2
bash deploy/install-service.sh
```

The service is managed with `sudo systemctl status|restart|stop swebench-web`.
Install/restart only when no job is running. Restarted in-flight jobs become
`interrupted`; they are **not automatically resubmitted**. Any detached Modal VM
still has a finite lifetime. Queued jobs remain durable and run in sequence.

The web guard obtains fresh Modal billing before launch, keeps a 10-minute
reservation for recent terminal estimates, and fails closed when billing is
unavailable or the projected total exceeds configured `$29/calendar month`.
This is a local safety policy, not verified remaining credits or a provider hard
cap. A smoke/mock reserves $0.50; full reserves the configured $20 estimate.
Full requires 500 rows and explicit confirmation in the browser; the actual
dataset IDs are checked against pinned Verified data inside Modal.

Uploads are UTF-8 JSON/JSONL/CSV, maximum 20 MiB. Each file must use one model
name and unique Verified instance IDs. CSV cells containing multiline patches
must be correctly quoted. File paths are used only by the CLI; the web accepts
file uploads and never reads arbitrary client-supplied server paths.

The initial deployment uses an existing `e2-micro` in `us-west1-b` (project
`coherent-vim-457802-j4`). Eligibility and existing IP/disk/network charges remain
subject to the account's actual billing. Hosting a web page does not make Modal
compute free. Check [Google Free Tier](https://docs.cloud.google.com/free/docs/free-cloud-features)
and the current account billing when changing resources.
