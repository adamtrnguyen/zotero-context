"""Adapters. The only layer allowed to import `mcp`.

  cli         JSON CLI, consumed by Obsidian
  read_mcp    the read tools
  write_mcp   the gated write tools

`mcp` is imported INSIDE `main()`, never at module scope, so that importing a verb does
not require an async runtime. Two contracts and two AST-level tests hold that line.

⚠ This used to add "and so `write/transports/cookjohn.py` stays stdlib-only and vendorable
into Calibre's embedded Python". Nothing is vendored there, and that module is now
`infrastructure/transports/cookjohn.py`. The reason that survives is `dependencies = []`.
"""
