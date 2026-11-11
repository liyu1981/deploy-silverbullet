# deploy-silverbullet

Installs or upgrades [SilverBullet](https://silverbullet.md) on a Linux box
(x86_64 / aarch64) as an unprivileged **systemd user service**.

| What        | Where                                            |
| ----------- | ------------------------------------------------ |
| Binary      | `~/.local/bin/silverbullet`                      |
| Data folder | `~/.silverbullet`                                |
| Listens on  | `http://127.0.0.1:3234`                          |
| Unit file   | `~/.config/systemd/user/silverbullet.service`    |

The client service worker is **disabled by default** (see
[Service worker](#service-worker--cloudflare-access)); pass
`--with-service-worker` to enable it.

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
--with-service-worker
                   Enable the client service worker (also: --service-worker)
--no-service-worker
                   Turn the service worker back off (the default)
```

Environment equivalents: `SB_VERSION`, `SB_HOSTNAME`, `SB_PORT`,
`SB_DATA_DIR`, `SB_BIN_DIR`, `SB_ADMIN`, `SB_WITH_SERVICE_WORKER=1`.

## Service worker / Cloudflare Access

By default the unit sets `SB_DISABLE_SERVICE_WORKER=1`, so boot skips
`navigator.serviceWorker.register()` and SilverBullet uses its plain IndexedDB
store instead.

That is deliberate: browsers refuse to register a worker whose script URL
answers with a redirect (`The script resource is behind a redirect, which is
disallowed`), and any authenticating reverse proxy in front of SilverBullet
(Cloudflare Access, oauth2-proxy, …) answers `302` for
`<prefix>/service_worker.js` unless the request already carries a session
cookie. Behind a tunnel + Access you get exactly:

```
Error: Failed to register a ServiceWorker for scope ('https://sb.example.com/notes/')
with script ('https://sb.example.com/notes/service_worker.js'):
The script resource is behind a redirect, which is disallowed.
```

Trade-offs with the worker off: no offline/PWA support, no tooling cache, and
**client-side encryption does not work** (it lives in the worker). Server-side
login still works normally.

Enable it with `--with-service-worker` when SilverBullet is directly exposed
(no auth proxy in front) and you want offline support or client-side
encryption:

```bash
./install.sh --with-service-worker   # rewrites the unit and restarts
```

Alternative, if you must keep an auth proxy *and* the worker: exempt only the
worker script from authentication in the proxy (in Cloudflare Access, a more
specific app for `host/service_worker.js` with a `bypass` policy), keeping in
mind that all of the worker's own fetches still pass through that proxy.

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
