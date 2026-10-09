"""Next-day PM10 risk model for the Kamenitsa station (EEA BG0051A).

Modules:

* dataset   loading of the landed station and reanalysis files, the daily table, the label
            definition and the time-ordered split
* features  feature construction and the feature sets of the model variants
* metrics   evaluation: ranking and probability scores, threshold choice, calibration
* train     training, evaluation and writing of the model files (python -m plovdiv.ml.train)
* trend     weather-adjusted monthly means of daily PM10 (method in docs/METHODS_trend.md)
* marts     builds the model and trend marts of the data contract (python -m plovdiv.ml.marts)
"""
