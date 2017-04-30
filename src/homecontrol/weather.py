import json
import logging

from urllib import parse as url_parse

from homecontrol import settings

logger = logging.getLogger(__name__)

client = None


def _client():
    global client

    if not client:
        secrets_filename = "yahoo_secrets.json"
        _update_secrets(secrets_filename)

        # we need to retain the logger class since yahoo_oauth kindly overwrites it globally
        logger_class = logging.getLoggerClass()
        from yahoo_oauth import OAuth1 as YOAuth1
        logging.setLoggerClass(logger_class)

        client = YOAuth1(
            None,
            None,
            from_file=secrets_filename,
        )
        client.refresh_access_token()

    return client


def _update_secrets(filename):
    secrets = _read_data(filename)
    secrets["consumer_key"] = settings.YAHOO_CLIENT_ID
    secrets["consumer_secret"] = settings.YAHOO_CLIENT_SECRET
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
    }

    qs = url_parse.urlencode(query_data)
    url = "https://query.yahooapis.com/v1/public/yql?{}".format(qs)

    response = None
    try:
        yahoo_client = _client()

        if not client.token_is_valid():
            client.refresh_access_token()

        response = yahoo_client.session.get(url)
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
