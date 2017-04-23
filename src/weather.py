import logging
import requests
import urllib.parse

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def _query(query):
    query_data = {
        "q": query,
        "format": "json",
    }

    qs = urllib.parse.urlencode(query_data)
    url = "https://query.yahooapis.com/v1/public/yql?{}".format(qs)

    response = None
    try:
        response = requests.get(url)
        response.raise_for_status()
    except:
        if response:
            logger.error("got a %s from the weather api: %s", response.status_code, response.content)
        else:
            logger.exception("error while sending request to the weather api")

        return None

    return response.json()


def get_today_high_temperature(location):
    data = _query("""
        select item.forecast
        from weather.forecast
        where woeid in (select woeid from geo.places(1) where text='{}') and u='c'
        | truncate(count=1)
    """.format(location))

    temperature = -99
    if data:
        temperature = data["query"]["results"]["channel"]["item"]["forecast"]["high"]

    return int(temperature)
