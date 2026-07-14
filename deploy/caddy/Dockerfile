# Caddy with a DNS plugin. The stock image cannot solve a DNS-01 challenge, and DNS-01 is the
# only way Let's Encrypt issues the *.rosterchief.app wildcard that subdomain tenancy needs.
#
# Swap the module if your DNS lives elsewhere: caddy-dns/route53, caddy-dns/digitalocean, ...
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/caddy-dns/cloudflare

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
