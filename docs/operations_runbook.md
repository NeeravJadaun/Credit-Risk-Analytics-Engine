# Operations runbook

Start services with `make services`, and stop them with `make stop`. Memory used on a 16 GB laptop: HDFS heap 1 GB per daemon, Spark driver 5 GB, PostgreSQL about 1 GB. Docker is not used.

| Problem | Likely cause | Fix |
|---|---|---|
| `postmaster became multithreaded` | Missing locale on macOS | The script sets `LC_ALL`. Run it through `scripts/local_postgres.sh` |
| HDFS `Connection refused` | NameNode not running | `./scripts/local_hadoop.sh start`, and check `.local/hadoop-logs/` |
| HDFS in safe mode | Just started, or the disk is nearly full | Wait, or free disk. HDFS blocks writes on low disk |
| `Unable to locate a Java Runtime` | Java not found | `brew install openjdk@17`. The scripts set `JAVA_HOME` from Homebrew |
| `Checksum mismatch` on ingest | Different source file | Re-download it, or review and update the manifest deliberately |
| Run stops with `Hard stop` | A reconciliation control failed | Read `data/gold/dq_layer_checks.csv`, and fix before publishing |
| Excel step: macro did not run | LibreOffice missing or profile error | `soffice --version`. It runs in a temporary profile each time |
| PostgreSQL WAL grows | Bulk load | `max_wal_size` is capped at 256 MB in the local config |

A failed run writes `reports/LATEST_STATUS.json` with `FAILED`, so an older report is not mistaken for current.

**To scale:** point the Spark master and `fs.defaultFS` to a cluster, raise replication, partition silver by more keys, move to incremental loads keyed by issue month, and replace the single-file gold exports with a scheduled job.
