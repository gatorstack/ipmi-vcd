.PHONY: check test lint chart docker

check: lint test chart

lint:
	ruff check src tests

test:
	pytest

chart:
	helm lint charts/ipmi-vcd
	helm template ipmi-vcd charts/ipmi-vcd >/dev/null

docker:
	docker build -t ipmi-vcd:dev .

