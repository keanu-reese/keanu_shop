# NETOPS Command Center — native Umbrel app (3.1.0)

This is an **Umbrel community App Store repository** with one native app, `netopslab-command-center`. It adds the NETOPS icon to the home screen and lets the Umbrel **owner** start, stop, restart and view logs through Umbrel's normal app controls.

The app code is the tested NETOPS v3 multi-user, shared-dashboard, browser-SSH application. The only new runtime packaging is the Umbrel manifest, Docker Compose integration and build. `scripts/update-netops.sh` is a **host-side**, intentionally manual updater for a specified GitHub ref or local folder/ZIP. There is **no privileged updater endpoint** in the web app and no Docker socket exposed to the container.

**Important:** This is a prepared app-store repository, **not an already published online store**. Publish it to a GitHub repository you control, then add that repository URL to Umbrel's *Community App Stores*. Umbrel does not show ordinary Portainer/Compose stacks as native apps. If you do not want to publish a public repo, you need to register a trusted, already accessible community store or use an Umbrel developer installation process; the files alone do not create a tile until Umbrel installs the native app.

## 1. Before installing (if NETOPS v2/v3 is already running)

Your old NETOPS standalone Compose deployment probably uses port **8765** and the named volumes `netops-command-center-data` and `netops-command-center-ssh`.

1. **Export JSON in NETOPS** using Settings & backup → Export JSON. This exports the selected dashboard only.
2. Back up the Docker named volumes if they contain SSH identities, databases or other data.
3. On the Umbrel host, stop the **old** standalone service, *not its volumes*:

   ```bash
   # From the OLD extracted NETOPS_Command_Center_Umbrel directory:
   docker compose stop
   # Do not run docker compose down -v
   ```

4. Keep the old `.env` and volumes. You can use the supplied migration script *after installing the native app* (see section 4). Do not run the old and native NETOPS servers on port 8765 concurrently.

## 2. Publish the app-store source (one time)

On your PC, put the **contents** of `NETOPS_Umbrel_Native_App` in a **public GitHub repository** you control, for example `YOUR_USER/netops-umbrel-store`. The GitHub repo **root** must contain `umbrel-app-store.yml` and a folder called `netopslab-command-center`, with `umbrel-app.yml`, `docker-compose.yml`, `Dockerfile` and `app/` inside that folder.

Example with Git installed and an **already-created empty GitHub repository**:

```bash
cd NETOPS_Umbrel_Native_App
git init
git add .
git commit -m "NETOPS native Umbrel app 3.1.0"
git branch -M main
git remote add origin https://github.com/YOUR_USER/keanu_shop.git
git push -u origin main
```

Never commit old `.env` files, SSH keys, SQLite database files, exported dashboard JSON, or NETOPS passwords. The supplied ZIP contains **application source only**.

You do **not** need to publish a separate Docker image; the Compose app has a local build context. The first installation pulls the official Python/Debian and Python package dependencies **at build time**. Docker and Umbrel must support Compose's `build` key; this is supported in current Umbrel's Compose app runner but the actual build **has not been exercised on your hardware**.

## 3. Install the native app through Umbrel

1. Open **http://umbrel.local** and choose **App Store**.
2. Select **⋯ → Community App Stores → Add**.
3. Enter `https://github.com/YOUR_USER/netops-umbrel-store` (your actual URL).
4. Open **NETOPS Lab** in the Community App Stores list. Install **NETOPS Command Center**.
5. Once installed, the NETOPS icon will be in Umbrel's home screen. Click the icon to open the dashboard.
6. To start/stop/restart, **right-click the app icon** and select the action. Only the Umbrel owner can install/change app settings; members need the app shared with them in Umbrel if Umbrel login is enforced.

**Login:** NETOPS username is **`admin`**. The initial admin password is Umbrel's **app-specific deterministic password**, shown under the Umbrel app settings → **Default credentials** (`deterministicPassword: true`), not your old standalone `.env` password. If you import an existing **v3 SQLite database**, its existing users/passwords replace the native bootstrap accounts. Change the password through NETOPS admin settings after you log in. Do **not** publish the deterministic password.

