import logging
from netaddr import IPAddress, IPNetwork
from pyunifi.controller import Controller as BaseUnifiClient

import settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class UnifiClient(BaseUnifiClient):
    def _logout(self):
        try:
            super(UnifiClient, self)._logout()
        except ValueError:
            pass


def _client():
    return UnifiClient(
        settings.UNIFI_HOSTNAME,
        settings.UNIFI_USERNAME,
        settings.UNIFI_PASSWORD,
        port=443,
        version="v5",
        site_id=settings.UNIFI_SITE_NAME,
    )


def _get_wireless_clients():
    unifi_client = _client()

    return filter(lambda c: not c.get("is_wired", True), unifi_client.get_clients())


def is_someone_home():
    clients = _get_wireless_clients()
    for client in clients:
        ip_address = client.get("ip", "")
        if not ip_address or not IPAddress(ip_address) in IPNetwork(settings.UNIFI_HOME_SUBNET):
            continue

        hostname = client.get("name", client.get("hostname", ""))
        if not hostname or hostname in settings.UNIFI_HOME_FIXED_DEVICES:
            continue

        return True

    return False
