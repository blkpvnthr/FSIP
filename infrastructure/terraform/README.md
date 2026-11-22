# infrastructure/terraform — (stub, later milestone)

Infrastructure-as-Code for the platform (S3 raw/landing buckets, Snowflake
warehouse/database/roles) will live here in a later milestone.

**Intentionally empty in Milestone 1.** Per the engineering constraints, this
milestone:

- provisions **no paid cloud infrastructure**, and
- introduces **no credentials or secrets** into version control.

When added, Terraform will follow the same discipline: variables for all account
identifiers, no hard-coded secrets, remote state configured out-of-band, and
`*.tfstate` / `.terraform/` git-ignored (already covered by `.gitignore`).
