"""M1 — the request-forecast model behind Gold's fact_request_forecast (F5).

The package keeps model arithmetic separate from city-specific I/O:

    features.py  panel -> feature matrix. Pure functions over a DataFrame.
    model.py     fit / predict / evaluate, plus the seasonal-naive baseline.
    bootstrap.py event-cluster refits and raw uncertainty replicas (H2-R13).
    behavior.py  one-at-a-time common-sense and extrapolation probes (H2-R3).

Neither knows which city it runs for. The panel arrives with **role names**
(``unit_id`` / ``event_id`` / ``unit_size``) and the mapping from Winnipeg's
column names lives in ``config/models/m1.yaml`` and
``scripts/models/train_m1.py`` — city-agnostic guardrail §2, the same shape as
``spark/transforms/geography_boundary.py``.

Requires the ``ml`` extra (pandas, statsmodels), which is deliberately not part
of ``dev``: install it into its own environment with ``make test-ml``.
"""
