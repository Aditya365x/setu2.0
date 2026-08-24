#!/bin/sh
# Geolocation, camera and service workers are all gated behind a secure
# context. localhost is exempt, which is why everything works on the dev laptop
# and then silently fails the moment a phone opens http://192.168.x.x — the API
# is not "denied", it is simply not there.
#
# A real certificate needs a public domain and a network round trip, and the
# whole system is supposed to run with the Wi-Fi unplugged. So: generate a
# self-signed cert at startup, covering whatever hosts this deployment answers
# to. The browser shows a one-time warning; accept it and GPS works.
set -e

CERT_DIR=/etc/nginx/certs
CERT_HOSTS="${CERT_HOSTS:-localhost}"

if [ ! -f "$CERT_DIR/setu.crt" ]; then
  mkdir -p "$CERT_DIR"

  SAN="DNS:localhost,IP:127.0.0.1"
  for host in $(echo "$CERT_HOSTS" | tr ',' ' '); do
    case "$host" in
      # Bare IPv4 has to be an IP SAN; a DNS SAN with an IP in it is ignored.
      *[!0-9.]*) SAN="$SAN,DNS:$host" ;;
      *)         SAN="$SAN,IP:$host" ;;
    esac
  done

  echo "SETU: generating self-signed certificate for $SAN"
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout "$CERT_DIR/setu.key" -out "$CERT_DIR/setu.crt" \
    -subj "/CN=SETU Citizen App/O=SETU/C=IN" \
    -addext "subjectAltName=$SAN" 2>/dev/null
fi

exec nginx -g 'daemon off;'
