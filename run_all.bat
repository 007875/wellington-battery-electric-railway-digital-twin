@echo off
python -m model.experiments 8
python -m model.make_figures
python -m model.build_html_dashboard
