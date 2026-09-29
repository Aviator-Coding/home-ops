# Agentgateway listeners

Three Gateways. The rules that keep `internal` https-only, `public`'s
http listener behind `apikey-policy`, and `internal-noauth` on ClusterIP
are skill `agentgateway` references/gateways-auth.md.

LoadBalancer addresses: `internal` `10.50.0.27`, `public` `10.50.0.29`.
None of them publish DNS (`external-dns` controller annotation `none`).
The LAN wildcard that points at `10.50.0.27` is a hand-made UniFi record.
Skill `networking`.

Admin UI is port 15000, path `/ui/`.
