# Evohome Helper

This service adds presence detection to any Honeywell Evohome installation, turning the heating off when no-one is home. It also adjusts to your heating schedule in the decision to turn your heating off when you're not at home, making sure you still have a warm home after coming back from work.

Depends on Home Assistant for presence and weather information.

## Features

- Presence detection (switches to 'Custom' when no-one is home)
- Use eco mode with warm weather to save energy

## Install via Home Assistant

1. In Home Assistant, go to **Settings → Apps → Install app**.
2. Open the menu (**⋮**) and choose **Repositories**.
3. Add <https://github.com/lexbrugman/ha-apps> as a repository.
4. Find **Evohome Helper** in the store and install it.
