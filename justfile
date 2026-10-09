# Copyright 2026 Helmut Grohne <helmut@subdivi.de>
# SPDX-License-Identifier: LGPL-2.0-or-later

# Collectection of common tasks

# List available commands
list:
	just -l

# Verify coding style
style:
	black --diff asyncvarlink tests

# Delete generated files
clean:
	rm -f .coverage
	rm -Rf .hypothesis .mypy_cache .pytest_cache __pycache__ docs/build */__pycache__

# Spell check comments
spell:
	codespell asyncvarlink tests

# Run the unit test suite and produce a coverage report
coverage:
	pytest --cov=asyncvarlink

# Type check tests (fails)
testtypecheck:
	mypy --strict tests

# Type check the library
typecheck:
	mypy --strict asyncvarlink

# Run pylint on the code
lint:
	pylint asyncvarlink tests

# Render the documentation into html
doc:
	sphinx-build docs docs/build

# Run unit tests
test:
	pytest
