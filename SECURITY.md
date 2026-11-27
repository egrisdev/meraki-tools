# Security policy

## Supported versions

Only the latest commit on `main` gets security fixes. Please check that a problem still happens there before reporting it.

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

Report it privately instead: on this repository, open the **Security** tab and choose **Report a vulnerability** ([direct link](https://github.com/egrisdev/meraki-tools/security/advisories/new)). Only the maintainers can see the report.

It helps to include:

- which tool, your Python version and your operating system,
- the steps that trigger the problem, and what an attacker could gain.

Leave out real API keys, passwords, organization names, serial numbers and addresses.

## What to expect

Meraki Tools is a free project maintained in spare time, so there is no guaranteed response time. Reports are read, and confirmed problems are fixed on `main` and credited in the commit unless you ask not to be named.

## Scope

In scope: anything that leaks the Meraki API key or a database password, lets data from Meraki or from the database run code, or makes a tool change a network when its README says it only reads.

Out of scope: someone who can already read your `.env` file or your database, and vulnerabilities in Meraki, Python or the libraries the tools use (report those to their maintainers).

## Security model

### The API key

Every tool reads `MERAKI_API_KEY` from the environment or from a `.env` file in its own folder. Git ignores `.env`. The key is passed to the official Meraki Python library and nowhere else: it is not written to the database, to logs or to reports. Keep `.env` readable by your user only (`chmod 600 .env`).

Use a key with the least access the tool needs. Each tool's README says whether it only reads from Meraki or also makes changes.

### WAN stats

- **Read-only.** It makes three kinds of request, all of them reads: the list of organizations, the list of devices (for their names), and uplink loss and latency. A read-only API key is enough.
- **What it stores.** Organization names, network IDs, device names and serial numbers, the test address each uplink is measured against, and one loss and latency reading per uplink per minute. Treat the database and any report made from it as internal: they describe your sites.
- **Database passwords.** A PostgreSQL or MongoDB password lives in the `WAN_STATS_DB` URL in `.env`. The tools do not print or log it: when a connection fails, the message says what to check and leaves out the database driver's own text, which can quote the URL.
- **No other traffic.** Nothing is sent anywhere except to the Meraki API and to the database you configured. The PDF export downloads a headless Chromium through nbconvert, and only when you ask it to.
- **Text from Meraki is treated as data.** Device and organization names are stored through parameterized queries. Charts draw them as plain text, and the notebook's tables escape HTML and Markdown in them, so a name cannot turn into a link or a picture.
