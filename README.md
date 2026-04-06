# adaptive6-takehome

Apache log analytics — statistical breakdowns by **Country**, **OS**, and **Browser**.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
make download-db
```

The last command fetches the GeoLite2-Country database into `data/`.

## Usage

```bash
source .venv/bin/activate
python -m log_analytics apache_log.txt
```

Options:

```bash
# Use a different GeoIP database
python -m log_analytics apache_log.txt --db /path/to/GeoLite2-Country.mmdb

# Show only the top 5 entries per dimension (rest rolled into "Other")
python -m log_analytics apache_log.txt --top-n 5

# Log parse errors to stderr
python -m log_analytics apache_log.txt -v
```

## Tests

```bash
# Unit tests only (fast, no GeoLite2 DB needed)
pytest -m "not integration"

# All tests including integration against apache_log.txt
pytest
```