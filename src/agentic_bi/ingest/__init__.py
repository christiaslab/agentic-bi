"""Ingest: the pinned USAspending snapshot and the same-day golden values (plan row S1).

``spec`` holds the slice filter and the column allow-list, ``client`` a standard-library
client limited to the USAspending hosts, ``fetch`` the snapshot and ``golden`` the
golden values. See ADR-0001.
"""
