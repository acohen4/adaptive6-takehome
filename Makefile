GEOIP_DB := data/GeoLite2-Country.mmdb
GEOIP_URL := https://raw.githubusercontent.com/6Kmfi6HP/maxmind/main/GeoLite2-Country.mmdb

.PHONY: download-db install dev test test-all clean

download-db: $(GEOIP_DB)

$(GEOIP_DB): | data
	curl -fSL -o $@ $(GEOIP_URL)

data:
	mkdir -p data

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest

test-all:
	pytest -m "" --tb=short

clean:
	rm -rf __pycache__ *.egg-info dist build .pytest_cache .coverage htmlcov
