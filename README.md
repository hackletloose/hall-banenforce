# HaLL Ban Enforcer
This Script automatically checks steam accounts that connects to your Hell Let Loose-Server. 

If the account is created recently, does not seem to own hell let loose or the community profile was not setted up, it bans them automatically to prevent ban bypass. 

## Features
- Connects to [CRCON Tool](https://github.com/MarechJ/hll_rcon_tool) and checks every player that connects
- Checks:
  - Account Age
  - If Community Profile is set up (can be disabled)
  - If Player owns Hell Let Loose (can be disabled)
- Writes checked players to database to prevent querying the steam api over and over again
- Checks player after a few days again, if profile is private (customizable in settings)
- Customizable messages
- Customizable Banlist

## Requirements
- CRCON Tool
  - Log Stream must be enabled
- MariaDB (can be set up via Docker)

## Install
### via Docker
1. Clone the repo
2. Copy example.env to .env and edit it to your needs
3. Enter `docker compose up -d db`
4. Enter `docker compose up -d`

### Manually
You need a mariadb for that.
1. Clone the repo
2. Install the requirements with `pip install -r requirements.txt`
3. Run the app with `python3 app.py`

You should create a systemd service. You can find more information on that in the manual of your linux distro. 
