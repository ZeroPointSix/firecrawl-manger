#!/usr/bin/env sh
set -eu

pytest --cov=app --cov-fail-under=80

