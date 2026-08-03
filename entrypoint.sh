#!/bin/sh
set -e

# ---- Timezone ----
if [ -n "$TZ" ] && [ -f "/usr/share/zoneinfo/$TZ" ]; then
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
fi

PUID=${PUID:-99}
PGID=${PGID:-100}

# ---- User setup (Unraid convention: nobody:users = 99:100) ----
if ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -o -g "$PGID" tracker 2>/dev/null || addgroup -g "$PGID" tracker 2>/dev/null || true
fi

if ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -o -u "$PUID" -g "$PGID" -d /config -s /bin/sh tracker 2>/dev/null || true
fi

echo "Running as ${PUID}:${PGID} (TZ=${TZ:-unset})"

# Only chown what we own; a large /output on a spinning array shouldn't be
# re-chowned recursively every start, so this is deliberately shallow.
chown "$PUID":"$PGID" /config /output 2>/dev/null || true

exec gosu "$PUID":"$PGID" "$@"
