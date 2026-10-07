#!/usr/bin/with-contenv bashio

# exec: the service must be the process s6 signals on shutdown, not a child of this shell
exec ./src/main.py
