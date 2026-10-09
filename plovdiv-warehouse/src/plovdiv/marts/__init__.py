"""Marts built directly from landed files.

Modules:

* core   builds the two required marts of the data contract (mart_weather_aq_hourly, mart_aq_daily)
         from the landing zone and assembles the authoritative manifest.json
         (python -m plovdiv.marts.core)

The optional marts (model, risk and trend) are built by plovdiv.ml.marts.
"""
