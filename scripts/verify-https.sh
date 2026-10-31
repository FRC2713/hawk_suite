#!/usr/bin/env bash
set -uo pipefail

# First issuance is three ACME round trips and can take a minute or
# two; a fixed sleep would be either flaky or wasteful.
check() { # check <url> <what>
  local url=$1 what=$2 deadline=$((SECONDS + 180)) code=000
  while ((SECONDS < deadline)); do
    # curl already writes 000 via -w when it cannot connect; the ||
    # replaces it rather than appending, so the message stays legible.
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$url") || code=000
    # 2xx and 3xx both count: hawk-shop redirects to Onshape when
    # nobody is signed in, which is a working app.
    if [[ $code =~ ^[23] ]]; then
      echo "  ok   $what ($code)"
      return 0
    fi
    sleep 10
  done
  echo "::error::$what did not serve HTTPS within 180s (last: $code)"
  return 1
}

rc=0
check "https://apps.${DOMAIN}"       "portal"    || rc=1
check "https://shop.${DOMAIN}"       "hawk-shop" || rc=1
check "https://mod.${DOMAIN}/health" "hawk-mod"  || rc=1
check "https://bot.${DOMAIN}/health" "hawk-bot"  || rc=1

# A plain-http redirect is invisible in a browser tab, because Caddy
# bounces it back to HTTPS. Inside Onshape's HTTPS iframe it is mixed
# content and Chrome blocks it silently — a blank panel, no error
# anywhere. The tab-level checks above cannot see this at all.
redirect=$(curl -sS -o /dev/null -w '%{redirect_url}' --max-time 15 \
           "https://shop.${DOMAIN}/onshape_connector?elementType=PARTSTUDIO" || true)
case "$redirect" in
  http://*)
    echo "::error::hawk-shop redirects to plain HTTP: ${redirect%%\?*}"
    echo "::error::The Onshape iframe will render blank. Caddy should be"
    echo "::error::rewriting this — check that the config reloaded."
    rc=1 ;;
  *) echo "  ok   redirects stay on https" ;;
esac

if ((rc)); then
  echo "::notice::Containers may be running fine — this checks TLS and routing."
  echo "::notice::Usual causes: the Linode Cloud Firewall blocking 80 (Caddy"
  echo "::notice::needs it for the HTTP-01 challenge), DNS not pointing at the"
  echo "::notice::host, or Let's Encrypt rate limits. 'docker compose logs caddy'"
  echo "::notice::names which."
fi
exit $rc
