# Agent rules

- Tests verify skills through structure, identifiers, and script behavior, so skill prose stays free to reword.
- Skill prose states the wanted action, adds one compact example only to settle an ambiguity, and uses the fewest tokens per behavior.
- Run the suite with `python3 -m pytest` after `python3 -m pip install -r requirements-dev.txt`. It runs in parallel by default, and the networked CLI suites stay skipped unless `EXPSKILL_RUN_NETWORK_TESTS=1` is set.
