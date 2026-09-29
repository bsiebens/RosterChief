# Caddy with a DNS plugin. The stock image cannot solve a DNS-01 challenge, and DNS-01 is the
# only way Let's Encrypt issues the *.rosterchief.app wildcard that subdomain tenancy needs.
#
# Swap the module if your DNS lives elsewhere: caddy-dns/route53, caddy-dns/digitalocean, ...
#
# Everything is pinned: both base images by version tag + multi-arch index digest (works on
# amd64 and arm64 alike -- `docker buildx imagetools inspect caddy:<ver>` prints it), the Caddy
# version xcaddy compiles, and the DNS plugin's release. Bump all three together: the builder
# and runtime tags should name the same Caddy release, and a new Caddy may need a newer plugin.
ARG CADDY_VERSION=v2.11.4
ARG CADDY_DNS_CLOUDFLARE_VERSION=v0.2.4

FROM caddy:2.11.4-builder@sha256:369218c81ca6d6af249981221b3a5c764d886dd5b058f51d144066de13f2418d AS builder
ARG CADDY_VERSION
ARG CADDY_DNS_CLOUDFLARE_VERSION
RUN xcaddy build "${CADDY_VERSION}" --with "github.com/caddy-dns/cloudflare@${CADDY_DNS_CLOUDFLARE_VERSION}"

FROM caddy:2.11.4@sha256:0c994536bddb66445885237f1a5dcc1916bccea922661c76b4e9fc24061f9b52
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
