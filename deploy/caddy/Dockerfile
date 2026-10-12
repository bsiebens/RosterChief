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

FROM caddy:2.11.6-builder@sha256:f5b1a66449d305280e559dba0ab9f7ce9a2a14c527c79d7c6fb48abc8f895818 AS builder
ARG CADDY_VERSION
ARG CADDY_DNS_CLOUDFLARE_VERSION
RUN xcaddy build "${CADDY_VERSION}" --with "github.com/caddy-dns/cloudflare@${CADDY_DNS_CLOUDFLARE_VERSION}"

FROM caddy:2.11.6@sha256:3422ce6de165df66534f9b9ba50efaf457114ec961763cc52f5dbdaac2972d73
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
