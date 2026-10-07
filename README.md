<div align="center">
  <img src="logo.svg" width="150" height="150" />
</div>

# Evohome Helper

This service adds presence detection to any Honeywell Evohome installation, turning the heating down when no-one is home while keeping the home warm for when people arrive.

Depends on Home Assistant for presence and weather information.

## Features

- **Presence detection**: switches to the configured away mode when no-one is home.
- **Pre-heating**: for a while after the schedule raises a setpoint, the heating stays on
  even if no-one is detected yet, so the home is warm on arrival. A home that has been
  empty for a longer period (holiday) is not pre-heated.
- **Auto-eco**: uses Evohome's eco mode when it is warm outside.

Presence is read from Home Assistant entities that report `home` when someone is home
(`person.*` or `device_tracker.*`). The time the last person left is taken from the
entity's state change time (`last_changed`).

Manual changes on the thermostat (a system mode or a zone override set by hand) are
left alone until they are cleared.

## Install via Home Assistant

1. In Home Assistant, go to **Settings → Apps → Install app**.
2. Open the menu (**⋮**) and choose **Repositories**.
3. Add <https://github.com/lexbrugman/ha-apps> as a repository.
4. Find **Evohome Helper** in the store and install it.


## Development

This project uses [Poetry](https://python-poetry.org/) for dependency management.

```bash
poetry install --with dev
poetry run pytest
```

The service only runs inside the Home Assistant add-on: it reads its configuration
from `/data/options.json` and talks to Home Assistant through the supervisor API,
neither of which exists outside the add-on container. The test suite is the local
development loop; to try changes for real, install the edge build of the add-on.
