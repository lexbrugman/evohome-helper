import json
import os

# reads app configuration from home assistant
with open("/data/options.json") as f:
    add_on_config = json.load(f)

EVOHOME_LOCATION_NAME = add_on_config["evohome"]["location_name"]
EVOHOME_USERNAME = add_on_config["evohome"]["username"]
EVOHOME_PASSWORD = add_on_config["evohome"]["password"]
EVOHOME_OFF_TEMP_THRESHOLD = add_on_config["evohome"]["off_temp_threshold"]
EVOHOME_AWAY_MODE = add_on_config["evohome"]["away_mode"]
EVOHOME_TOKEN_CACHE_PATH = "/data/evohome_token_cache.json"

HOMEASSISTANT_URL = "http://supervisor/core"
HOMEASSISTANT_TOKEN = os.environ["SUPERVISOR_TOKEN"]
HOMEASSISTANT_PRESENCE_ENTITIES = add_on_config["presence"]["entities"]
HOMEASSISTANT_AUTO_ECO_WEATHER_ENTITY = add_on_config["auto_eco"]["weather_entity"]

PRESENCE_LAST_HOME_GRACE_TIME = add_on_config["presence"]["last_home_grace_time"]
PRESENCE_HEATING_SCHEDULE_GRACE_TIME = add_on_config["presence"]["heating_schedule_grace_time"]

AUTO_ECO_ENABLED = add_on_config["auto_eco"]["enabled"]
AUTO_ECO_OUTSIDE_TEMP_THRESHOLD = add_on_config["auto_eco"]["outside_temp_threshold"]
AUTO_ECO_INSIDE_TEMP_DIFF = add_on_config["auto_eco"]["inside_temp_diff"]

INTERVAL = add_on_config["interval"]
