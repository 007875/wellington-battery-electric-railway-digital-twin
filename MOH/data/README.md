# data/

`stations.csv` and `sections.csv` describe the Wellington LNIRIM network used by the model.

**All values are APPROXIMATE / ILLUSTRATIVE.** Chainages, average speeds, gradient energy factors and coordinates are
rounded planning-level numbers so that the model runs; they are NOT survey data. In the PhD they are replaced by
validated data (Metlink/GTFS timetables, KiwiRail track data, OpenTrack/RailML export, Alstom rolling-stock data).

* `sections.csv`: `electrified` 1 = under 1500 V DC overhead line; `tracks` 1 = single track (one bidirectional block),
  2 = double track (one block per direction); `grade_out/grade_in` multiply battery consumption away from / towards Wellington.
* `stations.csv`: `role` loop = passing loop, terminal = end of line with fast charger; `mode_change` 1 = pantograph up/down point.

`dt_feed_example.csv` is an example Digital Twin event feed (see `model/digital_twin.py`).
