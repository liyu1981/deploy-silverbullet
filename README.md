# deploy-silverbullet

Installs or upgrades [SilverBullet](https://silverbullet.md) on a Linux box
(x86_64 / aarch64) as an unprivileged **systemd user service**.

| What        | Where                                            |
| ----------- | ------------------------------------------------ |
| Binary      | `~/.local/bin/silverbullet`                      |
| Data folder | `~/.silverbullet`                                |
| Listens on  | `http://127.0.0.1:3234`                          |
| Unit file   | `~/.config/systemd/user/silverbullet.service`    |

The release asset used is `silverbullet-server-linux-<arch>.zip` (the server
build with the web UI embedded) from the latest GitHub release.

## Install

```bash
./install.sh
```

Run it as the user that should own the service — **not** with `sudo`.
The script is idempotent: running it again is the upgrade path.

On a machine without `curl`/`unzip`:

```bash
sudo apt install curl unzip      # Debian/Ubuntu
sudo dnf install curl unzip      # Fedora
```

## Upgrade

```bash
./install.sh          # installs the newest release, restarts the service
./install.sh --force  # reinstall the current version (repair)
```

If the latest release is already installed, nothing is downloaded; the unit
file and service state are still reconciled.

Pin or roll back to a specific release:

```bash
./install.sh --version 2.12.1
```

## Options

```
--version <tag>    Install a specific release tag instead of the latest
--force            Reinstall even if already current
--no-start         Install files only, don't enable/start the service
--hostname <addr>  Listen address   (default 127.0.0.1)
--port <port>      Listen port      (default 3234)
--data <dir>       Data folder      (default ~/.silverbullet)
--bin-dir <dir>    Install folder   (default ~/.local/bin)
--admin <u:p>      Provision a fresh data folder non-interactively
                   (runs `silverbullet setup --admin u:p <dir>`)
```

Environment equivalents: `SB_VERSION`, `SB_HOSTNAME`, `SB_PORT`,
`SB_DATA_DIR`, `SB_BIN_DIR`, `SB_ADMIN`.

## After the first start

Open <http://127.0.0.1:3234/> — on an empty data folder SilverBullet shows the
setup wizard at `/.setup/`.

Because it's a *user* service, the script also enables lingering
(`loginctl enable-linger $USER`) so the wiki keeps running after you log out and
starts at boot. If lingering could not be enabled automatically:

```bash
sudo loginctl enable-linger "$USER"
```

## Managing the service

```bash
systemctl --user status silverbullet
systemctl --user restart silverbullet
systemctl --user stop silverbullet
journalctl --user -u silverbullet -f
```

## Exposing it beyond localhost

The service binds to loopback by design. To reach it from another machine, put
a reverse proxy (Caddy, nginx, …) or a tunnel in front of `127.0.0.1:3234`
instead of binding publicly, e.g. with Caddy:

```
silverbullet.example.com {
    reverse_proxy 127.0.0.1:3234
}
```

## Uninstall

```bash
systemctl --user disable --now silverbullet
rm -f ~/.config/systemd/user/silverbullet.service ~/.local/bin/silverbullet
systemctl --user daemon-reload
# data is kept; remove it explicitly if you want it gone:
# rm -rf ~/.silverbullet
```
