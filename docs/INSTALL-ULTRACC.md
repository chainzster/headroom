# Installing Headroom on Ultra.cc

These steps install Headroom entirely inside your home directory. They need no
root access, no `sudo` and no Docker. For Ultra.cc's general rules on custom
applications, see their [Generic Software Installation](https://docs.ultra.cc/unofficial-application-installers/generic-software-installation) guide.

> Headroom is a custom third-party application. Ultra.cc staff do not support it.

## 1. Connect over SSH

```bash
ssh <username>@<hostname>.usbx.me
```

## 2. Check the prerequisites

```bash
python3 --version   # 3.8 or newer
quota -s            # should print your space used and quota
```

If `python3` is missing or older than 3.8, install a newer Python with
Ultra.cc's Python installation guide (pyenv), then use that Python in step 5
(`PYTHON=~/.pyenv/shims/python3 bash install.sh`).

`quota -s` is the source of truth for your allocation. Do **not** use `df`:
it reports the whole shared server disk, not your plan.

## 3. Choose a port

Use a port from the range assigned to your service. **Using a port outside
your range is prohibited.** To list free ports in your range:

```bash
app-ports free
```

If that command isn't available on your service, look up your port range in
the User Control Panel (UCP). The examples below use `12345`.

## 4. Get the code

```bash
cd ~
git clone https://github.com/chainzster/headroom.git
cd headroom
```

The repository is public, so no GitHub login or key is needed. Prefer
`git clone` over copying the files by SFTP: it makes updating a one-line
`git pull`.

## 5. Run the installer

```bash
bash install.sh 12345 /headroom
```

Run it with `bash` as shown. `./install.sh` fails with "Permission denied" if
the executable bit was lost in transfer or your home is mounted `noexec`.

The installer:

1. creates a virtualenv in `~/headroom/.venv` and installs Flask and Waitress;
2. writes `~/.config/headroom/config.ini` with your port and base URL (an
   existing config is never overwritten);
3. installs `~/.config/systemd/user/headroom.service`;
4. runs `python -m headroom --check` to read the quota once;
5. enables and starts the service.

It is safe to re-run at any time.

### Manual installation (alternative)

```bash
cd ~/headroom
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

mkdir -p ~/.config/headroom ~/.local/share/headroom ~/.config/systemd/user
cp config.example.ini ~/.config/headroom/config.ini
nano ~/.config/headroom/config.ini          # set port and base_url

.venv/bin/python -m headroom --check        # verify config and quota
cp deploy/headroom.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now headroom.service
```

The unit file assumes the code lives in `~/headroom`. If you cloned it
somewhere else, edit `WorkingDirectory` and `ExecStart`.

## 6. (Recommended) Turn on a password

Once the Nginx proxy is set up, anyone who knows the URL can reach Headroom.
To require a login:

```bash
~/headroom/.venv/bin/python -m headroom --hash-password
```

Paste the **entire** output line, including the `scrypt:32768:8:1$` prefix,
into `~/.config/headroom/config.ini`. Don't add quotes. The prefix tells
Headroom how the hash was made; without it every login fails.

```ini
[auth]
username = me
password_hash = scrypt:32768:8:1$...
```

Then restart Headroom: `systemctl --user restart headroom`.

## 7. Publish over HTTPS with Ultra.cc's Nginx

```bash
sed -e 's|12345|YOUR_PORT|' ~/headroom/deploy/headroom.nginx.conf \
    > ~/.apps/nginx/proxy.d/headroom.conf
app-nginx restart
```

Open `https://<username>.<hostname>.usbx.me/headroom/`. Keep the trailing
slash: the Nginx `location` only matches `/headroom/`.

You only need to do this once. It doesn't need repeating after updates or
config changes, unless you change the port or `base_url`.

The `location` path in the Nginx file must match `base_url` in
`config.ini`. To use a different path, such as `/storage`, change both.

`proxy_pass` must **not** end in a path (`http://127.0.0.1:PORT`, not
`http://127.0.0.1:PORT/`). This makes Nginx forward the full `/headroom/...`
path, and Headroom removes the prefix itself.

## Managing the service

Always include `--user`. Without it, `systemctl` tries to use the system-wide
service manager, which needs root.

```bash
systemctl --user status headroom
systemctl --user restart headroom
systemctl --user stop headroom
systemctl --user disable --now headroom
```

Logs:

```bash
tail -f ~/.local/share/headroom/headroom.log   # application log (rotated)
tail -f ~/.local/share/headroom/service.log    # startup errors / crashes
```

## Updating

You never need to repeat the whole install.

**Config changes** (thresholds, password, etc.): edit
`~/.config/headroom/config.ini`, then restart:

```bash
systemctl --user restart headroom
```

**New code:** pull and restart:

```bash
cd ~/headroom && git pull && systemctl --user restart headroom
```

If `requirements.txt` changed, run `bash install.sh` instead of the restart.
It updates the virtualenv and restarts the service, and never overwrites your
config.

Your config and history are stored outside the code directory, so updates keep
them.

If you first copied the files by SFTP and `git pull` complains about local
changes, make the copy match GitHub exactly (this keeps your config, history
and `.venv`, but discards any edits made to the code on the server):

```bash
git config core.fileMode false
git fetch && git reset --hard origin/main
```

## Uninstalling

```bash
systemctl --user disable --now headroom
rm ~/.config/systemd/user/headroom.service
systemctl --user daemon-reload
rm -f ~/.apps/nginx/proxy.d/headroom.conf && app-nginx restart
rm -rf ~/headroom ~/.config/headroom ~/.local/share/headroom
```
