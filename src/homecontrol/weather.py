import json
import logging
import settings

from homecontrol.func_tools import return_cache
from urllib import parse as url_parse


logger = logging.getLogger(__name__)


def _client():
    secrets_filename = "yahoo_secrets.json"

    _update_secrets(secrets_filename)

    # we need to retain the logger class since yahoo_oauth kindly overwrites it globally
    logger_class = logging.getLoggerClass()
    from yahoo_oauth import OAuth2 as YOAuth2
    logging.setLoggerClass(logger_class)

    return YOAuth2(
        None,
        None,
        from_file=secrets_filename,
    )


def _update_secrets(filename):
    secrets = _read_data(filename)

    secrets_changed = False

    if secrets.get("consumer_key") != settings.YAHOO_CLIENT_ID:
        secrets["consumer_key"] = settings.YAHOO_CLIENT_ID
        secrets_changed = True

    if secrets.get("consumer_secret") != settings.YAHOO_CLIENT_SECRET:
        secrets["consumer_secret"] = settings.YAHOO_CLIENT_SECRET
        secrets_changed = True

    if secrets_changed:
        _write_data(filename, secrets)


def _read_data(filename):
    try:
        with open(filename, "r+") as f:
            data = json.load(f)
    except (ValueError, IOError):
        data = dict()

    return data


def _write_data(filename, data):
    with open(filename, "w+") as f:
        json.dump(data, f, indent=4, sort_keys=True)


@return_cache(refresh_interval=900)
def _query(query):
    query_data = {
        "q": query,
        "format": "json",
        "diagnostics": "false",
    }

    qs = url_parse.urlencode(query_data)
    url = "https://query.yahooapis.com/v1/yql?{}".format(qs)

    response = None
    try:
        response = _client().session.get(
            url,
            timeout=10,
        )
        response.raise_for_status()

    except Exception:
        if response:
            logger.error("got a %s from the weather api: %s", response.status_code, response.content)
        else:
            logger.exception("error while sending request to the weather api")

        return None

    data = response.json()
    query = data.get("query", {"results": None})
    result = query.get("results")

    return result


def get_temperature_info(location):
    #data = _query("""
    #    select item.forecast, item.condition
    #    from weather.forecast
    #    where woeid in (select woeid from geo.places(1) where text='{}') and u='c'
    #    | sort(field="item.forecast.date")
    #    | truncate(count=1)
    #""".format(location))

    high_temperature = -99
    current_temperature = -99
    #if data:
    #    try:
    #        item_data = data["channel"]["item"]
    #        high_temperature = float(item_data["forecast"]["high"])
    #        current_temperature = float(item_data["condition"]["temp"])
    #    except TypeError:
    #        logger.exception("error while parsing response from the weather api: %s", data)

    return round(high_temperature, 1), round(current_temperature, 1)
