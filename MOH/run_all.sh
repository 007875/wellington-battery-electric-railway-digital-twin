#!/usr/bin/env bash
# Re-creates every result: experiments (~10 min on one core), figures, standalone HTML dashboard.
set -e
python -m model.experiments ${1:-8}
python -m model.make_figures
python -m model.build_html_dashboard
