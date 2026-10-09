"""PulseScore data layer.

The ONLY module in the monorepo that touches the database. Everything that reads
employee/score data goes through the query seam in `data.query`. Auth scope and
audit logging are enforced inside that seam so neither the UI nor a future agent
can bypass them.
"""
