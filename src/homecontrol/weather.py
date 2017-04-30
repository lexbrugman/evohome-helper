import json
import logging

from urllib import parse as url_parse

from homecontrol import settings

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
        response = _client().session.get(url)
        response.raise_for_status()
    except:
        if response:
            logger.error("got a %s from the weather api: %s", response.status_code, response.content)
        else:
            logger.exception("error while sending request to the weather api")

        return None

    return response.json()


def get_temperature_info(location):
    data = _query("""
        select item.forecast, item.condition
        from weather.forecast
        where woeid in (select woeid from geo.places(1) where text='{}') and u='c'
        | sort(field="item.forecast.date")
        | truncate(count=1)
    """.format(location))

    high_temperature = -99
    current_temperature = -99
    if data:
        high_temperature = float(data["query"]["results"]["channel"]["item"]["forecast"]["high"])
        current_temperature = float(data["query"]["results"]["channel"]["item"]["condition"]["temp"])

    return round(high_temperature, 1), round(current_temperature, 1)