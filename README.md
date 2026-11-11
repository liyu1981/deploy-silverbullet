# deploy-silverbullet

Installs or upgrades [SilverBullet](https://silverbullet.md) on a Linux box
(x86_64 / aarch64) as an unprivileged **systemd user service**.

| What        | Where                                            |
| ----------- | ------------------------------------------------ |
| Binary      | `~/.local/bin/silverbullet`                      |
| Data folder | `~/.silverbullet`                                |
| Listens on  | `http://127.0.0.1:3234`                          |
| Unit file   | `~/.config/systemd/user/silverbullet.service`    |

Defaults: **single-space mode with no built-in login**, meant to sit behind an
authenticating proxy or tunnel (Cloudflare Access, oauth2-proxy, Authelia, …),
and the client **service worker enabled**. See
[Single-space mode / external login](#single-space-mode--external-login) and
[Service worker](#service-worker--auth-proxies).

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
--single           Single-space mode, no built-in login (default)
--multi-space      Multi-space mode with SilverBullet's own accounts
                   (spaces.json + users.json, set up through /.setup)
--admin <u:p>      Only with --multi-space: provision the admin account
--url-prefix <p>   Mount the space below the domain root (e.g. /notes)
--enable-shell     Allow Space Lua to run local shell commands (off by default)
--no-service-worker
                   Disable the client service worker (see below)
--with-service-worker
                   Enable the service worker (default; alias --service-worker)
```

Environment equivalents: `SB_VERSION`, `SB_HOSTNAME`, `SB_PORT`,
`SB_DATA_DIR`, `SB_BIN_DIR`, `SB_ADMIN`, `SB_SPACE_MODE=single|multi`,
`SB_URL_PREFIX`, `SB_WITH_SERVICE_WORKER=0|1`, `SB_SHELL_BACKEND=local|off`.

## Single-space mode / external login

By default the unit runs `silverbullet --single`, which means:

* no `spaces.json` / `users.json`, no built-in login page and no `/.auth`
  endpoint — the space is served to whoever can reach it;
* the gatekeeper is whatever you put in front: Cloudflare Access, an oauth2
  proxy, Authelia, a VPN, …;
* **client-side encryption is unavailable** (it needs SilverBullet's own
  username/password to derive the key). That is fine when an external proxy
  does authentication;
* shell execution from Space Lua is **off** (`SB_SHELL_BACKEND=off`). Upstream
  defaults to `local`, i.e. any command can be run from a `space-lua` block
  with an empty whitelist; with no built-in login that would be remote code
  execution for anyone who gets past the proxy. Pass `--enable-shell` if you
  rely on it, ideally together with a proxy-side access rule.

Because of that, a data folder left over from a multi-space setup
(`spaces.json`) makes the service refuse to start; the installer stops early
and tells you to either start from an empty folder or pass `--multi-space`.

If you want SilverBullet's own accounts instead:

```bash
./install.sh --multi-space
```

and complete the wizard at `/.setup/` (or pass `--admin user:pass`).

## Service worker / auth proxies

The service worker is **enabled by default**. It gives the offline cache, PWA
installation, tooling cache and client-side encryption, but browsers refuse to
register a worker whose script URL answers with a redirect:

```
Error: Failed to register a ServiceWorker for scope ('https://sb.example.com/')
with script ('https://sb.example.com/service_worker.js'):
The script resource is behind a redirect, which is disallowed.
```

Any authenticating proxy in front of SilverBullet answers `302` for those
static files unless the request carries a session cookie. The fix is to let
these two paths through unauthenticated (they only contain the public client
bundle):

* `/<prefix>/service_worker.js` (and `service_worker.js.map`)
* `/<prefix>/.client/*` (JS, CSS, fonts, icons, `manifest.json`)

With Cloudflare Access that is a more specific application with a **Bypass**
policy for those paths; everything else stays behind Google/SSO login.

Only if your proxy cannot do path exceptions, turn the worker off — at the cost
of offline support/PWA and client-side encryption:

```bash
./install.sh --no-service-worker
```

`--service-worker`/`--with-service-worker` switches it back on. Both are
recorded in the unit file (`SB_DISABLE_SERVICE_WORKER`).

## After the first start

Open <http://127.0.0.1:3234/>. In the default single-space mode there is no
login prompt; in `--multi-space` mode an empty data folder shows the setup
wizard at `/.setup/`.

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

## Migrating from Logseq

`logseq2sb/` contains a standalone converter for Logseq Markdown graphs
(pages, journals, assets, task markers, block references, macros, …):

```bash
logseq2sb/logseq2sb.py ~/logseq-graph /path/to/space-folder --dry-run
logseq2sb/logseq2sb.py ~/logseq-graph /path/to/space-folder
```

See [logseq2sb/README.md](logseq2sb/README.md) for the full mapping and the
list of things that need manual attention after the import.
