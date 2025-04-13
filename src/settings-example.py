import json
import os

with open("/data/options.json") as f:
    add_on_config = json.load(f)

EVOHOME_LOCATION_NAME = add_on_config["evohome_location_name"]
EVOHOME_USERNAME = add_on_config["evohome_username"]
EVOHOME_PASSWORD = add_on_config["evohome_password"]

HOMEASSISTANT_URL = "http://supervisor/core"
HOMEASSISTANT_TOKEN = os.environ["SUPERVISOR_TOKEN"]
HOMEASSISTANT_PRESENCE_ENTITIES = add_on_config["presence_entities"]
HOMEASSISTANT_WEATHER_ENTITY = add_on_config["weather_entity"]

LAST_HOME_GRACE_TIME = add_on_config["last_home_grace_time"]
HEATING_SCHEDULE_GRACE_TIME = add_on_config["heating_schedule_grace_time"]
HEATING_SCHEDULE_OFF_TEMP = add_on_config["heating_schedule_off_temp"]
HEATING_ECO_TEMPERATURE = add_on_config["heating_eco_temperature"]
HEATING_ECO_TEMPERATURE_OFFSET = add_on_config["heating_eco_temperature_offset"]
