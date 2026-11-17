# Broker operation

The merged deployment retains HTTPS/mTLS, JSON record delivery, PostgreSQL matching and the React dashboard.

See [broker/README.md](broker/README.md) for current setup, certificates, ports and operation.

The hospital export retains token_version, key_id and method with token1 through token25. Incomplete combinations remain blank. Private-healthcare tokens share the existing group caps; maximum score remains 24, with match/review thresholds 12/6. Existing global IDs and historical token snapshots are preserved.
