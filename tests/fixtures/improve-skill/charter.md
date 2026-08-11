# Disposable sample charter

Improve the `summarize-changes` fixture into an explicit-only skill that turns a supplied diff and validation evidence into release notes for a named audience.

The improved fixture must remain read-only, must not implement or review the change, and must not invent validation results. Its final response must distinguish user-visible changes, compatibility or migration concerns, known risks, and supplied verification evidence. If the audience or source evidence is missing, it must ask one focused question and stop.

This fixture is disposable evaluation input. Never edit a production skill while exercising the improvement workflow.
