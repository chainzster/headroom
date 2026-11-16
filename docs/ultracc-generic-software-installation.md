# Generic Software Installation

**Source:**
https://docs.ultra.cc/unofficial-application-installers/generic-software-installation\
**Retrieved:** 16 November 2026

> **Unofficial guide:** This guide is provided as-is and may not be
> updated or maintained by Ultra.cc. Unofficial support may be offered
> through Discord at Ultra.cc staff's discretion. Use at your own risk
> and only proceed if you are comfortable troubleshooting.

This guide describes generic ways to install custom third-party
applications on an Ultra.cc service. Some applications may require
additional steps. Ultra.cc does not provide support for installation or
issues with custom applications that it does not officially support.

If you want an application added to the User Control Panel (UCP) and
officially supported, submit a request on the Ultra.cc feedback site.
Vote on an existing request rather than creating a duplicate.

## Installation

Many applications can be installed through the UCP. Applications not
listed there can also be installed, provided you comply with the [Terms
of Service](https://ultra.cc/terms-of-service) and [Fair Usage
Policy](https://ultra.cc/terms-of-service).

When configuring a custom application, use a port within the range
assigned to your service. **Do not use the application's default port
unless it falls within your assigned range. Using ports outside your
range is prohibited.**

Important restrictions and considerations:

-   Applications requiring `sudo` or root privileges cannot be
    installed. This includes:
    -   Docker images (look for local installation instructions
        instead).
    -   Applications that need to place files outside your home
        directory.
-   Be mindful of application resource consumption and I/O utilization.
-   Use only ports assigned to your service.
-   Custom third-party applications are not officially supported by
    Ultra.cc staff.
-   Read the documentation for the application you are installing.

Before proceeding, connect to your Ultra.cc service via SSH. See the
[Ultra.cc SSH guide](https://docs.ultra.cc/).

## Compile from source

Some applications need to be compiled on the service. Check the
application's own documentation for dependencies and
application-specific configuration.

1.  Download the source. Tools such as `wget`, `curl`, and `git` can be
    used.

    ``` bash
    wget https://example-url.com/appname-1.23.tar.gz
    ```

2.  Extract the source archive.

    ``` bash
    tar xvzf appname-1.23.tar.gz
    ```

3.  Change into the extracted directory.

    ``` bash
    cd appname-1.23
    ```

4.  Configure and compile the application. The example installs under
    `$HOME/bin`.

    ``` bash
    ./configure --prefix="$HOME/bin" && make
    ```

5.  Install the software.

    ``` bash
    make install
    ```

6.  Add common user-level binary directories to your `PATH`.

    ``` bash
    echo "PATH=$HOME/.local/bin:$HOME/bin:$PATH" >> ~/.profile
    source ~/.profile
    ```

## Install pre-built binaries

Some applications provide pre-compiled binaries, so you do not need to
build them on your Ultra.cc service.

1.  Download the binary archive.

    ``` bash
    wget https://example-url.com/appname-1.23.tar.gz
    ```

2.  Extract it.

    ``` bash
    tar xvzf appname-1.23.tar.gz
    ```

3.  Move the executable into a directory in your shell's `PATH`.

    ``` bash
    mv appname ~/bin/
    ```

You can then run the application by executing its binary name:

``` bash
appname
```

## Clone a repository

Applications can also be installed by cloning a source repository.
GitHub and GitLab are examples of repository hosting services.

1.  Copy the repository's clone URL. On GitHub, use the **Code** button
    and copy the repository URL.

2.  Change to the directory where you want the repository. The home
    directory is a common choice.

    ``` bash
    cd
    ```

3.  Clone the repository, replacing the example author and repository
    with the actual values.

    ``` bash
    git clone https://github.com/AUTHOR/EXAMPLE-REPO
    ```

4.  Enter the cloned repository.

    ``` bash
    cd EXAMPLE-REPO
    ```

You can then run scripts or binaries included in the repository, subject
to Ultra.cc's terms and policies.

## Python applications

Before installing Python packages, make sure Python is installed on your
Ultra.cc service. Follow Ultra.cc's [Python installation
guide](https://docs.ultra.cc/).

### Install a Python package

Once Python is installed, packages can be installed with `pip`. The
Python Package Index (PyPI) contains the package catalogue.

Install an individual package:

``` bash
pip install <package-name>
```

If the application provides a `requirements.txt` file, install its
dependencies with:

``` bash
pip install -r requirements.txt
```

## Run an application as a user systemd service

You can run binaries and scripts manually or schedule them with cron.
Another option is a user-level systemd service, which runs an
application in the background and provides service-management commands.

Check the application's documentation first; it may provide a
recommended service configuration.

### Create the service file

A user-level systemd service file is stored under:

``` text
~/.config/systemd/user/
```

Create a file, replacing `SERVICE-NAME` with your chosen service name:

``` bash
touch ~/.config/systemd/user/SERVICE-NAME.service
nano ~/.config/systemd/user/SERVICE-NAME.service
```

Example service file:

``` ini
[Unit]
Description=A description of my custom application
After=network-online.target

[Service]
Type=exec
Restart=on-failure
ExecStart=%h/bin/MY-CUSTOM-APPLICATION
ExecStop=/bin/kill -s QUIT $MAINPID
StandardOutput=file:%h/path/to/logs/my-custom-application.log

[Install]
WantedBy=default.target
```

Edit `ExecStart` to point to your binary or script. Adjust the
description and log path as appropriate. Ensure the log directory
exists.

Save and exit the editor (`Ctrl+X`, then `Y`, then `Enter`).

### Reload, enable, and manage the service

After changing a service file, reload the user systemd daemon:

``` bash
systemctl --user daemon-reload
```

**Important:** Always include `--user` when using `systemctl` on an
Ultra.cc service. Omitting it attempts to interact with the system-level
service manager and requires root privileges.

Enable the service and start it immediately:

``` bash
systemctl --user enable --now SERVICE-NAME.service
```

Check whether it is enabled:

``` bash
systemctl --user is-enabled SERVICE-NAME.service
```

Check its status:

``` bash
systemctl --user status SERVICE-NAME.service
```

## Enable HTTPS using Nginx

A custom application listening on one of your assigned ports is
accessible over HTTP by default, which does not encrypt traffic.
Ultra.cc's Nginx integration can provide HTTPS access.

### Create an Nginx configuration file

Create a configuration file, replacing `APP-NAME` with a name of your
choice:

``` bash
touch ~/.apps/nginx/proxy.d/APP-NAME.conf
nano ~/.apps/nginx/proxy.d/APP-NAME.conf
```

Application developers sometimes provide an Nginx reverse-proxy
template. Prefer the application's own template and adapt it as
necessary.

If no template is available, the Ultra.cc guide provides this basic
example:

``` nginx
location /baseurl/ {
    proxy_pass              http://127.0.0.1:PORT;
    proxy_http_version      1.1;
    proxy_set_header        X-Forwarded-Host        $http_host;
}
```

Edit the following:

-   `/baseurl` is the URL path used to access the application. It must
    not already be used by another application. For example:
    `https://username.hostname.usbx.me/mycustomapp`.
-   `PORT` is the five-digit port configured for your application. It
    must be within your service's assigned port range.
-   Some applications also need to be configured with the same base URL.
    Check the application's documentation.

Save and exit the editor (`Ctrl+X`, then `Y`, then `Enter`).

### Restart Nginx

Restart the web server using either the UCP or:

``` bash
app-nginx restart
```

Then visit the configured base URL to check that the application's web
interface is accessible, for example:

``` text
https://username.hostname.usbx.me/mycustomapp
```

## Support

For further assistance, the guide directs users to open an Ultra.cc
support ticket. Note that custom third-party application installation
itself is not officially supported.

## Practical checklist

-   [ ] Confirm the application complies with Ultra.cc's Terms of
    Service and Fair Usage Policy.
-   [ ] Confirm it does not require root, `sudo`, Docker, or writes
    outside your home directory.
-   [ ] Check application dependencies and installation instructions.
-   [ ] Choose a port from your assigned port range.
-   [ ] Install the application under your home directory.
-   [ ] Configure a user-level service if it needs to run continuously.
-   [ ] Configure Nginx HTTPS access if you need a web interface.
-   [ ] Monitor resource and I/O usage.

------------------------------------------------------------------------

*This Markdown document is a structured conversion of the Ultra.cc
guide. Check the original page for updates and the latest linked
instructions.*
