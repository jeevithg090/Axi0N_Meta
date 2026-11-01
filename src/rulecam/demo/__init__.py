"""Golden demo runs from recorded live runs (PLAN-9.5 WS6).

A live eval run on the API pod with `RULECAM_MODE=record` stores every model reply. `capture`
freezes that run into a fixture folder (`scenarios/<name>/`): the eval run record, the approved
plan, the day's evidence chain, the track windows the rules engine read, the Glimmer strips and
the recorded replies those strips were sent with. The golden steps (`steps`) re-derive every
step of the run from those files with no network and no model cost, and `export` turns the
verified outputs into the web UI's REPLAY demo (`ui/src/fixtures/golden/`).
"""
