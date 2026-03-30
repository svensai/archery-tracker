.PHONY: install test lint run-web run-sync

# Install all dependencies (including pytest and apscheduler)
install:
	pip install -r requirements.txt

# Run the full test suite
test:
	pytest tests/ -v

# Run tests and stop on first failure
test-fast:
	pytest tests/ -x -q

# Start the web server
run-web:
	python run_web.py

# Start the sync worker in daemon mode
run-sync:
	python run_sync.py --daemon