Open directly at `http://umbrel.local:8765` (or your Umbrel's LAN IP and port 8765). Umbrel owns this port and routes traffic to NETOPS; the container itself does not publish a separate host port. Embedded SSH and diagnostics run **from the Umbrel container**, and WebSocket upgrade support is required if additionally using Nginx Proxy Manager.

### CLI fallback (only after store registration)

```bash
umbreld client apps.install.mutate --appId netopslab-command-center
umbreld client apps.stop.mutate --appId netopslab-command-center
umbreld client apps.restart.mutate --appId netopslab-command-center
```

These commands are for the **native** app and should not be mixed with `docker compose` commands in the old standalone directory.

## 4. Import existing NETOPS v2 / v3 data (optional, once)

The native Umbrel app stores its database and admin SSH config under Umbrel-managed `.../app-data/netopslab-command-center/data`, while your old standalone Compose install stores them in **named Docker volumes**. Those are separate locations. **Installing the native app does not automatically reuse the old volumes.**

After you have installed and started NETOPS native once, make sure the old Compose container is stopped, then run **on the Umbrel host** (from the extracted ZIP directory, not inside the app container):

```bash
sudo ./scripts/migrate-from-standalone.sh
```

The script checks for the old `netops-command-center-data` and `netops-command-center-ssh` volumes, stops the native app, backs up the native data, copies the legacy information and SSH settings, then restarts the native app. It **does not remove the old volumes**.

- **Old v3:** copies the old SQLite database so existing users/shared dashboards/passwords are retained, provided the new native database has not already been populated with additional users or boards. The admin's SSH folder is also carried over.
- **Old v2:** imports the old JSON inventory into **Operations · Shared**; sign in with the **new native Umbrel default password**, not the previous v2 password. The admin's old SSH folder is copied.
- **If the native dashboard has already been customized:** export its data before migration. The tool refuses to overwrite a populated shared board and refuses replacing populated user/board databases.

If your Umbrel home directory differs from `/home/umbrel/umbrel`, specify the app data folder explicitly:

```bash
sudo ./scripts/migrate-from-standalone.sh \
  --app-dir /YOUR/UMBREL/DATA/app-data/netopslab-command-center
```

The backup archive includes sensitive accounts and SSH keys; keep it private. Do **not uninstall** the old Compose volumes until your migrated app is confirmed working and backups are complete.

## 5. Upgrade from a specific local folder or GitHub ref

All updates are **manual, explicit**, require host permissions, preserve persistent data, back up existing data, and restart the native Umbrel app through `umbreld`. The updater does not automatically contact GitHub or scan directories. **For an interactive source picker**, run `sudo ./scripts/update-netops.sh` in a terminal; choose **1 (GitHub)** or **2 (local ZIP/folder)**, then enter the release location. Alternatively, use the commands below for scripted updates.

Run **on Umbrel's operating-system terminal**, not the NETOPS container. Both examples can be copy/pasted as a single command:

```bash
# Option A: pinned GitHub tag or branch
sudo ./scripts/update-netops.sh \
  --github https://github.com/YOUR_USER/netops-umbrel-store \
  --ref v3.2.0

# Option B: a local folder containing app/ and Dockerfile
sudo ./scripts/update-netops.sh \
  --local /home/umbrel/netops-releases/3.2.0

# Option C: local ZIP of NETOPS source, including the v3 ZIP layout
sudo ./scripts/update-netops.sh \
  --local /home/umbrel/netops-releases/NETOPS_Command_Center_Umbrel_v3.zip

# Safety check before applying any update
sudo ./scripts/update-netops.sh \
  --local /home/umbrel/netops-releases/3.2.0 --dry-run
```

**What it does:** validates source structure and Python syntax; stops NETOPS through Umbrel; backs up app source, Dockerfile, SQLite data, notes, users, and SSH files into a private `netops-backups` archive; replaces only the **application source and Dockerfile**; restarts the native app (which builds the new image), and attempts to restore the old source on errors. It never edits `.env`, deletes persistent data, updates other apps, or needs a Docker socket in the app container.

**What it does not do:** it will **not** run database migrations for a future incompatible release automatically; apply an altered Umbrel Compose/manifest from an arbitrary archive; watch GitHub for new releases; or manage Nginx Proxy Manager. If an update changes the Umbrel `docker-compose.yml` or manifest, publish that new app-store manifest and use Umbrel's own app update process, reviewing the changes first. The home-screen *displayed manifest version* may remain `3.1.0` for **code-only** updates; `netops-last-update.json` in Umbrel's installed app directory records the source and ref that was applied.

Use only trusted sources. GitHub tags can be moved, and the updater checks structure/syntax but does not cryptographically attest a GitHub release. To protect against unreviewed code, pin a reviewed commit in your own release process or use a locally verified ZIP.

If the install path is different, pass `--app-dir /YOUR/UMBREL/DATA/app-data/netopslab-command-center` to either script. If `sudo` changes `$HOME`, backups will be under `/root/netops-backups` by default; pass `--backup-dir /home/umbrel/secure-backups` to set your preferred backup destination.

## 6. Security / operational notes

- NETOPS backend runs as unprivileged UID `10001` with a read-only root filesystem, dropped Linux capabilities, and no host Docker socket. A short-lived permissions helper prepares the Umbrel app data directory during startup; it runs as root with only `CHOWN` and `DAC_OVERRIDE` capabilities.
- Application database, all users and dashboards, and admin SSH configuration stay in Umbrel app storage under `/data`. SSH sessions are initiated from Umbrel, not from your workstation. Any authenticated NETOPS user can still initiate ad hoc SSH; dashboard sharing is **not** a network access-control list.
- NETOPS does not automatically scan IP ranges, run diagnostics on a schedule, or send telemetry. Browser bookmarks connect when clicked. **Installation/build** fetches base image, OS and Python packages; **GitHub updating** contacts only the specified GitHub repository when explicitly run.
- App requires LAN/VPN-only access, unless secured by a vetted proxy with HTTPS and WebSocket support. For multi-user access, ensure Umbrel app sharing/login requirements align with NETOPS's own account permissions.
- **Do not choose Umbrel Uninstall as a stop mechanism**: uninstalling an Umbrel app may delete its app-managed data. Use Stop instead.
- Persistent folders are app-managed; NAS or mounted storage moves follow Umbrel's storage settings. No background update, automatic pull or scheduling is installed.

## Known limitations

There is no live UmbrelOS host available in this environment, so the native store registration, first Docker build and migration script have **not** been exercised against your installation. The source layout, manifest, YAML and host-side update dry runs are checked locally. Umbrel's app-store source build/install behavior can vary between versions: if your Umbrel refuses a build-only app during `docker compose pull`, a published prebuilt OCI image is the more compatible alternative and the manifest would need an `image:` reference.

**Help:** `./scripts/update-netops.sh --help` and `./scripts/migrate-from-standalone.sh --help`.
