# Data

Raw and row-level data are git-ignored. Get the source and verify it:

```bash
curl -L -o /tmp/lending-club.zip "https://www.kaggle.com/api/v1/datasets/download/wordsforthewise/lending-club"
unzip -j /tmp/lending-club.zip "accepted_2007_to_2018Q4.csv.gz" -d data/raw/
shasum -a 256 data/raw/accepted_2007_to_2018Q4.csv.gz
```

The checksum must match `data/source_manifest.yaml`. The pipeline refuses to load a file that differs.

| Layer | Location | Contents |
|---|---|---|
| Raw | `data/raw/` | The downloaded file, unchanged |
| Bronze | HDFS `/credit_risk/bronze/<sha12>/` | Uncompressed copy of the raw file, versioned by checksum |
| Quarantine | HDFS `/credit_risk/quarantine/<sha12>/` | Rejected rows with reason codes |
| Silver | HDFS `/credit_risk/silver/<sha12>/loans`, copied to `data/silver/` | Typed, validated loans with features |
| Gold | `data/gold/`, PostgreSQL schema `credit_risk` | Aggregates, metrics and the star schema |
| Tableau | `data/gold/tableau/` | Extracts for a manual Tableau build |
