# Caddy with the rate_limit module (mholt/caddy-ratelimit), which is NOT in the
# stock image. Built once at `docker compose build` via xcaddy.

FROM caddy:2-builder AS builder

RUN xcaddy build \
    --with github.com/mholt/caddy-ratelimit

FROM caddy:2

COPY --from=builder /usr/bin/caddy /usr/bin/caddy
