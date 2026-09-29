# postgres off-site destination

CloudNativePG has one live barman destination per `Cluster`. A second `ScheduledBackup` with its own `barmanObjectName` reports completed and writes to the original store (cloudnative-pg#7778).

The off-site copy is a suspended `rclone copy` mirror of the LAN archive. Operating rules, including copy-only and the 35d window, are `kubernetes/apps/base/database/cloudnative-pg/offsite-mirror/README.md`.
