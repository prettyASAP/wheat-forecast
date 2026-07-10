"""Panel összeállítás (Fázis 2 — TODO).

A három forrás (KSH hozam, NUTS3 geometria, ERA5 időjárás) egyesítése egy
(vármegye, termésév) panellá. KRITIKUS: a Y évi hozamhoz a Y-1 okt 1 – Y jún 30
időjárás tartozik (termésévi eltolás). Kimenet: data/processed/panel.parquet.
"""
