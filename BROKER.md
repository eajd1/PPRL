# Broker operation

The merged deployment retains HTTPS/mTLS, JSON record delivery, PostgreSQL matching and the React dashboard.

See [broker/README.md](broker/README.md) for current setup, certificates, ports and operation.

The hospital export now uses 14 v4 tokens with the metadata fields retained. See the current guide for combinations, five capped score groups, upgrade limitations and fresh-database evaluation. V3 and v4 are incompatible; regenerate from original hospital CSVs. Existing IDs and historical versions are retained.
