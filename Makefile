.DEFAULT_GOAL := help

.PHONY: help requirements upgrade quality test test-quality coverage clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

requirements: ## Install development requirements
	pip install -r requirements/dev.txt

upgrade: ## Re-pin requirements/*.txt from requirements/*.in
	pip-compile --upgrade -o requirements/base.txt requirements/base.in
	pip-compile --upgrade -o requirements/test.txt requirements/test.in
	pip-compile --upgrade -o requirements/quality.txt requirements/quality.in
	pip-compile --upgrade -o requirements/dev.txt requirements/dev.in

quality: ## Run static analysis
	ruff check openedx_webhook_relay
	isort --check-only --diff openedx_webhook_relay
	pylint openedx_webhook_relay

test: ## Run the test suite with coverage
	pytest --cov=openedx_webhook_relay --cov-report=term-missing --cov-report=html --cov-fail-under=90

test-quality: quality test ## Run quality checks then tests

clean: ## Remove build/test artifacts
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -type d -exec rm -rf {} +
	rm -rf .pytest_cache htmlcov .coverage *.egg-info build dist
