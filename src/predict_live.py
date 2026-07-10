"""Napi élő előrejelzés (Fázis 5 — TODO).

A futó termésévre lehúzza a mostanáig tartó időjárást (forecast API, past_days),
a hátralévő ablakokra klimatológiai átlagot tesz, feature-öket számol, vármegyénként
jósol anomáliát és sávot. Kimenet: web/data/forecast.json + napi history snapshot.
"""
