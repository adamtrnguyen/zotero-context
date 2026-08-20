"""The read half: what is in the library, and what the GUI is currently showing.

Sources, in the order they are trusted:
  sqlite    items, duplicates, annotations   -- the catalogue itself
  http      bridge (live window state), bbt (citekeys)

Every sqlite read opens the database read-only and REPORTS WHICH MODE SERVED IT
(`items.ZoteroItemStore._connect`): with Zotero running, `mode=ro` intermittently
loses to the rollback journal and `immutable=1` answers from a point-in-time
snapshot instead. A caller that cannot tell those apart cannot know whether it just
read the present or the recent past.
"""
