# AXON developer tasks
.PHONY: bench test

bench:  ## Run the token-savings benchmark and render the chart
	python3 -m benchmarks.scenarios.long_session_baseline
	python3 -m benchmarks.scenarios.long_session_axon
	python3 -m benchmarks.visualize

test:  ## Run the test suite
	python3 -m pytest tests/ -q
